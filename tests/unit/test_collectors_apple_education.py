"""Tests for the Apple Education Store collector (APA-012).

Every test here runs with the network off: HTTP responses come from a
``respx``-free ``httpx.MockTransport``, and robots.txt is injected into the gate.
The fixtures are the Product JSON-LD fragments captured from the live education
product pages on 2026-11-15, so the parser is exercised against the real markup
shape rather than a hand-written approximation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.collectors.apple_education import (
    ANNUAL_LIMIT_SOURCE_URL,
    ANNUAL_LIMIT_SUMMARY,
    EDUCATION_TERMS_URL,
    EDUCATION_VERIFICATION_URL,
    ELIGIBILITY_CATEGORIES,
    AppleEducationAdapter,
    FragmentError,
    build_snapshot,
    education_url_for,
    extract_product_fragment,
    load_education_products,
    read_price_fragment,
    source_hash,
    write_snapshot,
)
from scripts.collectors.base import ConcurrencyGate, RateLimiter, SellerPolicy
from scripts.collectors.http import DiskCache, HttpClient, HttpSettings
from scripts.collectors.policy import DEFAULT_USER_AGENT, RobotsGate, RobotsResource
from scripts.collectors.snapshot import SnapshotConflictError
from scripts.models import Product

NOW = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)
SELLER = "apple_education_au"
RUN_ID = "20261115T060000Z-testrun1"

#: Price observed in each saved fragment, in AUD. These are education prices,
#: captured from https://www.apple.com/au-edu/shop/... on 2026-11-15.
EXPECTED_EDUCATION_PRICES: Mapping[str, str] = {
    "apple-imac-24-inch-m6-16gb-256gb-blue": "2319.00",
    "apple-ipad-11-inch-a16-128gb-blue": "719.00",
    "apple-ipad-air-11-inch-m4-128gb-space-grey": "1169.00",
    "apple-ipad-pro-11-inch-m5-256gb-space-black": "1829.00",
    "apple-mac-mini-m6-16gb-256gb": "1289.00",
    "apple-macbook-air-13-inch-m5-16gb-512gb-midnight": "1939.00",
    "apple-macbook-air-15-inch-m5-16gb-512gb-midnight": "2339.00",
    "apple-macbook-pro-14-inch-m6-16gb-1tb-space-black": "3039.00",
}

#: saving_pct as (standard - education) / standard * 100, rounded half-up to 2dp.
EXPECTED_SAVING_PCT: Mapping[str, str] = {
    "apple-imac-24-inch-m6-16gb-256gb-blue": "3.33",
    "apple-ipad-11-inch-a16-128gb-blue": "4.01",
    "apple-ipad-air-11-inch-m4-128gb-space-grey": "6.41",
    "apple-ipad-pro-11-inch-m5-256gb-space-black": "8.50",
    "apple-mac-mini-m6-16gb-256gb": "11.04",
    "apple-macbook-air-13-inch-m5-16gb-512gb-midnight": "7.62",
    "apple-macbook-air-15-inch-m5-16gb-512gb-midnight": "6.40",
    "apple-macbook-pro-14-inch-m6-16gb-1tb-space-black": "5.00",
}

ALLOW_ROBOTS = "User-agent: *\nDisallow: /private/\n"
DENY_ROBOTS = "User-agent: *\nDisallow: /au-edu/\n"

TERMS_HTML = (
    "<html><body>"
    "<h2>Eligibility</h2>"
    "<p>Those eligible to purchase from the Apple Store for Education are as follows:</p>"
    "<ul>" + "".join(f"<li>{category};</li>" for category in ELIGIBILITY_CATEGORIES) + "</ul>"
    "<p>Additionally, products purchased from the Apple Store for Education must be for "
    "your own personal, education, or research use.</p>"
    "<h2>Purchase Quantity</h2>"
    f"<p>{ANNUAL_LIMIT_SUMMARY}</p>"
    "</body></html>"
)

VERIFICATION_HTML = (
    "<html><body>"
    "<a class='button'>Verify with UNiDAYS now</a>"
    "<a>Continue shopping and verify later</a>"
    '<script>var script = "Just shop the Education Store on apple.com and verify your '
    'eligibility during checkout with a UNiDAYS account.";</script>'
    "</body></html>"
)


def _fixture_dir(repo_root: Path) -> Path:
    return repo_root / "fixtures" / "html" / "apple_education"


def _fixture_payload(directory: Path, product_id: str) -> dict[str, Any]:
    path = directory / f"{product_id}.jsonld.json"
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _catalogue(repo_root: Path) -> list[Product]:
    return load_education_products(repo_root / "data" / "products.json")


def _product(repo_root: Path, product_id: str) -> Product:
    return next(item for item in _catalogue(repo_root) if item.product_id == product_id)


def _page(payload: Mapping[str, object] | None) -> str:
    """A minimal product page carrying (or omitting) the Product JSON-LD."""
    if payload is None:
        return "<html><body><h1>Buy Mac</h1><p>From A$1,939.00</p></body></html>"
    return (
        '<html><head><script type="application/ld+json">'
        + json.dumps(payload)
        + "</script></head><body><p>From A$1,939.00</p></body></html>"
    )


class _Site:
    """A fake apple.com that serves only the URLs a test declared."""

    def __init__(self, pages: Mapping[str, str]) -> None:
        self._pages = dict(pages)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        page = self._pages.get(url)
        if page is None:
            return httpx.Response(404, text="not found in this test's fake site")
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})


def _adapter(
    repo_root: Path,
    tmp_path: Path,
    pages: Mapping[str, str],
    *,
    robots: str = ALLOW_ROBOTS,
    run_id: str = RUN_ID,
) -> AppleEducationAdapter:
    settings = HttpSettings(
        user_agent=DEFAULT_USER_AGENT,
        cache_dir=tmp_path / "http-cache",
        cache_ttl_seconds=0.0,
        max_retries=0,
    )
    client = HttpClient(
        settings=settings,
        clock=lambda: NOW,
        sleeper=lambda _seconds: None,
        seed=1,
        cache=DiskCache(tmp_path / "http-cache"),
        transport=httpx.MockTransport(_Site(pages).handler),
    )

    def robots_fetcher(url: str) -> RobotsResource:
        return RobotsResource(
            url=url,
            status_code=200,
            content_type="text/plain",
            body=robots,
            fetched_at=NOW,
        )

    gate = RobotsGate(user_agent=DEFAULT_USER_AGENT, fetcher=robots_fetcher, clock=lambda: NOW)
    limiter = RateLimiter(
        {SELLER: SellerPolicy(seller_id=SELLER, rate_limit_rps=1000.0, crawl_delay_seconds=0.0)},
        clock=lambda: 0.0,
        sleeper=lambda _seconds: None,
    )
    return AppleEducationAdapter(
        http=client,
        gate=gate,
        limiter=limiter,
        concurrency=ConcurrencyGate(2),
        clock=lambda: NOW,
        run_id=run_id,
        products_path=repo_root / "data" / "products.json",
    )


def _full_site(repo_root: Path) -> dict[str, str]:
    directory = _fixture_dir(repo_root)
    pages: dict[str, str] = {
        EDUCATION_TERMS_URL: TERMS_HTML,
        EDUCATION_VERIFICATION_URL: VERIFICATION_HTML,
    }
    for product in _catalogue(repo_root):
        pages[education_url_for(product)] = _page(_fixture_payload(directory, product.product_id))
    return pages


# --------------------------------------------------------------------------- #
# Fixtures parse to the observed prices
# --------------------------------------------------------------------------- #


def test_saved_fixture_files_exist_for_every_eligible_product(repo_root: Path) -> None:
    directory = _fixture_dir(repo_root)
    on_disk = sorted(path.name for path in directory.glob("*.jsonld.json"))
    expected = sorted(f"{product_id}.jsonld.json" for product_id in EXPECTED_EDUCATION_PRICES)
    assert on_disk == expected


@pytest.mark.parametrize("product_id", sorted(EXPECTED_EDUCATION_PRICES))
def test_saved_fixture_parses_to_the_expected_education_price(
    repo_root: Path, product_id: str
) -> None:
    directory = _fixture_dir(repo_root)
    path = directory / f"{product_id}.jsonld.json"
    assert path.stat().st_size < 40_000, f"{path.name} exceeds the 40 KB fixture budget"

    fragment = read_price_fragment(_fixture_payload(directory, product_id))

    assert fragment.price_aud == Decimal(EXPECTED_EDUCATION_PRICES[product_id])
    assert fragment.currency == "AUD"
    assert fragment.sku == _product(repo_root, product_id).apple_sku
    assert fragment.source_hash == source_hash(fragment.payload)


def test_fragment_without_a_price_is_rejected() -> None:
    with pytest.raises(FragmentError):
        read_price_fragment({"@type": "Product", "name": "No offers here"})
    with pytest.raises(FragmentError):
        read_price_fragment(
            {
                "@type": "Product",
                "offers": [{"@type": "Offer", "priceCurrency": "USD", "price": 1939.0}],
            }
        )


def test_extract_product_fragment_skips_non_product_jsonld() -> None:
    html = (
        '<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>'
        '<script type="application/ld+json">{"@type":"Product","name":"X",'
        '"offers":[{"priceCurrency":"AUD","price":1.00}]}</script>'
    )
    payload = extract_product_fragment(html)
    assert payload is not None
    assert payload["@type"] == "Product"


# --------------------------------------------------------------------------- #
# A full run
# --------------------------------------------------------------------------- #


def test_full_run_emits_one_offer_per_eligible_product(repo_root: Path, tmp_path: Path) -> None:
    adapter = _adapter(repo_root, tmp_path, _full_site(repo_root))
    targets = _catalogue(repo_root)

    result = adapter.collect_education(targets)

    assert result.blocked == []
    assert result.errors == []
    assert {offer.product_id for offer in result.offers} == set(EXPECTED_EDUCATION_PRICES)
    assert len(result.offers) == len(targets) == 8
    assert result.policy is not None and result.policy.allowed
    assert result.terms.limits_verified and result.terms.eligibility_verified
    assert result.verification.verified


@pytest.mark.parametrize("product_id", sorted(EXPECTED_SAVING_PCT))
def test_saving_pct_recomputes_to_two_decimal_places(
    repo_root: Path, tmp_path: Path, product_id: str
) -> None:
    adapter = _adapter(repo_root, tmp_path, _full_site(repo_root))
    result = adapter.collect_education([_product(repo_root, product_id)])

    assert len(result.offers) == 1
    offer = result.offers[0]
    expected_saving = offer.standard_price_aud - offer.price_aud
    expected_pct = (expected_saving / offer.standard_price_aud * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    assert offer.saving_aud == expected_saving
    assert offer.saving_pct == expected_pct
    assert offer.saving_pct == Decimal(EXPECTED_SAVING_PCT[product_id])
    assert -offer.saving_pct.as_tuple().exponent == 2
    assert -offer.saving_aud.as_tuple().exponent == 2


def test_education_price_is_below_retail_and_never_becomes_an_rrp(
    repo_root: Path, tmp_path: Path
) -> None:
    adapter = _adapter(repo_root, tmp_path, _full_site(repo_root))
    result = adapter.collect_education(_catalogue(repo_root))

    for offer in result.offers:
        product = _product(repo_root, offer.product_id)
        assert product.apple_rrp_aud is not None
        assert offer.price_aud < offer.standard_price_aud
        assert offer.price_aud != product.apple_rrp_aud
        assert offer.standard_price_aud == product.apple_rrp_aud
        assert offer.price_basis.value == "edu"


def test_retail_price_is_never_recorded_as_an_education_price(
    repo_root: Path, tmp_path: Path
) -> None:
    product_id = "apple-macbook-air-13-inch-m5-16gb-512gb-midnight"
    product = _product(repo_root, product_id)
    payload = _fixture_payload(_fixture_dir(repo_root), product_id)
    offers = payload["offers"]
    assert isinstance(offers, list)
    offers[0]["price"] = float(product.apple_rrp_aud or 0)  # the retail price, mis-served

    pages = {
        EDUCATION_TERMS_URL: TERMS_HTML,
        EDUCATION_VERIFICATION_URL: VERIFICATION_HTML,
        education_url_for(product): _page(payload),
    }
    result = _adapter(repo_root, tmp_path, pages).collect_education([product])

    assert result.offers == []
    assert len(result.errors) == 1
    assert "not below the catalogue Apple AU retail price" in result.errors[0].message
    assert result.errors[0].product_id == product_id


def test_product_without_an_education_price_produces_one_error_not_a_crash(
    repo_root: Path, tmp_path: Path
) -> None:
    product = _product(repo_root, "apple-mac-mini-m6-16gb-256gb")
    pages = {
        EDUCATION_TERMS_URL: TERMS_HTML,
        EDUCATION_VERIFICATION_URL: VERIFICATION_HTML,
        education_url_for(product): _page(None),
    }

    result = _adapter(repo_root, tmp_path, pages).collect_education([product])

    assert result.offers == []
    assert len(result.errors) == 1
    assert result.errors[0].error_type == "MissingJsonLd"
    assert result.errors[0].product_id == product.product_id


def test_missing_price_does_not_stop_the_other_products(repo_root: Path, tmp_path: Path) -> None:
    broken = _product(repo_root, "apple-mac-mini-m6-16gb-256gb")
    healthy = _product(repo_root, "apple-ipad-11-inch-a16-128gb-blue")
    pages = _full_site(repo_root)
    pages[education_url_for(broken)] = _page(None)

    result = _adapter(repo_root, tmp_path, pages).collect_education([broken, healthy])

    assert [offer.product_id for offer in result.offers] == [healthy.product_id]
    assert len(result.errors) == 1


def test_robots_denial_produces_zero_offers(repo_root: Path, tmp_path: Path) -> None:
    adapter = _adapter(repo_root, tmp_path, _full_site(repo_root), robots=DENY_ROBOTS)

    result = adapter.collect_education(_catalogue(repo_root))

    assert result.offers == []
    assert result.blocked_by_policy
    assert result.errors == []
    assert "/au-edu/" in result.blocked[0].reason
    assert "Disallow" in result.blocked[0].reason
    assert result.blocked[0].evidence_url == "https://www.apple.com/robots.txt"


def test_ineligible_products_are_never_collected(repo_root: Path, tmp_path: Path) -> None:
    eligible = _product(repo_root, "apple-ipad-11-inch-a16-128gb-blue")
    ineligible = eligible.model_copy(update={"education_eligible": False})
    adapter = _adapter(repo_root, tmp_path, _full_site(repo_root))

    result = adapter.collect_education([ineligible, eligible])

    assert [offer.product_id for offer in result.offers] == [eligible.product_id]
    assert result.errors == []


# --------------------------------------------------------------------------- #
# Metadata: eligibility, verification and limits
# --------------------------------------------------------------------------- #


def test_offer_carries_apple_published_eligibility_and_limits(
    repo_root: Path, tmp_path: Path
) -> None:
    adapter = _adapter(repo_root, tmp_path, _full_site(repo_root))
    result = adapter.collect_education(_catalogue(repo_root))
    offer = result.offers[0]

    assert offer.eligibility == list(ELIGIBILITY_CATEGORIES)
    assert len(offer.eligibility) == 4
    assert offer.verification == "UNiDAYS"
    assert offer.annual_limit_summary == ANNUAL_LIMIT_SUMMARY
    assert str(offer.annual_limit_source_url) == ANNUAL_LIMIT_SOURCE_URL == EDUCATION_TERMS_URL
    assert offer.verification_status.value == "verified"
    assert offer.price_basis.value == "edu"
    assert offer.run_id == RUN_ID


def test_url_rewrite_keeps_the_apple_path_and_moves_it_to_the_education_store(
    repo_root: Path,
) -> None:
    for product in _catalogue(repo_root):
        education = education_url_for(product)
        assert education.startswith("https://www.apple.com/au-edu/shop/")
        assert (
            education.split("/au-edu/shop/", 1)[1]
            == str(product.apple_au_url).split("/au/shop/", 1)[1]
        )


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #


def test_snapshot_records_the_public_price_behaviour_and_sources(
    repo_root: Path, tmp_path: Path
) -> None:
    adapter = _adapter(repo_root, tmp_path, _full_site(repo_root))
    targets = _catalogue(repo_root)
    result = adapter.collect_education(targets)

    payload = build_snapshot(
        result, run_id=RUN_ID, user_agent=adapter.http.user_agent, targets=targets
    )

    snapshot = payload["snapshot"]
    assert isinstance(snapshot, dict)
    observation = snapshot["store_observation"]
    assert isinstance(observation, dict)
    assert observation["education_prices_public"] is True
    assert observation["sign_in_required_to_view_price"] is False
    assert observation["verification_vendor"] == "UNiDAYS"
    assert observation["verification_gate"] == "checkout"
    assert snapshot["annual_limit_summary"] == ANNUAL_LIMIT_SUMMARY
    assert snapshot["annual_limit_source_url"] == ANNUAL_LIMIT_SOURCE_URL
    assert snapshot["eligibility"] == list(ELIGIBILITY_CATEGORIES)
    assert "NOT OBSERVED" in str(snapshot["stock_basis"])

    assert payload["schema_version"] == 1
    offers = payload["offers"]
    assert isinstance(offers, list) and len(offers) == 8
    first = offers[0]
    assert isinstance(first, dict)
    assert first["price_basis"] == "edu"
    assert first["verification"] == "UNiDAYS"
    # Tri-state: None means "the page carries no stock statement". Encoded as
    # False it would publish a false "out of stock" claim.
    assert first["in_stock"] is None
    assert Decimal(str(first["price_aud"])) < Decimal(str(first["standard_price_aud"]))
    assert payload["errors"] == []
    assert payload["blocked"] == []


def test_snapshot_writer_refuses_to_rewrite_another_capture_date(tmp_path: Path) -> None:
    payload = {
        "snapshot": {"captured_at": "2026-11-15T06:00:00Z"},
        "offers": [],
    }
    with pytest.raises(ValueError, match="append-only"):
        write_snapshot(payload, tmp_path / "2026-11-14" / "apple_education_au.json")

    written = write_snapshot(payload, tmp_path / "2026-11-15" / "apple_education_au.json")
    assert written.is_file()


def test_snapshot_writer_refuses_to_replace_different_evidence(tmp_path: Path) -> None:
    path = tmp_path / "2026-11-15" / "apple_education_au.json"
    first = {"schema_version": 1, "snapshot": {"captured_at": "2026-11-15T06:00:00Z"}, "offers": []}
    assert write_snapshot(first, path) == write_snapshot(first, path)  # idempotent

    second = {
        "schema_version": 1,
        "snapshot": {"captured_at": "2026-11-15T07:00:00Z"},
        "offers": [],
    }
    with pytest.raises(SnapshotConflictError):
        write_snapshot(second, path)
    assert json.loads(path.read_text(encoding="utf-8")) == first


def test_limited_cli_run_never_replaces_a_complete_snapshot(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.collectors import apple_education

    adapter = _adapter(repo_root, tmp_path, _full_site(repo_root))
    monkeypatch.setattr(apple_education, "_build_adapter", lambda _path: adapter)
    snapshot = tmp_path / "2026-11-15" / "apple_education_au.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text('{"snapshot": {"captured_at": "2026-11-15T00:00:00Z"}}\n', encoding="utf-8")

    code = apple_education.main(["--limit", "3", "--snapshot", str(snapshot)])

    assert code == 0
    written = json.loads(snapshot.read_text(encoding="utf-8"))
    assert written["snapshot"]["captured_at"] == "2026-11-15T00:00:00Z"
