"""The collection contract every source adapter implements.

An adapter is a *policy-bound* fetcher. It is not allowed to reach the network
until :meth:`SourceAdapter.check_policy` has returned an all-clear, and it is not
allowed to move faster than the seller's token bucket. Both constraints live in
this module rather than in each adapter, so a new adapter cannot forget them.

The vocabulary is the important part:

* :class:`BlockedSource` — a **decision**. The seller refused, or the policy gate
  refused for it. The run stops for that seller, nothing is retried, and the
  curated feed takes over.
* :class:`CollectionError` — a **fault**. A timeout, a stale markup shape, a
  missing field. It is recorded and the run continues.

Conflating the two is how a crawler ends up hammering a site that has already
said no, so they are separate types and separate lists.
"""

from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Final, Protocol, runtime_checkable

from scripts.models import Offer, Product

from .http import DEFAULT_MAX_CONCURRENCY, HttpClient, HttpResponse, load_settings
from .policy import PolicyDecision, PolicyDenied, RobotsGate

__all__ = [
    "SELLERS_REGISTRY",
    "BaseAdapter",
    "BlockedSource",
    "Capabilities",
    "CollectionError",
    "CollectionResult",
    "ConcurrencyGate",
    "PolicyResult",
    "RateLimiter",
    "SellerNotCollectableError",
    "SellerPolicy",
    "SourceAdapter",
    "load_seller_policies",
    "shared_concurrency_gate",
    "utc_now",
]

#: Global ceiling on simultaneous in-flight requests lives with the rest of the
#: fetch policy, in :mod:`scripts.collectors.http`.

