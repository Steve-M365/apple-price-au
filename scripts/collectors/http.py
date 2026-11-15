"""Polite HTTP client for collectors.

Everything a fetch needs lives here: one user agent, a timeout, bounded retries
with jitter, a conditional-request-capable disk cache, and a hard rule that a
refusal is never retried.

The behaviours are deliberate rather than default:

* **Retry only what is retryable.** Connection errors, timeouts, ``5xx`` and a
  ``429`` that carries ``Retry-After``. Nothing else. In particular a ``401`` or
  ``403`` raises :class:`~scripts.collectors.policy.PolicyDenied`: the server has
  answered, and the answer is no.
* **Bounded.** ``HTTP_MAX_RETRIES`` caps attempts and every delay is capped, so a
  dead host cannot stall a run for an unbounded time.
* **Conditional.** A cached response is revalidated with ``If-None-Match`` or
  ``If-Modified-Since``; a ``304`` reuses the cached body instead of transferring
  it again.
* **Cache keyed by URL and user agent.** A change of identity is a change of
  request, so it must not collide with another identity's cache entry.
* **One identity, and it says who we are.** There is no proxy, no rotation and no
  browser emulation here. ``HTTP_USER_AGENT`` must name this project; a browser
  user agent is rejected outright, because presenting one would be a bypass.

Settings are read from the environment at call time, never at import time: an
import must not require configuration, and tests must be able to point the cache
at a temporary directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Final

import httpx

from .policy import DEFAULT_USER_AGENT, PolicyDenied

__all__ = [
    "DEFAULT_MAX_RETRY_AFTER_SECONDS",
    "DEFAULT_MAX_RETRY_DELAY_SECONDS",
    "CacheEntry",
    "DiskCache",
    "HttpClient",
    "HttpError",
    "HttpResponse",
    "HttpSettings",
    "NetworkError",
    "PolicyDenied",
    "RateLimitError",
    "UpstreamError",
    "load_settings",
    "parse_retry_after",
]

DEFAULT_TIMEOUT_SECONDS: Final = 20.0
DEFAULT_MAX_RETRIES: Final = 3
DEFAULT_CACHE_DIR: Final = Path(".cache/http")
DEFAULT_MAX_CONCURRENCY: Final = 2

#: How long a cached response may be served without revalidating, in seconds.
#: Short on purpose: a collector that re-reads the same URL inside one run should
#: not hit the network twice, while a price is never reused across runs.
DEFAULT_CACHE_TTL_SECONDS: Final = 600.0

#: Ceiling on one exponential backoff step, in seconds.
DEFAULT_MAX_RETRY_DELAY_SECONDS: Final = 30.0

#: Ceiling on a server-supplied ``Retry-After`` we are willing to honour, in
#: seconds. Beyond this the run should stop and report rather than sleep.
DEFAULT_MAX_RETRY_AFTER_SECONDS: Final = 120.0


class HttpError(RuntimeError):
    """A fetch failed in a way the caller must record."""


class NetworkError(HttpError):
    """The transport failed after every permitted retry."""


class UpstreamError(HttpError):
    """The server answered ``5xx`` after every permitted retry."""


class RateLimitError(HttpError):
    """The server answered ``429`` and we would not wait as long as it asked."""

    def __init__(self, url: str, retry_after: float | None) -> None:
        super().__init__(
            f"{url} answered HTTP 429"
            + (f" with Retry-After: {retry_after}s" if retry_after is not None else "")
            + "; not retrying"
        )
        self.url = url
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class HttpSettings:
    """Resolved fetch policy. Built from the environment, never from a file."""

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    cache_dir: Path = DEFAULT_CACHE_DIR
    user_agent: str = DEFAULT_USER_AGENT
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS


def _float_env(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number of seconds, got {raw!r}") from exc


def _int_env(env: Mapping[str, str], key: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a whole number, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}, got {value}")
    return value


#: Tokens that only appear in a browser's own user agent. Matching one means the
#: configured identity is an impersonation attempt, which this project forbids.
_BROWSER_IDENTITY: Final = re.compile(r"mozilla|chrome|chromium|safari|firefox|edg/|opera", re.I)


def _checked_user_agent(raw: str) -> str:
    user_agent = raw.strip() or DEFAULT_USER_AGENT
    if _BROWSER_IDENTITY.search(user_agent):
        raise ValueError(
            "HTTP_USER_AGENT must identify this crawler; a browser user agent is an "
            "impersonation and is never permitted"
        )
    return user_agent


def load_settings(env: Mapping[str, str] | None = None) -> HttpSettings:
    """Read fetch policy from the environment, falling back to polite defaults.

    The defaults are the ones published in ``.env.example``; they are intentionally
    the same in CI and locally so a run cannot be made accidentally aggressive.
    """
    source = os.environ if env is None else env
    timeout = _float_env(source, "HTTP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    if timeout <= 0:
        raise ValueError("HTTP_TIMEOUT_SECONDS must be greater than zero")
    user_agent = _checked_user_agent(source.get("HTTP_USER_AGENT", ""))
    cache_dir = Path(source.get("HTTP_CACHE_DIR", "").strip() or DEFAULT_CACHE_DIR)
    return HttpSettings(
        timeout_seconds=timeout,
        max_retries=_int_env(
            source, "HTTP_MAX_RETRIES", DEFAULT_MAX_RETRIES, minimum=0, maximum=10
        ),
        cache_dir=cache_dir,
        user_agent=user_agent,
        max_concurrency=_int_env(
            source,
            "COLLECTOR_MAX_CONCURRENCY",
            DEFAULT_MAX_CONCURRENCY,
            minimum=1,
            maximum=16,
        ),
        cache_ttl_seconds=max(
            0.0, _float_env(source, "HTTP_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS)
        ),
    )


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One cached response, with everything needed to revalidate it."""

    url: str
    user_agent: str
    status: int
    content_type: str
    etag: str | None
    last_modified: str | None
    fetched_at: datetime
    body: str
    extra_headers: Mapping[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "url": self.url,
            "user_agent": self.user_agent,
            "status": self.status,
            "content_type": self.content_type,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "fetched_at": self.fetched_at.isoformat(),
            "body": self.body,
            "extra_headers": dict(self.extra_headers),
        }

    @classmethod
    def from_json(cls, payload: object) -> CacheEntry | None:
        """Rebuild an entry, returning ``None`` for anything malformed."""
        if not isinstance(payload, dict):
            return None
        url = payload.get("url")
        user_agent = payload.get("user_agent")
        status = payload.get("status")
        body = payload.get("body")
        fetched_at = payload.get("fetched_at")
        if not (
            isinstance(url, str)
            and isinstance(user_agent, str)
            and isinstance(status, int)
            and isinstance(body, str)
            and isinstance(fetched_at, str)
        ):
            return None
        try:
            parsed_at = datetime.fromisoformat(fetched_at)
        except ValueError:
            return None
        if parsed_at.tzinfo is None:
            return None
        etag = payload.get("etag")
        last_modified = payload.get("last_modified")
        content_type = payload.get("content_type")
        extra = payload.get("extra_headers")
        return cls(
            url=url,
            user_agent=user_agent,
            status=status,
            content_type=content_type if isinstance(content_type, str) else "",
            etag=etag if isinstance(etag, str) else None,
            last_modified=last_modified if isinstance(last_modified, str) else None,
            fetched_at=parsed_at.astimezone(UTC),
            body=body,
            extra_headers=(
                {str(k): str(v) for k, v in extra.items()} if isinstance(extra, dict) else {}
            ),
        )


