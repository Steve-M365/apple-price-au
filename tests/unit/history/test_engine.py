"""Tests for snapshot-derived price history."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scripts.history.engine import build_history, write_history
from scripts.models.enums import (
    MatchMethod,
    MatchStatus,
    PriceBasis,
    StockStatus,
    VerificationStatus,
)
from scripts.models.offer import Offer

DAY_ONE = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
DAY_TWO = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
DAY_THREE = datetime(2026, 11, 22, 0, 0, tzinfo=UTC)
SOURCE_HASH = "sha256:" + "e" * 64


def offer(
    offer_id: str,
    *,
    captured_at: datetime,
    seller_id: str = "apple_au",
    price: str = "1599.00",
    delivery: str = "0.00",
    stock_status: StockStatus = StockStatus.IN_STOCK,
) -> Offer:
    return Offer(
        offer_id=offer_id,
        product_id="macbook-air-13-m4-16gb",
        seller_id=seller_id,
        url="https://www.apple.com/au/",
        price_aud=Decimal(price),
        price_basis=PriceBasis.ADVERTISED,
        in_stock=stock_status is not StockStatus.OUT_OF_STOCK,
        stock_status=stock_status,
        delivery_cost_aud=Decimal(delivery),
        confidence=Decimal("1.00"),
        match_method=MatchMethod.APPLE_SKU,
        match_status=MatchStatus.AUTO_PUBLISHED,
        verification_status=VerificationStatus.VERIFIED,
        captured_at=captured_at,
        source_hash=SOURCE_HASH,
        run_id=f"run_{captured_at.date().isoformat()}",
    )


def write_snapshot(
    root: Path,
    date: str,
    seller_id: str,
    offers: list[Offer],
    captured_at: datetime,
) -> None:
    directory = root / date
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "seller_id": seller_id,
        "run_id": f"run_{date}",
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "captured_date": date,
        "offer_count": len(offers),
        "offers": [json.loads(offer.model_dump_json()) for offer in offers],
    }
    (directory / f"{seller_id}.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def test_history_derives_series_from_snapshots(tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots"
    write_snapshot(
        snapshots,
        "2026-11-20",
        "apple_au",
        [offer("day-one", captured_at=DAY_ONE, price="1699.00")],
        DAY_ONE,
    )
    write_snapshot(
        snapshots,
        "2026-11-21",
        "apple_au",
        [offer("day-two", captured_at=DAY_TWO, price="1599.00")],
        DAY_TWO,
    )

    histories, result = build_history(snapshots, generated_at=DAY_THREE)

    assert result.snapshot_files == 2
    assert result.offers_read == 2
    assert result.products_with_history == 1

    history = histories["macbook-air-13-m4-16gb"]
    assert len(history["series"]) == 2
    assert history["all_time_low"]["offer_id"] == "day-two"
    assert history["previous_captured_price"]["offer_id"] == "day-one"


def test_out_of_stock_does_not_become_all_time_low(tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots"
    write_snapshot(
        snapshots,
        "2026-11-20",
        "apple_au",
        [offer("available", captured_at=DAY_ONE, price="1699.00")],
        DAY_ONE,
    )
    write_snapshot(
        snapshots,
        "2026-11-21",
        "apple_au",
        [
            offer(
                "unavailable",
                captured_at=DAY_TWO,
                price="999.00",
                stock_status=StockStatus.OUT_OF_STOCK,
            )
        ],
        DAY_TWO,
    )

    histories, _ = build_history(snapshots, generated_at=DAY_THREE)
    history = histories["macbook-air-13-m4-16gb"]

    assert history["all_time_low"]["offer_id"] == "available"
    assert history["previous_captured_price"] is None
    assert len(history["series"]) == 2


def test_lowest_ever_across_sellers_uses_landed_price(tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots"
    write_snapshot(
        snapshots,
        "2026-11-20",
        "apple_au",
        [offer("apple", captured_at=DAY_ONE, price="1599.00")],
        DAY_ONE,
    )
    write_snapshot(
        snapshots,
        "2026-11-21",
        "retailer",
        [offer("retailer", captured_at=DAY_TWO, seller_id="retailer", price="1549.00", delivery="25.00")],
        DAY_TWO,
    )

    histories, _ = build_history(snapshots, generated_at=DAY_THREE)
    history = histories["macbook-air-13-m4-16gb"]

    assert history["lowest_ever_across_sellers"]["offer_id"] == "apple"
    assert history["lowest_ever_across_sellers"]["total_price_aud"] == 1599.0


def test_history_output_is_deterministic(tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots"
    history = tmp_path / "history"
    write_snapshot(
        snapshots,
        "2026-11-20",
        "apple_au",
        [offer("day-one", captured_at=DAY_ONE)],
        DAY_ONE,
    )

    first = write_history(snapshots, history, generated_at=DAY_THREE)
    first_payload = (history / "macbook-air-13-m4-16gb.json").read_text(encoding="utf-8")

    second = write_history(snapshots, history, generated_at=DAY_THREE)
    second_payload = (history / "macbook-air-13-m4-16gb.json").read_text(encoding="utf-8")

    assert first.history_files_written == 1
    assert second.history_files_written == 1
    assert first_payload == second_payload


def test_invalid_snapshot_record_is_rejected(tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots"
    day = snapshots / "2026-11-20"
    day.mkdir(parents=True)
    (day / "apple_au.json").write_text(
        json.dumps({"schema_version": 1, "offers": [{"offer_id": "broken"}]}),
        encoding="utf-8",
    )

    try:
        build_history(snapshots, generated_at=DAY_THREE)
    except ValueError as exc:
        assert "broken" in str(exc) or "apple_au.json" in str(exc)
    else:
        raise AssertionError("expected an invalid snapshot to be rejected")
