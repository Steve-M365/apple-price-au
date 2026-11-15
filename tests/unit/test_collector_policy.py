"""Tests for the robots.txt gate, the HTTP client and the adapter short-circuit.

Every test here runs with the network off: robots.txt bodies are injected and
HTTP responses come from ``respx``. The gate's failure and absence rules are the
point of the module, so they are pinned individually.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
    PolicyResult,
    RateLimiter,
    SellerPolicy,
)
from scripts.collectors.http import (
    DiskCache,
    HttpClient,
    HttpSettings,
    NetworkError,
    PolicyDenied,
    RateLimitError,
    UpstreamError,
)
from scripts.collectors.policy import (
    DEFAULT_USER_AGENT,
    RobotsDenied,
    RobotsGate,
    RobotsResource,
    build_finding,
    parse_robots,
)

NOW = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)
UA = "OrchardPriceBot/1.0 (+https://github.com/Steve-M365/apple-price-au)"
PRODUCT_URL = "https://www.example.com.au/au/shop/product/widget"

ROBOTS = """\
User-agent: *
Disallow: /private/
Allow: /private/public/
Crawl-delay: 7

User-agent: OrchardPriceBot
Disallow: /au/shop/checkout
"""


@dataclass
class FakeFetcher:
    """A robots.txt fetcher that serves injected bodies and records calls."""

    body: str = ""
    status: int = 200
    content_type: str = "text/plain"
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def __call__(self, url: str) -> RobotsResource:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return RobotsResource(
            url=url,
            status_code=self.status,
            content_type=self.content_type,
            body=self.body,
            fetched_at=NOW,
        )


def gate_for(
    body: str = "", *, status: int = 200, error: Exception | None = None
) -> tuple[RobotsGate, FakeFetcher]:
    fetcher = FakeFetcher(body=body, status=status, error=error)
    gate = RobotsGate(fetcher=fetcher, clock=lambda: NOW)
    return gate, fetcher


# --------------------------------------------------------------------------
# robots.txt: parsing and precedence
# --------------------------------------------------------------------------


def test_parse_groups_rules_and_crawl_delay() -> None:
    groups = parse_robots(ROBOTS)
    assert [group.agents for group in groups] == [("*",), ("OrchardPriceBot",)]
    assert [(rule.allow, rule.pattern) for rule in groups[0].rules] == [
        (False, "/private/"),
        (True, "/private/public/"),
    ]
    assert groups[0].crawl_delay_seconds == 7.0
    assert groups[1].crawl_delay_seconds is None


def test_robots_with_no_rules_allows_everything() -> None:
    gate, _ = gate_for("# nothing here\n")
    decision = gate.is_allowed(PRODUCT_URL)
    assert decision.allowed is True
    assert decision.status == 200
    assert decision.crawl_delay_seconds is None
    assert "unrestricted" in decision.reason


def test_minor_disallow_blocks_only_the_matching_path() -> None:
    gate, _ = gate_for("User-agent: *\nDisallow: /private/\n")
    assert gate.is_allowed("https://www.example.com.au/private/x").allowed is False
    assert gate.is_allowed(PRODUCT_URL).allowed is True


def test_longest_match_allow_overrides_disallow() -> None:
    gate, _ = gate_for(ROBOTS)
    wildcard_agent = "SomeOtherBot/2.0"
    allowed = gate.is_allowed("https://www.example.com.au/private/public/deed.pdf", wildcard_agent)
    assert allowed.allowed is True
    assert allowed.matched_rule == "Allow: /private/public/"
    disallowed = gate.is_allowed("https://www.example.com.au/private/deed.pdf", wildcard_agent)
    assert disallowed.allowed is False
    assert disallowed.matched_rule == "Disallow: /private/"


def test_a_specific_group_is_not_merged_with_the_wildcard_group() -> None:
    gate, _ = gate_for(ROBOTS)
    # The group naming this UA carries only the /au/shop/checkout rule, so a path
    # the wildcard group disallows is allowed for us: a specific group replaces '*'.
    assert gate.is_allowed("https://www.example.com.au/private/deed.pdf", UA).allowed is True
    assert gate.is_allowed(PRODUCT_URL, UA).allowed is True


def test_longest_match_within_the_wildcard_group() -> None:
    gate, _ = gate_for(ROBOTS)
    wildcard_agent = "SomeOtherBot/2.0"
    decision = gate.is_allowed("https://www.example.com.au/private/deed.pdf", wildcard_agent)
    assert decision.allowed is False
    assert decision.matched_rule == "Disallow: /private/"
    assert "most specific match" in decision.reason


def test_equal_length_allow_beats_disallow() -> None:
    gate, _ = gate_for("User-agent: *\nDisallow: /au/shop\nAllow: /au/shop\n")
    decision = gate.is_allowed("https://www.example.com.au/au/shop/product/widget")
    assert decision.allowed is True
    assert decision.matched_rule == "Allow: /au/shop"


def test_user_agent_specific_group_beats_wildcard() -> None:
    gate, _ = gate_for(ROBOTS)
    specific = gate.is_allowed("https://www.example.com.au/au/shop/checkout", UA)
    assert specific.allowed is False
    assert specific.matched_rule == "Disallow: /au/shop/checkout"
    assert specific.reason.find("most specific match") >= 0
    # The same path is allowed for an agent that only matches '*'.
    assert gate.is_allowed("https://www.example.com.au/au/shop/checkout", "OtherBot/1.0").allowed


def test_crawl_delay_is_reported_for_the_governing_group() -> None:
    gate, _ = gate_for(ROBOTS)
    wildcard = gate.is_allowed(PRODUCT_URL, "OtherBot/1.0")
    assert wildcard.crawl_delay_seconds == 7.0
    assert gate.is_allowed(PRODUCT_URL, UA).crawl_delay_seconds is None


def test_empty_disallow_value_is_not_a_restriction() -> None:
    gate, _ = gate_for("User-agent: *\nDisallow:\n")
    assert gate.is_allowed(PRODUCT_URL).allowed is True


def test_query_string_participates_in_matching() -> None:
    gate, _ = gate_for("User-agent: *\nDisallow: /*?\n")
    assert gate.is_allowed("https://www.example.com.au/search?q=apple").allowed is False
    assert gate.is_allowed("https://www.example.com.au/search").allowed is True


def test_dollar_anchor_is_honoured() -> None:
    gate, _ = gate_for("User-agent: *\nDisallow: /*.pdf$\n")
    assert gate.is_allowed("https://www.example.com.au/a/deed.pdf").allowed is False
    assert gate.is_allowed("https://www.example.com.au/a/deed.pdf.html").allowed is True


# --------------------------------------------------------------------------
# robots.txt: absence and unreadability
# --------------------------------------------------------------------------


def test_missing_robots_txt_is_allow_all() -> None:
    gate, fetcher = gate_for("", status=404)
    decision = gate.is_allowed(PRODUCT_URL)
    assert decision.allowed is True
    assert decision.status == 404
    assert "RFC 9309" in decision.reason
    assert fetcher.calls == ["https://www.example.com.au/robots.txt"]


def test_timeout_on_robots_is_a_denial() -> None:
    gate, _ = gate_for(error=httpx.ConnectTimeout("timed out"))
    decision = gate.is_allowed(PRODUCT_URL)
    assert decision.allowed is False
    assert decision.status is None
    assert "unreadable policy is not permission" in decision.reason


def test_server_error_on_robots_is_a_denial() -> None:
    gate, _ = gate_for("upstream is down", status=500)
    decision = gate.is_allowed(PRODUCT_URL)
    assert decision.allowed is False
    assert decision.status == 500
    assert "an unreadable policy is not permission" in decision.reason


def test_forbidden_robots_is_a_denial_not_a_pass() -> None:
    gate, _ = gate_for("", status=403)
    assert gate.is_allowed(PRODUCT_URL).allowed is False


@pytest.mark.parametrize("status", [429, 401, 503])
def test_every_unreadable_status_denies(status: int) -> None:
    gate, _ = gate_for("", status=status)
    assert gate.is_allowed(PRODUCT_URL).allowed is False


def test_robots_is_cached_per_origin() -> None:
    gate, fetcher = gate_for(ROBOTS)
    first = gate.is_allowed(PRODUCT_URL)
    second = gate.is_allowed("https://www.example.com.au/au/shop/buy-mac/macbook-air")
    assert fetcher.calls == ["https://www.example.com.au/robots.txt"]
    assert first.fetched_at == second.fetched_at
    gate.is_allowed("https://other.example.com.au/thing")
    assert len(fetcher.calls) == 2


def test_robots_denied_is_the_same_decision_class_as_policy_denied() -> None:
    assert RobotsDenied is PolicyDenied
    with pytest.raises(PolicyDenied) as caught:
        raise RobotsDenied(PRODUCT_URL, "robots.txt disallows this path", status=200)
    assert caught.value.status == 200
    assert caught.value.url == PRODUCT_URL


def test_build_finding_records_the_denial_and_the_delay(tmp_path: Path) -> None:
    gate, _ = gate_for("User-agent: *\nDisallow: /au/shop/product/\nCrawl-delay: 7\n")
    decisions = [
        gate.is_allowed(PRODUCT_URL),
        gate.is_allowed("https://www.example.com.au/au/shop/buy-mac/macbook-air"),
    ]
    finding = build_finding("example", decisions, evidence_file="fixtures/policy/example.txt")
    assert finding["allowed"] is False
    assert finding["crawl_delay_seconds"] == 7.0
    assert finding["status"] == 200
    assert finding["evidence_file"] == "fixtures/policy/example.txt"
    assert isinstance(finding["checked_at"], str) and finding["checked_at"].endswith("Z")
    assert "DENIED" in str(finding["notes"])


# --------------------------------------------------------------------------
# HTTP client: retries, policy refusal and caching
# --------------------------------------------------------------------------


@dataclass
class Clock:
    """A frozen clock that only moves when a test moves it."""

    at: datetime = NOW

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at = self.at + timedelta(seconds=seconds)


def http_client(
    tmp_path: Path,
    *,
    max_retries: int = 3,
    cache_ttl_seconds: float = 0.0,
    clock: Clock | None = None,
) -> tuple[HttpClient, list[float]]:
    delays: list[float] = []
    settings = HttpSettings(
        timeout_seconds=1.0,
        max_retries=max_retries,
        cache_dir=tmp_path,
        user_agent=UA,
        max_concurrency=2,
        cache_ttl_seconds=cache_ttl_seconds,
    )
    client = HttpClient(
        settings,
        clock=clock or Clock(),
        sleeper=delays.append,
        seed=7,
        cache=DiskCache(tmp_path),
    )
    return client, delays


def test_retries_on_503_then_succeeds(tmp_path: Path) -> None:
    client, delays = http_client(tmp_path)
    with respx.mock(base_url="https://www.example.com.au") as mock:
        route = mock.get("/au/shop/product/widget").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, text="<html>ok</html>", headers={"etag": '"v1"'}),
            ]
        )
        response = client.get(PRODUCT_URL)
        assert response.status == 200
        assert response.text == "<html>ok</html>"
        assert route.call_count == 2
    assert len(delays) == 1
    assert 0 < delays[0] <= 30.0


def test_retries_are_bounded(tmp_path: Path) -> None:
    client, delays = http_client(tmp_path, max_retries=2)
    with respx.mock(base_url="https://www.example.com.au") as mock:
        route = mock.get("/au/shop/product/widget").mock(return_value=httpx.Response(500))
        with pytest.raises(UpstreamError):
            client.get(PRODUCT_URL)
        assert route.call_count == 3  # the first attempt plus two retries
    assert len(delays) == 2


def test_network_error_is_bounded_too(tmp_path: Path) -> None:
    client, delays = http_client(tmp_path, max_retries=1)
    with respx.mock(base_url="https://www.example.com.au") as mock:
        route = mock.get("/au/shop/product/widget").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(NetworkError):
            client.get(PRODUCT_URL)
        assert route.call_count == 2
    assert len(delays) == 1


def test_forbidden_is_never_retried(tmp_path: Path) -> None:
    client, delays = http_client(tmp_path, max_retries=5)
    with respx.mock(base_url="https://www.example.com.au") as mock:
        route = mock.get("/au/shop/product/widget").mock(return_value=httpx.Response(403))
        with pytest.raises(PolicyDenied) as caught:
            client.get(PRODUCT_URL)
        assert route.call_count == 1
    assert caught.value.status == 403
    assert "no retry" in caught.value.reason
    assert delays == []


def test_unauthorised_is_never_retried(tmp_path: Path) -> None:
    client, _ = http_client(tmp_path, max_retries=5)
    with respx.mock(base_url="https://www.example.com.au") as mock:
        route = mock.get("/au/shop/product/widget").mock(return_value=httpx.Response(401))
        with pytest.raises(PolicyDenied):
            client.get(PRODUCT_URL)
        assert route.call_count == 1


def test_429_with_retry_after_is_honoured(tmp_path: Path) -> None:
    client, delays = http_client(tmp_path)
    with respx.mock(base_url="https://www.example.com.au") as mock:
        mock.get("/au/shop/product/widget").mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "11"}),
                httpx.Response(200, text="ok"),
            ]
        )
        assert client.get(PRODUCT_URL).status == 200
    assert delays == [11.0]


def test_429_without_retry_after_stops_the_run(tmp_path: Path) -> None:
    client, delays = http_client(tmp_path)
    with respx.mock(base_url="https://www.example.com.au") as mock:
        route = mock.get("/au/shop/product/widget").mock(return_value=httpx.Response(429))
        with pytest.raises(RateLimitError):
            client.get(PRODUCT_URL)
        assert route.call_count == 1
    assert delays == []


def test_404_is_returned_for_the_adapter_to_record(tmp_path: Path) -> None:
    client, _ = http_client(tmp_path)
    with respx.mock(base_url="https://www.example.com.au") as mock:
        mock.get("/au/shop/product/widget").mock(return_value=httpx.Response(404, text="gone"))
        response = client.get(PRODUCT_URL)
    assert response.status == 404
    assert response.text == "gone"


def test_cache_hit_avoids_a_second_request(tmp_path: Path) -> None:
    client, _ = http_client(tmp_path, cache_ttl_seconds=600.0)
    with respx.mock(base_url="https://www.example.com.au") as mock:
        route = mock.get("/au/shop/product/widget").mock(
            return_value=httpx.Response(200, text="cached body")
        )
        first = client.get(PRODUCT_URL)
        second = client.get(PRODUCT_URL)
        assert route.call_count == 1
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.text == "cached body"


def test_conditional_request_sends_if_none_match_and_handles_304(tmp_path: Path) -> None:
    client, _ = http_client(tmp_path, cache_ttl_seconds=0.0)
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        if len(seen) == 1:
            return httpx.Response(200, text="first body", headers={"etag": '"v1"'})
        return httpx.Response(304)

    with respx.mock(base_url="https://www.example.com.au") as mock:
        mock.get("/au/shop/product/widget").mock(side_effect=handler)
        first = client.get(PRODUCT_URL)
        second = client.get(PRODUCT_URL)
        assert mock.routes[0].call_count == 2

    assert first.text == "first body"
    assert seen[0].get("if-none-match") is None
    assert seen[1].get("if-none-match") == '"v1"'
    assert second.status == 304
    assert second.text == "first body"  # the body came from the cache
    assert second.from_cache is True
    assert second.not_modified is True


def test_conditional_request_uses_last_modified_when_there_is_no_etag(tmp_path: Path) -> None:
    client, _ = http_client(tmp_path, cache_ttl_seconds=0.0)
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        if len(seen) == 1:
            return httpx.Response(
                200, text="body", headers={"last-modified": "Sat, 14 Nov 2026 23:00:00 GMT"}
            )
        return httpx.Response(304)

    with respx.mock(base_url="https://www.example.com.au") as mock:
        mock.get("/au/shop/product/widget").mock(side_effect=handler)
        client.get(PRODUCT_URL)
        client.get(PRODUCT_URL)

    assert seen[1].get("if-modified-since") == "Sat, 14 Nov 2026 23:00:00 GMT"


def test_cache_is_keyed_by_url_and_user_agent(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    assert cache.key(PRODUCT_URL, UA) != cache.key(PRODUCT_URL, "OtherBot/1.0")
    with respx.mock(base_url="https://www.example.com.au") as mock:
        route = mock.get("/au/shop/product/widget").mock(
            return_value=httpx.Response(200, text="body")
        )
        client, _ = http_client(tmp_path, cache_ttl_seconds=600.0)
        client.get(PRODUCT_URL)
        other = HttpClient(
            HttpSettings(cache_dir=tmp_path, user_agent="OtherBot/1.0", cache_ttl_seconds=600.0),
            sleeper=lambda _: None,
            cache=DiskCache(tmp_path),
        )
        other.get(PRODUCT_URL)
        assert route.call_count == 2


def test_corrupt_cache_entry_is_a_miss(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path)
    path = cache.path_for(PRODUCT_URL, UA)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert cache.read(PRODUCT_URL, UA) is None


def test_settings_come_from_the_environment() -> None:
    from scripts.collectors.http import load_settings

    settings = load_settings(
        {
            "HTTP_TIMEOUT_SECONDS": "12",
            "HTTP_MAX_RETRIES": "4",
            "COLLECTOR_MAX_CONCURRENCY": "3",
            "HTTP_USER_AGENT": "TestBot/2.0",
        }
    )
    assert settings.timeout_seconds == 12.0
    assert settings.max_retries == 4
    assert settings.max_concurrency == 3
    assert settings.user_agent == "TestBot/2.0"


def test_settings_default_to_the_published_polite_values() -> None:
    from scripts.collectors.http import load_settings

    settings = load_settings({})
    assert settings.timeout_seconds == 20.0
    assert settings.max_retries == 3
    assert settings.max_concurrency == 2
    assert settings.user_agent == DEFAULT_USER_AGENT


@pytest.mark.parametrize("payload", [{"HTTP_MAX_RETRIES": "many"}, {"HTTP_TIMEOUT_SECONDS": "0"}])
def test_bad_settings_fail_loudly(payload: dict[str, str]) -> None:
    from scripts.collectors.http import load_settings

    with pytest.raises(ValueError):
        load_settings(payload)


# --------------------------------------------------------------------------
# The gate in front of the parser
# --------------------------------------------------------------------------


class ProbeAdapter(BaseAdapter):
    """An adapter whose parser raises if it is reached without permission."""

    seller_id = "probe"
    capabilities = Capabilities(product_page=True, stock=True)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.parser_calls = 0

    def policy_urls(self) -> Sequence[str]:
        return (PRODUCT_URL,)

    def _collect(self, targets: list[Any]) -> CollectionResult:
        self.parser_calls += 1
        return CollectionResult(offers=[], fetched_at=NOW)


def probe_adapter(
    tmp_path: Path,
    gate: RobotsGate,
    *,
    http: HttpClient | None = None,
) -> ProbeAdapter:
    client = http or http_client(tmp_path)[0]
    limiter = RateLimiter(
        {"probe": SellerPolicy("probe", rate_limit_rps=1.0, crawl_delay_seconds=0.0)},
        clock=lambda: 0.0,
        sleeper=lambda _: None,
    )
    return ProbeAdapter(
        http=client,
        gate=gate,
        limiter=limiter,
        concurrency=ConcurrencyGate(2),
        clock=lambda: NOW,
    )


def test_policy_denial_short_circuits_collect_and_never_reaches_the_parser(
    tmp_path: Path,
) -> None:
    gate, fetcher = gate_for("User-agent: *\nDisallow: /au/shop/product/\n")
    adapter = probe_adapter(tmp_path, gate)
    result = adapter.collect([])
    assert adapter.parser_calls == 0
    assert result.offers == []
    assert result.errors == []
    assert len(result.blocked) == 1
    assert result.blocked_by_policy is True
    blocked = result.blocked[0]
    assert blocked.seller_id == "probe"
    assert blocked.evidence_url == "https://www.example.com.au/robots.txt"
    assert blocked.checked_at == NOW
    assert "'/au/shop/product/'" in blocked.reason
    assert "most specific match" in blocked.reason
    assert fetcher.calls == ["https://www.example.com.au/robots.txt"]


def test_unreadable_robots_also_short_circuits_collect(tmp_path: Path) -> None:
    gate, _ = gate_for(error=httpx.ReadTimeout("no answer"))
    adapter = probe_adapter(tmp_path, gate)
    result = adapter.collect([])
    assert adapter.parser_calls == 0
    assert result.blocked_by_policy is True


def test_an_allow_all_decision_lets_the_adapter_run(tmp_path: Path) -> None:
    gate, _ = gate_for("User-agent: *\nAllow: /\n")
    adapter = probe_adapter(tmp_path, gate)
    result = adapter.collect([])
    assert adapter.parser_calls == 1
    assert result.blocked == []


def test_a_mid_run_403_becomes_a_block_not_a_retry(tmp_path: Path) -> None:
    gate, _ = gate_for("")

    class RefusedAdapter(BaseAdapter):
        seller_id = "refused"
        capabilities = Capabilities(product_page=True)

        def policy_urls(self) -> Sequence[str]:
            return (PRODUCT_URL,)

        def _collect(self, targets: list[Any]) -> CollectionResult:
            self.fetch(PRODUCT_URL)
            return CollectionResult(offers=[], fetched_at=NOW)

    limiter = RateLimiter(
        {"refused": SellerPolicy("refused", 1.0, 0.0)}, clock=lambda: 0.0, sleeper=lambda _: None
    )
    adapter = RefusedAdapter(
        http=http_client(tmp_path)[0],
        gate=gate,
        limiter=limiter,
        clock=lambda: NOW,
    )
    with respx.mock(base_url="https://www.example.com.au") as mock:
        route = mock.get("/au/shop/product/widget").mock(return_value=httpx.Response(403))
        result = adapter.collect([])
        assert route.call_count == 1
    assert result.offers == []
    assert len(result.blocked) == 1
    assert "refused automated access" in result.blocked[0].reason


def test_gate_and_client_must_present_the_same_identity(tmp_path: Path) -> None:
    gate, _ = gate_for("")
    settings = HttpSettings(cache_dir=tmp_path, user_agent="SomeOtherBot/9.9")
    with pytest.raises(ValueError, match="is a bypass"):
        ProbeAdapter(
            http=HttpClient(settings, sleeper=lambda _: None),
            gate=gate,
            limiter=RateLimiter(
                {"probe": SellerPolicy("probe", rate_limit_rps=1.0, crawl_delay_seconds=0.0)},
                clock=lambda: 0.0,
                sleeper=lambda _: None,
            ),
        )


def test_policy_result_without_decisions_is_not_permission() -> None:
    result = PolicyResult(seller_id="probe", decisions=())
    assert result.allowed is False
    blocked = result.blocked_source()
    assert blocked is not None
    assert "absence of a check is not permission" in blocked.reason


def test_policy_result_aggregates_denials() -> None:
    gate, _ = gate_for("User-agent: *\nDisallow: /au/shop/product/\n")
    allowed = gate.is_allowed("https://www.example.com.au/au/shop/buy-mac/macbook-air")
    denied = gate.is_allowed(PRODUCT_URL)
    result = PolicyResult(seller_id="probe", decisions=(allowed, denied))
    assert result.allowed is False
    assert result.denials == (denied,)
    assert result.evidence_url == "https://www.example.com.au/robots.txt"
    assert result.crawl_delay_seconds is None
    assert result.blocked_source() is not None
    assert result.to_json()["allowed"] is False


def test_source_adapter_protocol_is_satisfied_by_base_adapter(tmp_path: Path) -> None:
    from scripts.collectors.base import SourceAdapter

    adapter = probe_adapter(tmp_path, gate_for("")[0])
    assert isinstance(adapter, SourceAdapter)


def test_protocol_requires_check_policy_and_collect() -> None:
    from scripts.collectors.base import SourceAdapter

    class NotAnAdapter:
        seller_id = "nope"

    assert not isinstance(NotAnAdapter(), SourceAdapter)


# --------------------------------------------------------------------------
# The recorded Apple AU finding (real evidence, parsed offline)
# --------------------------------------------------------------------------

APPLE_ROBOTS = "fixtures/policy/apple_com_au.txt"
APPLE_BUY_URL = (
    "https://www.apple.com/au/shop/buy-mac/macbook-air/13-inch-midnight-m5-chip-10-core-cpu-"
    "8-core-gpu-16gb-memory-512gb-storage"
)
APPLE_PRODUCT_URL = "https://www.apple.com/au/shop/product/airpods-pro-3"


def test_saved_apple_robots_allows_apple_au_product_pages(repo_root: Path) -> None:
    body = (repo_root / APPLE_ROBOTS).read_text(encoding="utf-8")
    assert "User-agent: *" in body
    gate, _ = gate_for(body)
    for url in (APPLE_BUY_URL, APPLE_PRODUCT_URL, "https://www.apple.com/au/store"):
        decision = gate.is_allowed(url)
        assert decision.allowed is True, decision.reason
        assert decision.status == 200
        assert decision.crawl_delay_seconds is None
    # The saved file is not a blanket pass: Apple disallows its overlay paths.
    overlay = gate.is_allowed("https://www.apple.com/au/shop/browse/overlay/buy")
    assert overlay.allowed is False
    assert overlay.matched_rule == "Disallow: /*shop/browse/overlay/*"


def test_recorded_apple_finding_matches_the_saved_robots(repo_root: Path) -> None:
    payload = json.loads(
        (repo_root / "data" / "health" / "policy_findings.json").read_text(encoding="utf-8")
    )
    findings = {finding["seller_id"]: finding for finding in payload["findings"]}
    finding = findings["apple_au"]
    assert finding["robots_url"] == "https://www.apple.com/robots.txt"
    assert finding["status"] == 200
    assert finding["allowed"] is True
    assert finding["crawl_delay_seconds"] is None
    assert finding["checked_at"].endswith("Z")
    evidence = repo_root / finding["evidence_file"]
    assert evidence.is_file()
    assert "User-agent: *" in evidence.read_text(encoding="utf-8")
    assert "ALLOWED" in finding["notes"]

    registry = json.loads((repo_root / "data" / "sellers.json").read_text(encoding="utf-8"))
    known_sellers = {record["seller_id"] for record in registry["sellers"]}
    assert set(findings) <= known_sellers


def test_cli_replays_a_saved_body_offline(
    repo_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The recording tool can re-render a finding from evidence, with no network."""
    from scripts.collectors.policy import main

    findings = tmp_path / "policy_findings.json"
    code = main(
        [
            "--seller",
            "apple_au",
            "--from-file",
            str(repo_root / APPLE_ROBOTS),
            "--checked-at",
            "2026-11-15T11:04:23Z",
            "--url",
            APPLE_BUY_URL,
            "--findings",
            str(findings),
            "--fixtures",
            str(tmp_path / "policy"),
            "--evidence-stem",
            "apple_com_au",
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "ALLOWED" in printed
    assert "crawl_delay=None" in printed
    payload = json.loads(findings.read_text(encoding="utf-8"))
    assert payload["findings"][0]["checked_at"] == "2026-11-15T11:04:23Z"
    assert payload["findings"][0]["allowed"] is True
    assert (tmp_path / "policy" / "apple_com_au.txt").is_file()


def test_cli_exits_two_when_the_replayed_body_denies(tmp_path: Path) -> None:
    from scripts.collectors.policy import main

    body = tmp_path / "robots.txt"
    body.write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    code = main(
        [
            "--seller",
            "example",
            "--from-file",
            str(body),
            "--url",
            "https://www.example.com.au/au/shop/product/widget",
            "--no-save",
        ]
    )
    assert code == 2


def test_a_browser_user_agent_is_refused() -> None:
    from scripts.collectors.http import load_settings

    browser = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"
    with pytest.raises(ValueError, match="impersonation"):
        load_settings({"HTTP_USER_AGENT": browser})


def test_the_shipped_user_agent_identifies_the_project() -> None:
    from scripts.collectors.http import load_settings

    user_agent = load_settings({}).user_agent
    assert "OrchardPriceBot" in user_agent
    assert "apple-price-au" in user_agent