class DiskCache:
    """A directory of one-JSON-file-per-response cache entries.

    A missing or corrupt entry is a cache miss, never a crash: the cache is an
    optimisation, and the network remains the source of truth.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path:
        return self._directory

    @staticmethod
    def key(url: str, user_agent: str) -> str:
        digest = hashlib.sha256(f"{url}\0{user_agent}".encode()).hexdigest()
        return digest[:40]

    def path_for(self, url: str, user_agent: str) -> Path:
        return self._directory / f"{self.key(url, user_agent)}.json"

    def read(self, url: str, user_agent: str) -> CacheEntry | None:
        path = self.path_for(url, user_agent)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        entry = CacheEntry.from_json(payload)
        if entry is None or entry.url != url or entry.user_agent != user_agent:
            return None
        return entry

    def write(self, entry: CacheEntry) -> Path:
        path = self.path_for(entry.url, entry.user_agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry.to_json(), ensure_ascii=False), encoding="utf-8")
        return path


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """A response the caller may parse, with its provenance attached."""

    url: str
    status: int
    text: str
    headers: Mapping[str, str]
    fetched_at: datetime
    from_cache: bool = False
    not_modified: bool = False
    cache_path: Path | None = None


def parse_retry_after(value: str | None) -> float | None:
    """Parse ``Retry-After`` as delta-seconds or an HTTP date, capped and non-negative."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    return max(0.0, seconds)


def _cacheable_headers(headers: Mapping[str, str]) -> dict[str, str]:
    keep = ("content-language", "content-type")
    return {key: value for key, value in headers.items() if key.lower() in keep}