#: The committed seller registry, resolved from this file rather than the current
#: working directory so a run cannot pick up the wrong registry.
SELLERS_REGISTRY: Final = Path(__file__).resolve().parents[2] / "data" / "sellers.json"


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What an adapter can actually do, declared rather than discovered.

    ``api`` means a documented, sanctioned interface; it does not mean "an
    undocumented endpoint that happens to return JSON".
    """

    search: bool = False
    listing: bool = False
    product_page: bool = False
    stock: bool = False
    api: bool = False


@dataclass(frozen=True, slots=True)
class BlockedSource:
    """One seller that will not be collected automatically. A decision, not a bug."""

    seller_id: str
    reason: str
    evidence_url: str
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class CollectionError:
    """One recoverable failure during a run. The run continues."""

    message: str
    error_type: str
    product_id: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """What one adapter produced, including why it produced nothing."""

    offers: list[Offer] = field(default_factory=list)
    blocked: list[BlockedSource] = field(default_factory=list)
    errors: list[CollectionError] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=utc_now)
    raw_snapshot_path: Path | None = None

    @property
    def blocked_by_policy(self) -> bool:
        """True when the seller was refused by policy rather than by a fault."""
        return bool(self.blocked)


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """The full set of policy decisions an adapter made about its targets.

    An empty decision list is *not* a pass: nothing was proved, so nothing may be
    fetched.
    """

    seller_id: str
    decisions: tuple[PolicyDecision, ...]

    @property
    def allowed(self) -> bool:
        return bool(self.decisions) and all(decision.allowed for decision in self.decisions)

    @property
    def denials(self) -> tuple[PolicyDecision, ...]:
        return tuple(decision for decision in self.decisions if not decision.allowed)

    @property
    def reason(self) -> str:
        if not self.decisions:
            return "no policy URL was checked; absence of a check is not permission"
        denials = self.denials
        if denials:
            return "; ".join(decision.reason for decision in denials)
        return "; ".join(decision.reason for decision in self.decisions)

    @property
    def evidence_url(self) -> str:
        if not self.decisions:
            return ""
        return self.decisions[0].robots_url

    @property
    def checked_at(self) -> datetime:
        if not self.decisions:
            return utc_now()
        return max(decision.fetched_at for decision in self.decisions)

    @property
    def crawl_delay_seconds(self) -> float | None:
        delays = [
            decision.crawl_delay_seconds
            for decision in self.decisions
            if decision.crawl_delay_seconds is not None
        ]
        return max(delays) if delays else None

    def blocked_source(self) -> BlockedSource | None:
        """Return the decision record when the result is a denial, else ``None``."""
        if self.allowed:
            return None
        return BlockedSource(
            seller_id=self.seller_id,
            reason=self.reason,
            evidence_url=self.evidence_url,
            checked_at=self.checked_at,
        )

    def to_json(self) -> dict[str, object]:
        return {
            "seller_id": self.seller_id,
            "allowed": self.allowed,
            "reason": self.reason,
            "robots_url": self.evidence_url,
            "checked_at": self.checked_at.isoformat(),
            "crawl_delay_seconds": self.crawl_delay_seconds,
            "decisions": [
                {
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                    "robots_url": decision.robots_url,
                    "status": decision.status,
                    "matched_rule": decision.matched_rule,
                }
                for decision in self.decisions
            ],
        }


@dataclass(frozen=True, slots=True)
class SellerPolicy:
    """The politeness budget declared for one seller in ``data/sellers.json``."""

    seller_id: str
    rate_limit_rps: float
    crawl_delay_seconds: float
    collection_method: str = ""

    @property
    def collectable(self) -> bool:
        return self.rate_limit_rps > 0


class SellerNotCollectableError(RuntimeError):
    """Raised when something asks to fetch a seller that is curated or manual.

    A zero rate limit is not "unlimited"; it means this seller is not collected
    automatically at all.
    """


def load_seller_policies(path: Path | str) -> dict[str, SellerPolicy]:
    """Load per-seller politeness budgets from ``data/sellers.json``.

    The registry is the single source of truth for rate limits, so adapters read
    it rather than hard-coding a number that can drift from the declaration.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("sellers.json must contain an object")
    records = payload.get("sellers")
    if not isinstance(records, list):
        raise ValueError("sellers.json must contain a 'sellers' list")
    policies: dict[str, SellerPolicy] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("every sellers.json record must be an object")
        seller_id = record.get("seller_id")
        rate = record.get("rate_limit_rps")
        delay = record.get("crawl_delay_seconds")
        method = record.get("collection_method")
        if not isinstance(seller_id, str) or not seller_id:
            raise ValueError("every sellers.json record needs a seller_id")
        if not isinstance(rate, (int, float)) or isinstance(rate, bool):
            raise ValueError(f"{seller_id}: rate_limit_rps must be a number")
        if not isinstance(delay, (int, float)) or isinstance(delay, bool):
            raise ValueError(f"{seller_id}: crawl_delay_seconds must be a number")
        policies[seller_id] = SellerPolicy(
            seller_id=seller_id,
            rate_limit_rps=float(rate),
            crawl_delay_seconds=float(delay),
            collection_method=method if isinstance(method, str) else "",
        )
    return policies


@dataclass(slots=True)
class _Bucket:
    tokens: float
    last_refill: float
    last_request: float | None = None


