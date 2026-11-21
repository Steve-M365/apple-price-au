"""Apple AU collection-to-publication pipeline.

This module keeps the collector and publisher loosely coupled: an adapter
returns a :class:`CollectionResult`, and this pipeline publishes only the
offers that already satisfy Orchard's match and confidence rules.

It does not modify collector policy, rate limiting or snapshot behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from scripts.collectors.base import CollectionResult, SourceAdapter
from scripts.matching.publish import publish_offers
from scripts.models.product import Product

__all__ = ["AppleAuPipelineResult", "publish_apple_au_collection", "run_apple_au"]


@dataclass(frozen=True, slots=True)
class AppleAuPipelineResult:
    """A summary of one collection-and-publication run."""

    collected_offers: int
    published_offers: int
    withheld_offers: int
    products_with_best_price: int
    collection_errors: int
    policy_blocked: bool
    latest_offers_path: Path
    confidence_health_path: Path

    def as_dict(self) -> dict[str, object]:
        return {
            "collected_offers": self.collected_offers,
            "published_offers": self.published_offers,
            "withheld_offers": self.withheld_offers,
            "products_with_best_price": self.products_with_best_price,
            "collection_errors": self.collection_errors,
            "policy_blocked": self.policy_blocked,
            "latest_offers_path": self.latest_offers_path.as_posix(),
            "confidence_health_path": self.confidence_health_path.as_posix(),
        }


def publish_apple_au_collection(
    result: CollectionResult,
    *,
    data_dir: Path,
    generated_at: datetime | None = None,
) -> AppleAuPipelineResult:
    """Publish the publishable offers produced by an Apple AU collection run.

    The collector is responsible for policy, fetching, parsing and snapshotting.
    This function is responsible only for turning its already-validated offers
    into the stable published feed and confidence health report.
    """
    latest_offers_path = data_dir / "latest_offers.json"
    confidence_health_path = data_dir / "health" / "match_confidence.json"

    publication = publish_offers(
        result.offers,
        latest_offers_path=latest_offers_path,
        confidence_health_path=confidence_health_path,
        generated_at=generated_at,
    )

    return AppleAuPipelineResult(
        collected_offers=len(result.offers),
        published_offers=publication.published_offers,
        withheld_offers=publication.withheld_offers,
        products_with_best_price=publication.products_with_best_price,
        collection_errors=len(result.errors),
        policy_blocked=result.blocked_by_policy,
        latest_offers_path=latest_offers_path,
        confidence_health_path=confidence_health_path,
    )


def run_apple_au(
    adapter: SourceAdapter,
    products: Sequence[Product],
    *,
    data_dir: Path,
    generated_at: datetime | None = None,
) -> AppleAuPipelineResult:
    """Run one Apple AU adapter and publish its publishable offers.

    The adapter must already be configured with its policy gate, rate limiter
    and HTTP client. This function does not construct network dependencies or
    bypass any collector safeguard.
    """
    result = adapter.collect(list(products))
    return publish_apple_au_collection(
        result,
        data_dir=data_dir,
        generated_at=generated_at or datetime.now(UTC),
    )
