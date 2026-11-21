"""Derive price history from append-only snapshot evidence.

History is calculated only from ``data/snapshots/YYYY-MM-DD/*.json``. It never
reads ``latest_offers.json``, because published offers are a derived view while
snapshots are the evidence layer.

Out-of-stock observations remain in the historical series, but cannot become an
all-time low or the previous available price: an unavailable listing is not a
price drop.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final, Mapping

from pydantic import ValidationError

from scripts.models.common import format_utc
from scripts.models.enums import StockStatus
from scripts.models.offer import Offer

__all__ = ["HistoryResult", "build_history", "write_history"]

_SNAPSHOTS_DIR: Final = "snapshots"
_HISTORY_DIR: Final = "history"


def _atomic_write_json(path: Path, payload: object) -> None:
    """Write JSON atomically so an interrupted run cannot corrupt history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _landed_price(offer: Offer) -> Decimal:
    return offer.price_aud + offer.delivery_cost_aud


def _available(offer: Offer) -> bool:
    return offer.stock_status is not StockStatus.OUT_OF_STOCK


def _point(offer: Offer) -> dict[str, object]:
    return {
        "captured_at": format_utc(offer.captured_at),
        "seller_id": offer.seller_id,
        "offer_id": offer.offer_id,
        "price_aud": float(offer.price_aud),
        "delivery_cost_aud": float(offer.delivery_cost_aud),
        "total_price_aud": float(_landed_price(offer)),
        "stock_status": offer.stock_status.value,
        "in_stock": offer.in_stock,
        "url": str(offer.url),
        "source_hash": offer.source_hash,
    }


def _low(points: list[dict[str, object]]) -> dict[str, object] | None:
    """Return the lowest-priced available observation, with stable tie-breaking."""
    available = [point for point in points if point["stock_status"] != StockStatus.OUT_OF_STOCK.value]
    if not available:
        return None
    return min(
        available,
        key=lambda point: (
            Decimal(str(point["total_price_aud"])),
            str(point["captured_at"]),
            str(point["offer_id"]),
        ),
    )


def _previous_available(points: list[dict[str, object]]) -> dict[str, object] | None:
    """Return the latest available observation before the newest one."""
    available = [
        point
        for point in points
        if point["stock_status"] != StockStatus.OUT_OF_STOCK.value
    ]
    if len(available) < 2:
        return None
    return sorted(
        available,
        key=lambda point: (str(point["captured_at"]), str(point["offer_id"])),
    )[-2]


@dataclass(frozen=True, slots=True)
class HistoryResult:
    """Counts from one history rebuild."""

    snapshot_files: int
    offers_read: int
    products_with_history: int
    history_files_written: int

    def as_dict(self) -> dict[str, int]:
        return {
            "snapshot_files": self.snapshot_files,
            "offers_read": self.offers_read,
            "products_with_history": self.products_with_history,
            "history_files_written": self.history_files_written,
        }


def _read_snapshot(path: Path) -> tuple[list[Offer], int]:
    """Read and validate one snapshot file, returning its offers."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")

    offers_payload = payload.get("offers")
    if not isinstance(offers_payload, list):
        raise ValueError(f"{path} must contain an 'offers' list")

    offers: list[Offer] = []
    for index, record in enumerate(offers_payload):
        try:
            offers.append(Offer.model_validate(record))
        except ValidationError as exc:
            location = ".".join(str(part) for part in exc.errors()[0]["loc"])
            raise ValueError(
                f"{path}[{index}].{location}: {exc.errors()[0]['msg']}"
            ) from exc
    return offers, len(offers_payload)


def build_history(
    snapshots_dir: Path,
    *,
    generated_at: datetime | None = None,
) -> tuple[dict[str, dict[str, object]], HistoryResult]:
    """Build compact per-product history from every committed snapshot."""
    generated = generated_at or datetime.now(UTC)
    snapshot_files = sorted(snapshots_dir.glob("*/*.json")) if snapshots_dir.is_dir() else []

    series_by_product: dict[str, list[dict[str, object]]] = defaultdict(list)
    offers_read = 0

    for path in snapshot_files:
        offers, count = _read_snapshot(path)
        offers_read += count
        for offer in offers:
            series_by_product[offer.product_id].append(_point(offer))

    histories: dict[str, dict[str, object]] = {}
    for product_id, points in series_by_product.items():
        ordered = sorted(
            points,
            key=lambda point: (str(point["captured_at"]), str(point["seller_id"]), str(point["offer_id"])),
        )
        sellers: dict[str, list[dict[str, object]]] = defaultdict(list)
        for point in ordered:
            sellers[str(point["seller_id"])].append(point)

        histories[product_id] = {
            "product_id": product_id,
            "generated_at": format_utc(generated),
            "series": ordered,
            "sellers": {
                seller_id: sorted(points, key=lambda point: str(point["captured_at"]))
                for seller_id, points in sorted(sellers.items())
            },
            "all_time_low": _low(ordered),
            "lowest_ever_across_sellers": _low(ordered),
            "previous_captured_price": _previous_available(ordered),
        }

    return histories, HistoryResult(
        snapshot_files=len(snapshot_files),
        offers_read=offers_read,
        products_with_history=len(histories),
        history_files_written=len(histories),
    )


def write_history(
    snapshots_dir: Path,
    history_dir: Path,
    *,
    generated_at: datetime | None = None,
) -> HistoryResult:
    """Rebuild every product history file from snapshot evidence."""
    histories, result = build_history(snapshots_dir, generated_at=generated_at)
    for product_id, history in histories.items():
        _atomic_write_json(history_dir / f"{product_id}.json", history)
    return result
