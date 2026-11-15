"""Tests for the politeness limits: token bucket, crawl delay and concurrency cap.

The clock and the sleeper are injected everywhere, so the whole file runs in
microseconds and asserts exact gaps rather than "about a second".
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from scripts.collectors.base import (
    BaseAdapter,
    Capabilities,
    CollectionResult,
    ConcurrencyGate,
    RateLimiter,
    SellerNotCollectableError,
    SellerPolicy,
    load_seller_policies,
)
from scripts.collectors.http import DiskCache, HttpClient, HttpSettings
from scripts.collectors.policy import DEFAULT_USER_AGENT, RobotsGate

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)


@dataclass
class VirtualClock:
    """A monotonic clock that only moves when something sleeps."""

    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def limiter_for(
    seller_id: str = "seller", *, rps: float = 1.0, crawl_delay: float = 0.0
) -> tuple[RateLimiter, VirtualClock]:
    clock = VirtualClock()
    limiter = RateLimiter(
        {seller_id: SellerPolicy(seller_id, rate_limit_rps=rps, crawl_delay_seconds=crawl_delay)},
        clock=clock,
        sleeper=clock.sleep,
    )
    return limiter, clock


def test_first_request_is_immediate() -> None:
    limiter, clock = limiter_for(rps=0.25, crawl_delay=3.0)
    assert limiter.acquire("seller") == 0.0
    assert clock.sleeps == []


def test_n_calls_are_spaced_by_the_declared_rate() -> None:
    limiter, clock = limiter_for(rps=2.0)
    waits = [limiter.acquire("seller") for _ in range(5)]
    assert waits == [0.0, 0.5, 0.5, 0.5, 0.5]
    assert clock.now == pytest.approx(2.0)
    assert clock.sleeps == [0.5, 0.5, 0.5, 0.5]


def test_call_count_within_a_window_respects_the_rate() -> None:
    limiter, clock = limiter_for(rps=1.0)
    for _ in range(10):
        limiter.acquire("seller")
    # One immediate call plus nine one-second gaps.
    assert clock.now == pytest.approx(9.0)
    assert len(clock.sleeps) == 9


def test_crawl_delay_is_enforced_between_every_request() -> None:
    limiter, clock = limiter_for(rps=100.0, crawl_delay=2.0)
    waits = [limiter.acquire("seller") for _ in range(3)]
    assert waits == [0.0, 2.0, 2.0]
    assert clock.now == pytest.approx(4.0)


def test_the_slower_of_rate_and_crawl_delay_wins() -> None:
    # One request per second is faster than a three-second crawl delay, so the
    # crawl delay is what the caller feels.
    limiter, clock = limiter_for(rps=1.0, crawl_delay=3.0)
    waits = [limiter.acquire("seller") for _ in range(4)]
    assert waits == [0.0, 3.0, 3.0, 3.0]
    assert clock.now == pytest.approx(9.0)


def test_the_apple_au_budget_is_rate_bound_just_beyond_its_crawl_delay() -> None:
    # 0.33 rps is 3.03 s per token, so at the very first gap the rate loses to
    # nothing except itself: the crawl delay of 3 s is a hair too short.
    limiter, _clock = limiter_for(rps=0.33, crawl_delay=3.0)
    waits = [limiter.acquire("seller") for _ in range(3)]
    assert waits[0] == 0.0
    assert waits[1:] == pytest.approx([1 / 0.33, 1 / 0.33])
    assert all(wait >= 3.0 for wait in waits[1:])


def test_two_sellers_have_independent_buckets() -> None:
    clock = VirtualClock()
    limiter = RateLimiter(
        {
            "slow": SellerPolicy("slow", rate_limit_rps=1.0, crawl_delay_seconds=0.0),
            "fast": SellerPolicy("fast", rate_limit_rps=10.0, crawl_delay_seconds=0.0),
        },
        clock=clock,
        sleeper=clock.sleep,
    )
    assert limiter.acquire("fast") == 0.0
    assert limiter.acquire("slow") == 0.0
    assert limiter.acquire("fast") == pytest.approx(0.1)
    assert limiter.acquire("slow") == pytest.approx(0.9)


def test_delay_for_does_not_consume_a_token() -> None:
    limiter, clock = limiter_for(rps=1.0)
    assert limiter.delay_for("seller") == 0.0
    assert limiter.delay_for("seller") == 0.0
    assert clock.sleeps == []
    limiter.acquire("seller")
    assert limiter.delay_for("seller") == pytest.approx(1.0)
    assert limiter.delay_for("seller") == pytest.approx(1.0)
    assert clock.sleeps == []


def test_curated_seller_is_not_fetchable() -> None:
    limiter = RateLimiter(
        {"curated": SellerPolicy("curated", rate_limit_rps=0.0, crawl_delay_seconds=5.0)},
        clock=lambda: 0.0,
        sleeper=lambda _: None,
    )
    with pytest.raises(SellerNotCollectableError) as caught:
        limiter.acquire("curated")
    assert "curated path" in str(caught.value)


def test_undeclared_seller_has_no_permission() -> None:
    limiter, _ = limiter_for("seller")
    with pytest.raises(SellerNotCollectableError) as caught:
        limiter.acquire("somebody_else")
    assert "no entry in sellers.json" in str(caught.value)


def test_limiter_can_be_built_straight_from_the_registry() -> None:
    limiter = RateLimiter.from_registry(clock=lambda: 0.0, sleeper=lambda _: None)
    assert limiter.policy_for("apple_au").rate_limit_rps == pytest.approx(0.33)
    assert limiter.delay_for("apple_au") == 0.0
    with pytest.raises(SellerNotCollectableError):
        limiter.acquire("jbhifi")


def test_policies_come_from_the_committed_registry() -> None:
    policies = load_seller_policies(REPO_ROOT / "data" / "sellers.json")
    apple = policies["apple_au"]
    assert apple.rate_limit_rps == pytest.approx(0.33)
    assert apple.crawl_delay_seconds == pytest.approx(3.0)
    assert apple.collectable is True
    assert apple.collection_method == "automated_jsonld"
    assert policies["jbhifi"].collectable is False


def test_a_bad_registry_record_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "sellers.json"
    path.write_text('{"sellers": [{"seller_id": "x", "rate_limit_rps": 0}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="crawl_delay_seconds"):
        load_seller_policies(path)


def test_concurrency_gate_defaults_to_two() -> None:
    assert ConcurrencyGate().max_concurrency == 2


def test_concurrency_gate_rejects_a_zero_cap() -> None:
    with pytest.raises(ValueError):
        ConcurrencyGate(0)


def test_concurrency_cap_blocks_the_extra_worker() -> None:
    gate = ConcurrencyGate(2)
    first = gate.slot()
    second = gate.slot()
    first.__enter__()
    second.__enter__()
    assert gate.in_flight == 2

    entered = threading.Event()

    def worker() -> None:
        with gate.slot():
            entered.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    # The third worker must not get in while both slots are held.
    assert entered.wait(0.25) is False
    assert gate.peak == 2
    assert gate.admitted == 2

    first.__exit__(None, None, None)
    assert entered.wait(2.0) is True
    second.__exit__(None, None, None)
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert gate.in_flight == 0
    assert gate.admitted == 3
    assert gate.peak == 2


def test_concurrency_cap_holds_under_a_thread_stampede() -> None:
    gate = ConcurrencyGate(2)
    start = threading.Barrier(6)
    release = threading.Event()
    seen: list[int] = []

    def worker() -> None:
        start.wait()
        with gate.slot():
            seen.append(gate.in_flight)
            release.wait(2.0)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(6)]
    for thread in threads:
        thread.start()
    # Let the first pair run, then open the gate for everyone else.
    release.set()
    for thread in threads:
        thread.join(timeout=5.0)

    assert all(not thread.is_alive() for thread in threads)
    assert gate.admitted == 6
    assert max(seen) <= 2
    assert gate.in_flight == 0


class FetchProbe(BaseAdapter):
    """Adapter that fetches through the base helper, to prove the limiter is used."""

    seller_id = "probe"
    capabilities = Capabilities(product_page=True)

    def policy_urls(self) -> Sequence[str]:
        return ("https://www.example.com.au/robots.txt",)

    def _collect(self, targets: list[Any]) -> CollectionResult:
        for _ in range(3):
            self.fetch("https://www.example.com.au/au/shop/product/widget")
        return CollectionResult(offers=[], fetched_at=NOW)


def test_every_adapter_fetch_passes_through_the_rate_limiter(tmp_path: Path) -> None:
    clock = VirtualClock()
    limiter = RateLimiter(
        {"probe": SellerPolicy("probe", rate_limit_rps=0.5, crawl_delay_seconds=0.0)},
        clock=clock,
        sleeper=clock.sleep,
    )
    settings = HttpSettings(
        cache_dir=tmp_path, user_agent=DEFAULT_USER_AGENT, cache_ttl_seconds=0.0
    )
    adapter = FetchProbe(
        http=HttpClient(settings, sleeper=lambda _: None, cache=DiskCache(tmp_path)),
        gate=RobotsGate(
            fetcher=lambda url: _allowing(url),
            clock=lambda: NOW,
        ),
        limiter=limiter,
        concurrency=ConcurrencyGate(1),
        clock=lambda: NOW,
    )
    with respx.mock(base_url="https://www.example.com.au") as mock:
        route = mock.get("/au/shop/product/widget").mock(
            return_value=httpx.Response(200, text="ok")
        )
        adapter.collect([])
        assert route.call_count == 3
    assert clock.sleeps == [2.0, 2.0]
    assert clock.now == pytest.approx(4.0)


def _allowing(url: str) -> Any:
    from scripts.collectors.policy import RobotsResource

    return RobotsResource(
        url=url, status_code=200, content_type="text/plain", body="", fetched_at=NOW
    )


def test_the_concurrency_cap_is_shared_across_adapters(tmp_path: Path) -> None:
    from scripts.collectors.base import shared_concurrency_gate

    gate = shared_concurrency_gate(2)
    assert gate.max_concurrency == 2
    assert shared_concurrency_gate() is gate
    with pytest.raises(ValueError, match="refusing to change it mid-run"):
        shared_concurrency_gate(5)

    settings = HttpSettings(
        cache_dir=tmp_path, user_agent=DEFAULT_USER_AGENT, cache_ttl_seconds=0.0
    )

    def probe() -> BaseAdapter:
        return BaseProbe(
            http=HttpClient(settings, sleeper=lambda _: None, cache=DiskCache(tmp_path)),
            gate=RobotsGate(fetcher=lambda url: _allowing(url), clock=lambda: NOW),
            limiter=RateLimiter(
                {"probe": SellerPolicy("probe", rate_limit_rps=1.0, crawl_delay_seconds=0.0)},
                clock=lambda: 0.0,
                sleeper=lambda _: None,
            ),
            clock=lambda: NOW,
        )

    first, second = probe(), probe()
    assert first.concurrency is second.concurrency is gate


class BaseProbe(BaseAdapter):
    seller_id = "probe"
    capabilities = Capabilities(product_page=True)

    def policy_urls(self) -> Sequence[str]:
        return ("https://www.example.com.au/robots.txt",)

    def _collect(self, targets: list[Any]) -> CollectionResult:
        return CollectionResult(offers=[], fetched_at=NOW)


def test_slots_are_reserved_even_when_the_sleeper_does_nothing() -> None:
    """Atomic reservation, not the sleep, is what spaces requests.

    With a frozen clock and a no-op sleeper the waits still grow by the crawl
    delay, which is what makes two threads unsafe to double-book the same slot.
    """
    clock = VirtualClock()
    limiter = RateLimiter(
        {"seller": SellerPolicy("seller", rate_limit_rps=1.0, crawl_delay_seconds=3.0)},
        clock=lambda: 0.0,
        sleeper=lambda _: None,
    )
    waits = [limiter.acquire("seller") for _ in range(3)]
    assert waits == [0.0, 3.0, 6.0]
    assert limiter.delay_for("seller") == 9.0
    assert clock.sleeps == []


def test_robots_crawl_delay_can_only_tighten_the_declared_delay() -> None:
    limiter, _clock = limiter_for(rps=2.0, crawl_delay=1.0)
    limiter.tighten_crawl_delay("seller", 5.0)
    assert limiter.policy_for("seller").crawl_delay_seconds == 5.0
    limiter.tighten_crawl_delay("seller", 2.0)
    assert limiter.policy_for("seller").crawl_delay_seconds == 5.0
    limiter.tighten_crawl_delay("seller", None)
    assert limiter.policy_for("seller").crawl_delay_seconds == 5.0


def test_check_policy_folds_the_robots_delay_into_the_limiter(tmp_path: Path) -> None:
    gate = RobotsGate(
        fetcher=lambda url: _allowing_body(url, "User-agent: *\nCrawl-delay: 9\n"),
        clock=lambda: NOW,
    )
    limiter, _clock = limiter_for("probe", rps=1.0, crawl_delay=1.0)
    adapter = BaseProbe(
        http=HttpClient(
            HttpSettings(cache_dir=tmp_path, user_agent=DEFAULT_USER_AGENT),
            sleeper=lambda _: None,
            cache=DiskCache(tmp_path),
        ),
        gate=gate,
        limiter=limiter,
        clock=lambda: NOW,
    )
    assert adapter.check_policy().crawl_delay_seconds == 9.0
    assert limiter.policy_for("probe").crawl_delay_seconds == 9.0


def _allowing_body(url: str, body: str) -> Any:
    from scripts.collectors.policy import RobotsResource

    return RobotsResource(
        url=url, status_code=200, content_type="text/plain", body=body, fetched_at=NOW
    )