class RateLimiter:
    """Token bucket plus mandatory crawl delay, per seller, on an injectable clock.

    :meth:`acquire` *reserves* a slot under a lock and only then sleeps, so two
    threads asking for the same seller can never be granted the same instant, and
    two threads asking about different sellers still overlap. The clock and the
    sleeper are injected, so a test can replay an hour in microseconds and assert
    the exact gaps.
    """

    def __init__(
        self,
        policies: Mapping[str, SellerPolicy],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        burst: float = 1.0,
    ) -> None:
        if burst < 1.0:
            raise ValueError("burst must be at least one request")
        self._policies = dict(policies)
        self._clock = clock
        self._sleeper = sleeper
        self._burst = burst
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_registry(
        cls,
        path: Path | str = SELLERS_REGISTRY,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        burst: float = 1.0,
    ) -> RateLimiter:
        """Build a limiter from ``data/sellers.json``, the declared budgets."""
        return cls(load_seller_policies(path), clock=clock, sleeper=sleeper, burst=burst)

    def tighten_crawl_delay(self, seller_id: str, seconds: float | None) -> None:
        """Raise this seller's crawl delay to at least ``seconds``.

        robots.txt may ask for a slower pace than ``data/sellers.json`` declares.
        The stricter of the two always wins, so this method can only tighten: it
        refuses to lower a declared delay, and there is no API to loosen one.
        """
        if seconds is None or seconds <= 0:
            return
        with self._lock:
            policy = self.policy_for(seller_id)
            if seconds > policy.crawl_delay_seconds:
                self._policies[seller_id] = replace(policy, crawl_delay_seconds=seconds)

    def policy_for(self, seller_id: str) -> SellerPolicy:
        try:
            return self._policies[seller_id]
        except KeyError as exc:
            raise SellerNotCollectableError(
                f"{seller_id} has no entry in sellers.json; an undeclared seller "
                "has no permission to be fetched"
            ) from exc

    def _bucket(self, seller_id: str) -> tuple[SellerPolicy, _Bucket]:
        policy = self.policy_for(seller_id)
        if not policy.collectable:
            raise SellerNotCollectableError(
                f"{seller_id} declares rate_limit_rps=0 "
                f"(collection_method={policy.collection_method or 'unknown'}); "
                "it is collected through the curated path, not by a fetcher"
            )
        bucket = self._buckets.get(seller_id)
        if bucket is None:
            bucket = _Bucket(tokens=self._burst, last_refill=self._clock())
            self._buckets[seller_id] = bucket
        return policy, bucket

    def _ready_at(self, policy: SellerPolicy, bucket: _Bucket, now: float) -> float:
        ready = now
        if policy.crawl_delay_seconds > 0 and bucket.last_request is not None:
            ready = max(ready, bucket.last_request + policy.crawl_delay_seconds)
        tokens = min(
            self._burst,
            bucket.tokens + (now - bucket.last_refill) * policy.rate_limit_rps,
        )
        if tokens < 1.0:
            ready = max(ready, now + (1.0 - tokens) / policy.rate_limit_rps)
        return ready

    def delay_for(self, seller_id: str) -> float:
        """Seconds the next :meth:`acquire` would wait. Does not mutate state."""
        with self._lock:
            policy, bucket = self._bucket(seller_id)
            now = self._clock()
            return max(0.0, self._ready_at(policy, bucket, now) - now)

    def acquire(self, seller_id: str) -> float:
        """Reserve this seller's next slot, wait for it, and return the wait."""
        with self._lock:
            policy, bucket = self._bucket(seller_id)
            now = self._clock()
            start = self._ready_at(policy, bucket, now)
            tokens = min(
                self._burst,
                bucket.tokens + (start - bucket.last_refill) * policy.rate_limit_rps,
            )
            bucket.tokens = max(0.0, tokens - 1.0)
            bucket.last_refill = start
            bucket.last_request = start
        wait = max(0.0, start - now)
        if wait > 0:
            self._sleeper(wait)
        return wait


class ConcurrencyGate:
    """A bounded permit for in-flight work, with a high-water mark for tests.

    The global cap exists so that a run with many adapters never turns into a
    burst of simultaneous requests. It is a semaphore, not a queue: the caller
    blocks until a slot is free.
    """

    def __init__(self, max_concurrency: int = DEFAULT_MAX_CONCURRENCY) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self._max = max_concurrency
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._peak = 0
        self._admitted = 0

    @property
    def max_concurrency(self) -> int:
        return self._max

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    @property
    def peak(self) -> int:
        with self._lock:
            return self._peak

    @property
    def admitted(self) -> int:
        """Total successful admissions; proves every caller got through."""
        with self._lock:
            return self._admitted

    @contextmanager
    def slot(self) -> Iterator[None]:
        with self._semaphore:
            with self._lock:
                self._in_flight += 1
                self._admitted += 1
                self._peak = max(self._peak, self._in_flight)
            try:
                yield
            finally:
                with self._lock:
                    self._in_flight -= 1


_GATE_LOCK = threading.Lock()
_SHARED_GATE: ConcurrencyGate | None = None


