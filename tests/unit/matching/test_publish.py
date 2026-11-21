"""Tests for deterministic latest-offer publication."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from scripts.matching.publish import publish_offers
from scripts.models.enums import (
    Category,
    MatchMethod,
    MatchStatus,
    PriceBasis,
    StockStatus,
    VerificationStatus,
)
from scripts.models.offer import Offer

CAPTURED_AT = datetime(2026, 11, 22, 0, 0, tzinfo=UTC)
SOURCE_HASH = "sha256:" + "c" * 64


def offer(
    offer_id: str,
    *,
    product_id: str = "macbook-air-13-m4-16gb",
    seller_id: str = "apple_au",
    price: str = "1599.00",
    delivery: str = "0.00",
    status: MatchStatus = MatchStatus.AUTO_PUBLISHED,
) -> Offer:
    return Offer(
        offer_id=offer_id,
        product_id=product_id,
        seller_id=seller_id,
        url="https://www.apple.com/au/",
        price_aud=Decimal(price),
        price_basis=PriceBasis.ADVERTISED,
        in_stock=True,
        stock_status=StockStatus.IN_STOCK,
        delivery_cost_aud=Decimal(delivery),
        confidence=Decimal("1.00"),
        match_method=MatchMethod.APPLE_SKU,
        match_status=status,
        verification_status=VerificationStatus.VERIFIED,
        captured_at=CAPTURED_AT,
        source_hash=SOURCE_HASH,
        run_id="run_001",
    )


def test_only_publishable_match_statuses_are_included(tmp_path) -> None:
    latest = tmp_path / "latest_offers.json"
    health = tmp_path / "match_confidence.json"

    result = publish_offers(
        [
            offer("published"),
            offer("approved", status=MatchStatus.APPROVED),
            offer("pending", status=MatchStatus.PENDING),
            offer("rejected", status=MatchStatus.REJECTED),
        ],
        latest_offers_path=latest,
        confidence_health_path=health,
    )

    assert result.published_offers == 2
    assert result.withheld_offers == 2

    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert [item["offer_id"] for item in payload["offers"]] == [
        "approved",
        "published",
    ]


def test_best_price_uses_landed_price(tmp_path) -> None:
    latest = tmp_path / "latest_offers.json"
    health = tmp_path / "match_confidence.json"

    result = publish_offers(
        [
            offer("retailer", seller_id="retailer", price="1549.00", delivery="25.00"),
            offer("apple", seller_id="apple_au", price="1599.00"),
        ],
        latest_offers_path=latest,
        confidence_health_path=health,
    )

    assert result.products_with_best_price == 1
    payload = json.loads(latest.read_text(encoding="utf-8"))
    best = payload["best_prices"]["macbook-air-13-m4-16gb"]
    assert best["offer_id"] == "retailer"
    assert best["total_price_aud"] == 1574.0


def test_output_is_stable_across_runs(tmp_path) -> None:
    latest = tmp_path / "latest_offers.json"
    health = tmp_path / "match_confidence.json"
    offers = [
        offer("second", price="1699.00"),
        offer("first", price="1599.00"),
    ]

    first = publish_offers(offers, latest_offers_path=latest, confidence_health_path=health)
    first_payload = latest.read_text(encoding="utf-8")

    second = publish_offers(offers, latest_offers_path=latest, confidence_health_path=health)
    second_payload = latest.read_text(encoding="utf-8")

    assert first.published_offers == 2
    assert second.published_offers == 2
    assert first_payload == second_payload


def test_confidence_health_is_written_per_seller(tmp_path) -> None:
    latest = tmp_path / "latest_offers.json"
    health = tmp_path / "match_confidence.json"

    publish_offers(
        [offer("one"), offer("two", seller_id="retailer")],
        latest_offers_path=latest,
        confidence_health_path=health,
    )

    payload = json.loads(health.read_text(encoding="utf-8"))
    assert payload["sellers"]["apple_au"]["offer_count"] == 1
    assert payload["sellers"]["retailer"]["offer_count"] == 1
    assert payload["sellers"]["apple_au"]["confidence_distribution"] == {"1.0": 1}


def test_empty_input_writes_empty_feeds(tmp_path) -> None:
    latest = tmp_path / "latest_offers.json"
    health = tmp_path / "match_confidence.json"

    result = publish_offers(
        [],
        latest_offers_path=latest,
        confidence_health_path=health,
    )

    assert result.published_offers == 0
    assert result.products_with_best_price == 0
    assert json.loads(latest.read_text(encoding="utf-8")) == {
        "offers": [],
        "best_prices": {},
    }
