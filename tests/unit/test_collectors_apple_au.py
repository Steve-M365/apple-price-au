"""Tests for the Apple AU collector: fixtures, fallbacks and refusals.

Every test here runs with the network off. Live Apple captures are committed as
trimmed fixtures under ``fixtures/html/apple_au/``: the page title, the JSON-LD
``Product`` block and the buy-block price fragments, and nothing else. The
synthetic variants under ``variants/`` each document the single field they change
so a reviewer can see exactly what is being pinned.

The tests are as interested in what the collector *refuses* as in what it
publishes: a title that has moved, a buy block that prices several variants and
a part number that contradicts the catalogue must all stop the offer rather than
produce a plausible-but-wrong price.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.collectors.apple_au import (
    AppleAuAdapter,
    CapturedPage,
    LowConfidenceError,
    ParseError,
    TitleMismatchError,
    build_offer,
    fixture_payload,
    load_products,
    main,
    map_availability,
    offer_from_capture,
    offer_id_for,
    parse_page,
    save_fixture,
)
from scripts.collectors.base import ConcurrencyGate
from scripts.collectors.http import HttpClient, HttpSettings
from scripts.collectors.policy import DEFAULT_USER_AGENT, RobotsGate, RobotsResource
from scripts.collectors.snapshot import (
    SnapshotConflictError,
    render_snapshot,
    write_snapshot,
)
from scripts.models import Offer, Product
from scripts.models.enums import MatchMethod, StockStatus

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "html" / "apple_au"
VARIANTS = FIXTURES / "variants"
CAPTURED_AT = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)
RUN_ID = "run-20261115T120000"

ALLOW_ROBOTS = "User-agent: *\nAllow: /au/shop/\n"
DENY_ROBOTS = "User-agent: *\nDisallow: /au/shop/\n"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _catalogue() -> dict[str, Product]:
    """The committed catalogue keyed by product_id."""
    return {product.product_id: product for product in load_products()}


def _real_fixtures() -> list[Path]:
    """Every committed live capture, one per catalogue product."""
    return sorted(path for path in FIXTURES.glob("*.json"))


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _page(path: Path) -> CapturedPage:
    return CapturedPage.from_fixture(_load(path))


def _offer(path: Path) -> tuple[Product, Offer]:
    payload = _load(path)
    product = _catalogue()[payload["product_id"]]
    return product, offer_from_capture(product, _page(path), run_id=RUN_ID)


def _robots(body: str) -> RobotsGate:
    """A gate that replays a fixed robots.txt body instead of fetching one."""

    def fetcher(url: str) -> RobotsResource:
        return RobotsResource(
            url=url,
            status_code=200,
            content_type="text/plain",
            body=body,
            fetched_at=CAPTURED_AT,
        )

    return RobotsGate(user_agent=DEFAULT_USER_AGENT, fetcher=fetcher, clock=lambda: CAPTURED_AT)


def _limiter() -> Any:
    from scripts.collectors.base import SELLERS_REGISTRY, RateLimiter

    return RateLimiter.from_registry(SELLERS_REGISTRY, sleeper=lambda _seconds: None)


def _adapter(
    tmp_path: Path,
    *,
    robots: str = ALLOW_ROBOTS,
    handler: Any = None,
    fixtures_dir: Path | None = None,
    catalogue: Path | None = None,
) -> AppleAuAdapter:
    settings = HttpSettings(
        user_agent=DEFAULT_USER_AGENT,
        cache_dir=tmp_path / "cache",
        cache_ttl_seconds=0.0,
        max_concurrency=1,
    )
    http = HttpClient(
        settings,
        clock=lambda: CAPTURED_AT,
        sleeper=lambda _seconds: None,
        transport=httpx.MockTransport(handler or _no_requests),
    )
    return AppleAuAdapter(
        http=http,
        gate=_robots(robots),
        limiter=_limiter(),
        concurrency=ConcurrencyGate(1),
        clock=lambda: CAPTURED_AT,
        fixtures_dir=fixtures_dir,
        catalogue=catalogue or Path("data/products.json").resolve(),
    )


def _no_requests(request: httpx.Request) -> httpx.Response:  # pragma: no cover - guard
    raise AssertionError(f"the collector fetched {request.url} when it should not have")


def _serve_by_url(pages: Mapping[str, str]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(str(request.url))
        if body is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return handler


@pytest.fixture
def tiny_catalogue(tmp_path: Path) -> Path:
    """A two-product catalogue pointing at URLs the tests mock."""
    records = [
        {
            "product_id": product_id,
            "category": "airpods" if "airpods" in product_id else "mac",
            "family": "test",
            "name": name,
            "model": name,
            "apple_au_url": url,
            "created_at": "2026-11-15T00:00:00Z",
            "updated_at": "2026-11-15T00:00:00Z",
        }
        for product_id, name, url in (
            (
                "apple-airpods-pro-3",
                "AirPods Pro 3",
                "https://www.apple.com/au/shop/buy-airpods/airpods-pro-3",
            ),
            (
                "apple-mac-mini-m6-16gb-256gb",
                "Mac mini M6 16GB 256GB",
                "https://www.apple.com/au/shop/buy-mac/mac-mini/m6-256",
            ),
        )
    ]
    path = tmp_path / "products.json"
    path.write_text(
        json.dumps({"products": records, "generated_at": "2026-11-15T00:00:00Z"}),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# Fixture coverage
# --------------------------------------------------------------------------- #


def test_every_committed_fixture_names_a_catalogue_product() -> None:
    catalogue = _catalogue()
    saved = {_load(path)["product_id"] for path in _real_fixtures()}
    assert saved, "no committed live fixtures"
    # The fixtures were captured for the original seed catalogue. Every one must
    # still name a live product; the generated catalogue has since grown past
    # the seed, so the fixture set is a subset rather than the whole catalogue.
    assert saved <= set(catalogue)


def test_fixtures_are_trimmed_captures_not_pages() -> None:
    for path in _real_fixtures():
        payload = _load(path)
        assert payload["synthetic"] is False
        assert payload["http_status"] == 200
        # A trimmed capture keeps title, JSON-LD and price fragments only.
        assert len(payload["html"]) < 40_000, path.name
        assert "<title>" in payload["html"]
        assert "application/ld+json" in payload["html"]


@pytest.mark.parametrize("path", _real_fixtures(), ids=lambda path: path.stem)
def test_every_fixture_parses_to_its_catalogue_product(path: Path) -> None:
    product, offer = _offer(path)

    assert offer.product_id == product.product_id
    assert offer.price_aud == product.apple_rrp_aud
    assert offer.seller_id == "apple_au"
    assert offer.verification_status.value == "verified"
    assert offer.confidence >= Decimal("0.85")
    assert offer.source_hash.startswith("sha256:")
    assert offer.was_price_aud is None
    # captured_at is the fetch instant recorded with the capture.
    assert offer.captured_at == _page(path).fetched_at


@pytest.mark.parametrize("path", _real_fixtures(), ids=lambda path: path.stem)
def test_every_fixture_matches_on_part_number(path: Path) -> None:
    product, offer = _offer(path)

    assert offer.match_method is MatchMethod.APPLE_SKU
    assert offer.confidence == 1.0
    assert product.apple_sku is not None
    assert product.apple_sku in _load(path)["html"]


def test_parse_path_is_recorded_for_every_fixture() -> None:
    paths = {_load(path)["product_id"]: _page(path) for path in _real_fixtures()}
    assert all(parse_page(page).parse_path == "jsonld" for page in paths.values())


# --------------------------------------------------------------------------- #
# Fallbacks and edge cases
# --------------------------------------------------------------------------- #


def test_jsonld_absent_uses_the_markup_fallback() -> None:
    path = VARIANTS / "jsonld-absent-airpods-pro-3.json"
    _, offer = _offer(path)

    assert "application/ld+json" not in _load(path)["html"]
    assert parse_page(_page(path)).parse_path == "html"
    assert offer.price_aud == Decimal("429.00")
    assert offer.product_id == "apple-airpods-pro-3"
    # No JSON-LD means no part number and no availability statement.
    assert offer.match_method is MatchMethod.CONFIG
    assert offer.confidence == Decimal("0.95")
    assert offer.stock_status is StockStatus.UNKNOWN


def test_html_fallback_refuses_an_ambiguous_buy_block() -> None:
    page = _page(VARIANTS / "ambiguous-price-ipad-air.json")

    with pytest.raises(ParseError) as caught:
        parse_page(page)
    assert "different prices" in str(caught.value)
    assert "refusing to guess" in str(caught.value)


def test_out_of_stock_availability_is_mapped() -> None:
    product, offer = _offer(VARIANTS / "out-of-stock-apple-pencil-pro.json")

    assert product.product_id == "apple-pencil-pro"
    assert offer.stock_status is StockStatus.OUT_OF_STOCK
    assert offer.in_stock is False


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        ("http://schema.org/InStock", StockStatus.IN_STOCK),
        ("https://schema.org/OutOfStock", StockStatus.OUT_OF_STOCK),
        ("https://schema.org/PreOrder", StockStatus.PREORDER),
        ("https://schema.org/BackOrder", StockStatus.UNKNOWN),
        ("", StockStatus.UNKNOWN),
        (None, StockStatus.UNKNOWN),
    ],
)
def test_availability_mapping(published: str | None, expected: StockStatus) -> None:
    assert map_availability(published) is expected


def test_absent_availability_is_unknown_not_in_stock() -> None:
    _, offer = _offer(FIXTURES / "apple-mac-mini-m6-16gb-256gb.json")

    assert offer.stock_status is StockStatus.UNKNOWN
    assert offer.in_stock is False


def test_delivery_cost_is_only_recorded_when_the_page_states_it() -> None:
    _, offer = _offer(FIXTURES / "apple-airpods-pro-3.json")

    # Apple's JSON-LD publishes a 0.00 AUD shipping rate, so it is verified.
    assert offer.delivery_cost_aud == Decimal("0.00")
    assert offer.delivery_cost_verified is True

    _, fallback = _offer(VARIANTS / "jsonld-absent-airpods-pro-3.json")
    # The markup says nothing about shipping, so the model default stands.
    assert fallback.delivery_cost_aud == Decimal("0.00")
    assert fallback.delivery_cost_verified is False


def test_source_hash_covers_the_fragment_not_the_page() -> None:
    page = _page(FIXTURES / "apple-airpods-pro-3.json")
    evidence = parse_page(page)

    expected = hashlib.sha256(evidence.source_fragment.encode("utf-8")).hexdigest()
    assert evidence.source_hash == f"sha256:{expected}"
    whole_page = hashlib.sha256(page.html.encode("utf-8")).hexdigest()
    assert evidence.source_hash != f"sha256:{whole_page}"
    assert '"@type":"Product"' in evidence.source_fragment


def test_superseded_title_is_refused_rather_than_remapped() -> None:
    path = VARIANTS / "title-superseded-apple-mac-mini.json"
    product = _catalogue()["apple-mac-mini-m6-16gb-256gb"]

    with pytest.raises(TitleMismatchError) as caught:
        offer_from_capture(product, _page(path), run_id=RUN_ID)
    assert caught.value.resolved_product_id == "apple-mac-mini-m5-16gb-256gb"
    assert caught.value.catalogue_product_id == "apple-mac-mini-m6-16gb-256gb"
    assert caught.value.error_type == "title_mismatch"
    assert "refusing to re-map" in str(caught.value)


def test_unrecognised_title_is_a_title_mismatch() -> None:
    path = VARIANTS / "title-incomplete-apple-mac-mini.json"
    product = _catalogue()["apple-mac-mini-m6-16gb-256gb"]

    with pytest.raises(TitleMismatchError) as caught:
        offer_from_capture(product, _page(path), run_id=RUN_ID)
    assert caught.value.resolved_product_id is None
    assert "cannot be normalised" in str(caught.value)


def test_contradicting_part_number_is_not_published() -> None:
    product = _catalogue()["apple-airpods-pro-3"]
    payload = _load(FIXTURES / "apple-airpods-pro-3.json")
    payload["html"] = payload["html"].replace('"sku":"MFHP4X/A"', '"sku":"MFHP9X/A"')
    page = CapturedPage.from_fixture(payload)

    with pytest.raises(LowConfidenceError) as caught:
        offer_from_capture(product, page, run_id=RUN_ID)
    assert caught.value.confidence < Decimal("0.85")
    assert caught.value.error_type == "low_confidence"
    assert "does not contain the catalogue part number" in str(caught.value)


def test_watch_bundle_part_number_matches_on_a_component() -> None:
    _, offer = _offer(FIXTURES / "apple-watch-series-12-42mm-space-grey-aluminium-gps.json")

    assert offer.match_method is MatchMethod.APPLE_SKU
    assert offer.confidence == 1.0


def test_no_price_at_all_raises_a_parse_error() -> None:
    page = CapturedPage(
        url="https://www.apple.com/au/shop/buy-airpods/airpods-pro-3",
        html="<html><head><title>Buy AirPods Pro 3 - Apple (AU)</title></head><body></body></html>",
        fetched_at=CAPTURED_AT,
    )

    with pytest.raises(ParseError) as caught:
        parse_page(page)
    assert "no JSON-LD Product price" in str(caught.value)


# --------------------------------------------------------------------------- #
# Offer identity
# --------------------------------------------------------------------------- #


def test_offer_id_is_stable_across_two_runs_on_the_same_day() -> None:
    product = _catalogue()["apple-airpods-pro-3"]
    page = _page(FIXTURES / "apple-airpods-pro-3.json")

    first = offer_from_capture(product, page, run_id=RUN_ID)
    second = offer_from_capture(product, page, run_id="run-20261115T235959")

    assert first.offer_id == second.offer_id == "apple-au-apple-airpods-pro-3-20261115"
    assert first.run_id != second.run_id


def test_offer_id_changes_with_the_capture_date() -> None:
    next_day = datetime(2026, 11, 16, 0, 0, 1, tzinfo=UTC)
    assert offer_id_for("apple-airpods-pro-3", CAPTURED_AT).endswith("20261115")
    assert offer_id_for("apple-airpods-pro-3", next_day).endswith("20261116")


def test_build_offer_rejects_a_low_confidence_match() -> None:
    product = _catalogue()["apple-airpods-pro-3"]
    evidence = parse_page(_page(FIXTURES / "apple-airpods-pro-3.json"))

    with pytest.raises(LowConfidenceError):
        build_offer(
            product,
            replace(evidence, sku="MFHP0X/A"),
            run_id=RUN_ID,
            captured_at=CAPTURED_AT,
        )


# --------------------------------------------------------------------------- #
# Collection: policy, errors and fixtures
# --------------------------------------------------------------------------- #


def test_robots_denial_short_circuits_to_zero_offers(tmp_path: Path) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        requests.append(str(request.url))
        return httpx.Response(200, text="should not be fetched")

    adapter = _adapter(tmp_path, robots=DENY_ROBOTS, handler=handler)
    result = adapter.collect(load_products())

    assert result.offers == []
    assert result.errors == []
    assert result.blocked
    assert "Disallow" in result.blocked[0].reason
    assert requests == []
    assert adapter.parse_paths == {}


def test_collect_records_a_title_mismatch_and_keeps_the_other_offers(
    tmp_path: Path, tiny_catalogue: Path
) -> None:
    pages = {
        "https://www.apple.com/au/shop/buy-airpods/airpods-pro-3": _load(
            FIXTURES / "apple-airpods-pro-3.json"
        )["html"],
        "https://www.apple.com/au/shop/buy-mac/mac-mini/m6-256": _load(
            VARIANTS / "title-superseded-apple-mac-mini.json"
        )["html"],
    }
    adapter = _adapter(tmp_path, handler=_serve_by_url(pages), catalogue=tiny_catalogue)

    result = adapter.collect(load_products(tiny_catalogue))

    assert [offer.product_id for offer in result.offers] == ["apple-airpods-pro-3"]
    assert len(result.errors) == 1
    error = result.errors[0]
    assert error.product_id == "apple-mac-mini-m6-16gb-256gb"
    assert error.error_type == "title_mismatch"
    assert error.url == "https://www.apple.com/au/shop/buy-mac/mac-mini/m6-256"
    assert adapter.parse_paths == {"apple-airpods-pro-3": "jsonld"}


def test_collect_records_http_errors_per_product(tmp_path: Path, tiny_catalogue: Path) -> None:
    pages = {
        "https://www.apple.com/au/shop/buy-airpods/airpods-pro-3": _load(
            FIXTURES / "apple-airpods-pro-3.json"
        )["html"]
    }
    adapter = _adapter(tmp_path, handler=_serve_by_url(pages), catalogue=tiny_catalogue)

    result = adapter.collect(load_products(tiny_catalogue))

    assert [offer.product_id for offer in result.offers] == ["apple-airpods-pro-3"]
    assert [error.error_type for error in result.errors] == ["parse_error"]
    assert "HTTP 404" in result.errors[0].message


def test_collect_saves_a_trimmed_fixture_when_asked(tmp_path: Path, tiny_catalogue: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    pages = {
        "https://www.apple.com/au/shop/buy-airpods/airpods-pro-3": _load(
            FIXTURES / "apple-airpods-pro-3.json"
        )["html"]
    }
    adapter = _adapter(
        tmp_path,
        handler=_serve_by_url(pages),
        fixtures_dir=fixtures_dir,
        catalogue=tiny_catalogue,
    )

    result = adapter.collect(load_products(tiny_catalogue))

    assert len(result.offers) == 1
    written = fixtures_dir / "apple-airpods-pro-3.json"
    assert written.is_file()
    saved = _load(written)
    assert saved["product_id"] == "apple-airpods-pro-3"
    assert saved["parse_path"] == "jsonld"
    assert "application/ld+json" in saved["html"]
    assert len(saved["html"]) < 40_000
    # A second identical capture must not churn the committed fixture.
    assert (
        save_fixture(
            fixtures_dir,
            fixture_payload(
                _catalogue()["apple-airpods-pro-3"],
                _page(FIXTURES / "apple-airpods-pro-3.json"),
                parse_path="jsonld",
            ),
        )
        is None
    )


# --------------------------------------------------------------------------- #
# Snapshots
# --------------------------------------------------------------------------- #


def _offers() -> list[Offer]:
    return [
        _offer(FIXTURES / "apple-airpods-pro-3.json")[1],
        _offer(FIXTURES / "apple-mac-mini-m6-16gb-256gb.json")[1],
    ]


def test_snapshot_writer_writes_once_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "2026-11-15" / "apple_au.json"
    offers = _offers()

    written = write_snapshot(path, "apple_au", RUN_ID, offers, CAPTURED_AT)
    assert written == path
    first = path.read_bytes()
    payload = json.loads(first)
    assert payload["seller_id"] == "apple_au"
    assert payload["run_id"] == RUN_ID
    assert payload["captured_date"] == "2026-11-15"
    assert payload["offer_count"] == 2
    assert [record["offer_id"] for record in payload["offers"]] == sorted(
        offer.offer_id for offer in offers
    )

    assert write_snapshot(path, "apple_au", RUN_ID, offers, CAPTURED_AT) == path
    assert path.read_bytes() == first
    # Evidence is published: the atomic write must not leave a 0600 temp-file mode.
    assert path.stat().st_mode & 0o444 == 0o444


def test_snapshot_writer_refuses_to_overwrite_different_content(tmp_path: Path) -> None:
    path = tmp_path / "apple_au.json"
    offers = _offers()
    write_snapshot(path, "apple_au", RUN_ID, offers, CAPTURED_AT)
    original = path.read_bytes()

    with pytest.raises(SnapshotConflictError) as caught:
        write_snapshot(path, "apple_au", "run-20261115T131500", offers, CAPTURED_AT)
    assert "append-only" in str(caught.value)
    assert path.read_bytes() == original


def test_snapshot_writer_refuses_a_seller_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="belongs to seller"):
        write_snapshot(tmp_path / "x.json", "jbhifi", RUN_ID, _offers(), CAPTURED_AT)


def test_render_snapshot_is_deterministic(tmp_path: Path) -> None:
    offers = _offers()
    assert render_snapshot("apple_au", RUN_ID, offers, CAPTURED_AT) == render_snapshot(
        "apple_au", RUN_ID, list(reversed(offers)), CAPTURED_AT
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_dry_run_fetches_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--dry-run", "--limit", "2"]) == 0
    output = capsys.readouterr().out
    assert "dry run" in output
    assert "nothing fetched" in output
    # Which two products a --limit run picks depends on catalogue order, so
    # assert the shape: two catalogued products, no duplicates, nothing fetched.
    catalogued = set(_catalogue())
    shown = [line for line in output.splitlines() if line.startswith("  apple-")]
    assert len(shown) == 2
    assert len({line.split()[0] for line in shown}) == 2
    assert all(line.split()[0] in catalogued for line in shown)


def test_policy_urls_cover_the_catalogue(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    urls = adapter.policy_urls()
    assert len(urls) == len(load_products())
    assert all("apple.com/au/shop/" in url for url in urls)
