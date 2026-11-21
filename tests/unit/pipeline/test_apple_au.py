"""Offline tests for the Apple AU collection-to-publication pipeline."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scripts.collectors.base import CollectionError, CollectionResult
from scripts.pipeline.apple_au import publish_apple_au_collection, run_apple_au
from scripts.models.enums import (
    MatchMethod,
    MatchStatus,
    PriceBasis,
    StockStatus,
    VerificationStatus,
)
from scripts.models.offer import Offer

CAPTURED_AT = datetime(2026, 11, 22, 0, 0, tzinfo=UTC)
SOURCE_HASH = "sha256:" + "d" * 64


def offer(offer_id: str, *, status: MatchStatus = MatchStatus.AUTO_PUBLISHED) -> Offer:
    return Offer(
        offer_id=offer_id,
        product_id="macbook-air-13-m4-16gb",
        seller_id="apple_au",
        url="https://www.apple.com/au/",
        price_aud=Decimal("1599.00"),
        price_basis=PriceBasis.ADVERTISED,
        in_stock=True,
        stock_status=StockStatus.IN_STOCK,
        confidence=Decimal("1.00"),
        match_method=MatchMethod.APPLE_SKU,
        match_status=status,
        verification_status=VerificationStatus.VERIFIED,
        captured_at=CAPTURED_AT,
        source_hash=SOURCE_HASH,
        run_id="run_001",
    )


def test_publishes_apple_au_collection_result(tmp_path: Path) -> None:
    result = CollectionResult(
        offers=[offer("apple-au-offer")],
        errors=[CollectionError(message="ignored product", error_type="parse_error")],
        fetched_at=CAPTURED_AT,
    )

    summary = publish_apple_au_collection(
        result,
        data_dir=tmp_path,
        generated_at=CAPTURED_AT,
    )

    assert summary.collected_offers == 1
    assert summary.published_offers == 1
    assert summary.withheld_offers == 0
    assert summary.collection_errors == 1
    assert summary.policy_blocked is False
    assert summary.products_with_best_price == 1

    latest = json.loads((tmp_path / "latest_offers.json").read_text(encoding="utf-8"))
    assert latest["offers"][0]["offer_id"] == "apple-au-offer"
    assert latest["best_prices"]["macbook-air-13-m4-16gb"]["offer_id"] == "apple-au-offer"


def test_withholds_pending_and_rejected_offers(tmp_path: Path) -> None:
    result = CollectionResult(
        offers=[
            offer("pending", status=MatchStatus.PENDING),
            offer("rejected", status=MatchStatus.REJECTED),
        ],
        fetched_at=CAPTURED_AT,
    )

    summary = publish_apple_au_collection(
        result,
        data_dir=tmp_path,
        generated_at=CAPTURED_AT,
    )

    assert summary.published_offers == 0
    assert summary.withheld_offers == 2
    latest = json.loads((tmp_path / "latest_offers.json").read_text(encoding="utf-8"))
    assert latest["offers"] == []
    assert latest["best_prices"] == {}


def test_policy_blocked_result_publishes_nothing(tmp_path: Path) -> None:
    result = CollectionResult(
        offers=[],
        errors=[],
        fetched_at=CAPTURED_AT,
    )
    result.blocked.append(
        type(
            "BlockedSource",
            (),
            {},
        )
    )

    summary = publish_apple_au_collection(
        result,
        data_dir=tmp_path,
        generated_at=CAPTURED_AT,
    )

    assert summary.policy_blocked is True
    assert summary.published_offers == 0


def test_run_apple_au_uses_adapter_result(tmp_path: Path) -> None:
    class FakeAdapter:
        seller_id = "apple_au"

        def collect(self, targets):
            assert targets == ["product"]
            return CollectionResult(
                offers=[offer("apple-au-offer")],
                fetched_at=CAPTURED_AT,
            )

    summary = run_apple_au(
        FakeAdapter(),
        ["product"],
        data_dir=tmp_path,
        generated_at=CAPTURED_AT,
    )

    assert summary.published_offers == 1
    assert (tmp_path / "latest_offers.json").is_file()
    assert (tmp_path / "health" / "match_confidence.json").is_file()
