"""Assemble the published offer set for the Orchard static site.

Only offers with an explicit publishable match decision are included. Best
prices are derived from the offers themselves, never hand-written, and output
ordering is stable so repeated runs do not create repository diff noise.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from scripts.models.common import format_utc
from scripts.models.enums import MatchStatus
from scripts.models.offer import Offer

__all__ = ["PublicationResult", "publish_offers"]

_PUBLISHABLE_STATUSES = frozenset(
    {
        MatchStatus.AUTO_PUBLISHED,
        MatchStatus.APPROVED,
    }
)


def _atomic_write_json(path: Path, payload: object) -> None:
    """Write JSON atomically so a failed run cannot leave a partial feed."""
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


def _offer_payload(offer: Offer) -> dict[str, object]:
    return offer.model_dump(mode="json", exclude_none=True)


@dataclass(frozen=True, slots=True)
class PublicationResult:
    """Counts and paths from one publication run."""

    input_offers: int
    published_offers: int
    withheld_offers: int
    products_with_best_price: int
    latest_offers_path: Path
    confidence_health_path: Path

    def as_dict(self) -> dict[str, object]:
        return {
            "input_offers": self.input_offers,
            "published_offers": self.published_offers,
            "withheld_offers": self.withheld_offers,
            "products_with_best_price": self.products_with_best_price,
            "latest_offers_path": self.latest_offers_path.as_posix(),
            "confidence_health_path": self.confidence_health_path.as_posix(),
        }


def publish_offers(
    offers: Sequence[Offer],
    *,
    latest_offers_path: Path,
    confidence_health_path: Path,
    generated_at: datetime | None = None,
) -> PublicationResult:
    """Publish validated offers and derive best prices deterministically.

    Offers with ``PENDING`` or ``REJECTED`` match status are withheld. Offers
    are sorted by product, landed price and offer id, making the output stable
    across repeated runs with unchanged inputs.
    """
    generated = generated_at or datetime.now(UTC)
    publishable = sorted(
        (offer for offer in offers if offer.match_status in _PUBLISHABLE_STATUSES),
        key=lambda offer: (
            offer.product_id,
            _landed_price(offer),
            offer.offer_id,
        ),
    )

    best_prices: dict[str, dict[str, object]] = {}
    offers_by_product: dict[str, list[Offer]] = defaultdict(list)
    for offer in publishable:
        offers_by_product[offer.product_id].append(offer)

    for product_id, product_offers in sorted(offers_by_product.items()):
        best = product_offers[0]
        best_prices[product_id] = {
            "offer_id": best.offer_id,
            "seller_id": best.seller_id,
            "price_aud": float(best.price_aud),
            "delivery_cost_aud": float(best.delivery_cost_aud),
            "total_price_aud": float(_landed_price(best)),
            "captured_at": format_utc(best.captured_at),
            "url": str(best.url),
        }

    confidence_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for offer in publishable:
        confidence_counts[offer.seller_id][str(offer.confidence)] += 1

    seller_confidence = {
        seller_id: {
            "offer_count": sum(counts.values()),
            "confidence_distribution": dict(sorted(counts.items())),
        }
        for seller_id, counts in sorted(confidence_counts.items())
    }

    latest_payload = {
        "offers": [_offer_payload(offer) for offer in publishable],
        "best_prices": best_prices,
    }
    health_payload = {
        "generated_at": format_utc(generated),
        "sellers": seller_confidence,
    }

    _atomic_write_json(latest_offers_path, latest_payload)
    _atomic_write_json(confidence_health_path, health_payload)

    return PublicationResult(
        input_offers=len(offers),
        published_offers=len(publishable),
        withheld_offers=len(offers) - len(publishable),
        products_with_best_price=len(best_prices),
        latest_offers_path=latest_offers_path,
        confidence_health_path=confidence_health_path,
    )