class HttpClient:
    """A synchronous, polite HTTP client shared by every adapter.

    The clock, sleeper, random source and cache are injectable so tests are
    deterministic and never sleep or touch the network.
    """

    def __init__(
        self,
        settings: HttpSettings | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        seed: int | None = None,
        cache: DiskCache | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings or load_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper
        self._random = random.Random(seed)
        self._cache = cache or DiskCache(self._settings.cache_dir)
        self._client = httpx.Client(
            timeout=self._settings.timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self._settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-AU,en;q=0.8",
            },
            transport=transport,
        )

    @property
    def settings(self) -> HttpSettings:
        return self._settings

    @property
    def user_agent(self) -> str:
        return self._settings.user_agent

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def backoff_seconds(self, attempt: int) -> float:
        """Jittered, capped exponential backoff for a zero-based retry ``attempt``."""
        base: float = min(2.0 * (2**attempt), DEFAULT_MAX_RETRY_DELAY_SECONDS)
        return base * self._random.uniform(0.5, 1.0)

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        use_cache: bool = True,
    ) -> HttpResponse:
        """Fetch ``url`` politely.

        Raises :class:`~scripts.collectors.policy.PolicyDenied` for ``401``/``403``,
        :class:`RateLimitError` for an unhonourable ``429``, :class:`UpstreamError`
        for a ``5xx`` that survived every retry, and :class:`NetworkError` when the
        transport never succeeded. Other non-2xx responses are returned so the
        adapter can record what happened without guessing.
        """
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"{url!r} is not an absolute http(s) URL")
        user_agent = self._settings.user_agent
        entry = self._cache.read(url, user_agent) if use_cache else None
        if entry is not None and self._settings.cache_ttl_seconds > 0:
            age = (self._clock() - entry.fetched_at).total_seconds()
            if age < self._settings.cache_ttl_seconds:
                return HttpResponse(
                    url=entry.url,
                    status=entry.status,
                    text=entry.body,
                    headers={key.lower(): value for key, value in entry.extra_headers.items()},
                    fetched_at=entry.fetched_at,
                    from_cache=True,
                    cache_path=self._cache.path_for(url, user_agent),
                )
        request_headers = dict(headers or {})
        if entry is not None:
            if entry.etag:
                request_headers["If-None-Match"] = entry.etag
            elif entry.last_modified:
                request_headers["If-Modified-Since"] = entry.last_modified

        attempt = 0
        while True:
            try:
                response = self._client.get(url, headers=request_headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self._settings.max_retries:
                    raise NetworkError(
                        f"{url} failed after {attempt + 1} attempt(s): {exc}"
                    ) from exc
                self._sleeper(self.backoff_seconds(attempt))
                attempt += 1
                continue

            status = response.status_code
            if status in (401, 403):
                raise PolicyDenied(
                    url,
                    f"the source refused automated access (HTTP {status}); no retry, no workaround",
                    status=status,
                )
            if status == 304 and entry is not None:
                return HttpResponse(
                    url=str(response.url),
                    status=status,
                    text=entry.body,
                    headers={
                        **{key.lower(): value for key, value in entry.extra_headers.items()},
                        "etag": entry.etag or "",
                    },
                    fetched_at=entry.fetched_at,
                    from_cache=True,
                    not_modified=True,
                    cache_path=self._cache.path_for(url, user_agent),
                )
            if status == 429:
                retry_after = parse_retry_after(response.headers.get("retry-after"))
                if (
                    retry_after is not None
                    and retry_after <= DEFAULT_MAX_RETRY_AFTER_SECONDS
                    and attempt < self._settings.max_retries
                ):
                    self._sleeper(retry_after)
                    attempt += 1
                    continue
                raise RateLimitError(url, retry_after)
            if 500 <= status <= 599:
                if attempt >= self._settings.max_retries:
                    raise UpstreamError(
                        f"{url} answered HTTP {status} after {attempt + 1} attempt(s)"
                    )
                self._sleeper(self.backoff_seconds(attempt))
                attempt += 1
                continue

            fetched_at = self._clock()
            result_headers = {key.lower(): value for key, value in response.headers.items()}
            cache_path: Path | None = None
            if status == 200 and use_cache:
                cache_path = self._cache.write(
                    CacheEntry(
                        url=url,
                        user_agent=user_agent,
                        status=status,
                        content_type=response.headers.get("content-type", ""),
                        etag=response.headers.get("etag"),
                        last_modified=response.headers.get("last-modified"),
                        fetched_at=fetched_at,
                        body=response.text,
                        extra_headers=_cacheable_headers(response.headers),
                    )
                )
            return HttpResponse(
                url=str(response.url),
                status=status,
                text=response.text,
                headers=result_headers,
                fetched_at=fetched_at,
                from_cache=False,
                not_modified=False,
                cache_path=cache_path,
            )