def shared_concurrency_gate(max_concurrency: int | None = None) -> ConcurrencyGate:
    """Return the process-wide concurrency gate.

    One run, one cap. Every adapter in a run must share this gate, otherwise
    ``COLLECTOR_MAX_CONCURRENCY`` would be a per-adapter limit and a run with
    twenty sellers could make forty simultaneous requests. The cap is read from
    the environment on first use, never at import time.
    """
    global _SHARED_GATE
    with _GATE_LOCK:
        if _SHARED_GATE is None:
            cap = (
                max_concurrency if max_concurrency is not None else load_settings().max_concurrency
            )
            _SHARED_GATE = ConcurrencyGate(cap)
        elif max_concurrency is not None and max_concurrency != _SHARED_GATE.max_concurrency:
            raise ValueError(
                f"the shared concurrency gate is already capped at "
                f"{_SHARED_GATE.max_concurrency}; refusing to change it mid-run"
            )
        return _SHARED_GATE


@runtime_checkable
class SourceAdapter(Protocol):
    """The contract an adapter satisfies.

    ``check_policy`` is not advisory. ``collect`` must call it before any fetch
    and must return a :class:`BlockedSource` rather than raising when the answer
    is no.
    """

    seller_id: str
    capabilities: Capabilities

    def check_policy(self) -> PolicyResult: ...

    def collect(self, targets: list[Product]) -> CollectionResult: ...


class BaseAdapter(ABC):
    """Concrete base that enforces the gate, the rate limit and the concurrency cap.

    Subclasses declare ``seller_id``, ``capabilities``, the URLs whose policy must
    be checked, and a ``_collect`` that is only ever reached when the gate passes.
    """

    seller_id: ClassVar[str]
    capabilities: ClassVar[Capabilities] = Capabilities()

    def __init__(
        self,
        *,
        http: HttpClient,
        gate: RobotsGate,
        limiter: RateLimiter,
        concurrency: ConcurrencyGate | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if http.user_agent != gate.user_agent:
            raise ValueError(
                "the policy gate and the HTTP client must present the same user agent; "
                "checking one identity while sending another is a bypass"
            )
        self._http = http
        self._gate = gate
        self._limiter = limiter
        self._concurrency = concurrency or shared_concurrency_gate()
        self._clock = clock

    @property
    def http(self) -> HttpClient:
        return self._http

    @property
    def concurrency(self) -> ConcurrencyGate:
        """The gate this adapter fetches through; share one across an entire run."""
        return self._concurrency

    def policy_urls(self) -> Sequence[str]:
        """Representative URLs the gate must clear before this adapter fetches.

        Empty by default, which can never pass: an adapter that does not say what
        it intends to fetch has not been authorised to fetch anything.
        """
        return ()

    def check_policy(self) -> PolicyResult:
        """Check robots.txt for every representative URL. Cached per origin.

        A ``Crawl-delay`` the site asks for is folded into the rate limiter here, so
        the politeness budget is never looser than the published policy.
        """
        decisions = tuple(self._gate.is_allowed(url) for url in self.policy_urls())
        result = PolicyResult(seller_id=self.seller_id, decisions=decisions)
        self._limiter.tighten_crawl_delay(self.seller_id, result.crawl_delay_seconds)
        return result

    def fetch(self, url: str) -> HttpResponse:
        """Rate-limited, concurrency-capped GET. Call only from ``_collect``."""
        self._limiter.acquire(self.seller_id)
        with self._concurrency.slot():
            return self._http.get(url)

    def collect(self, targets: list[Product]) -> CollectionResult:
        """Run the gate, then (and only then) the collector.

        A denial returns a ``BlockedSource`` with no offers and no errors. A
        ``PolicyDenied`` raised mid-run by the HTTP client is converted the same
        way, so a ``403`` part-way through cannot be retried or worked around.
        """
        started = self._clock()
        policy = self.check_policy()
        blocked = policy.blocked_source()
        if blocked is not None:
            return CollectionResult(offers=[], blocked=[blocked], errors=[], fetched_at=started)
        try:
            return self._collect(targets)
        except PolicyDenied as exc:
            return CollectionResult(
                offers=[],
                blocked=[
                    BlockedSource(
                        seller_id=self.seller_id,
                        reason=f"{exc.reason}; collection stopped for this seller",
                        evidence_url=exc.url,
                        checked_at=self._clock(),
                    )
                ],
                errors=[],
                fetched_at=self._clock(),
            )

    @abstractmethod
    def _collect(self, targets: list[Product]) -> CollectionResult:
        """Fetch and parse. Reached only when the policy gate has passed."""
