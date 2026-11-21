"""Layered seller-listing to catalogue-product matching.

The layer order is deliberate and must not change without a migration:

1. Apple SKU
2. GTIN/EAN
3. Normalised configuration
4. Fuzzy title
5. Manual review

A SKU match never falls through to a weaker layer. Fuzzy matches are always
withheld for human review, while configuration matches auto-publish only when
their confidence reaches the domain's 0.85 threshold.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from scripts.models.common import AUTO_PUBLISH_MIN_CONFIDENCE
from scripts.models.enums import MatchMethod, MatchStatus
from scripts.models.matching import MatchCandidate
from scripts.models.product import Product
from scripts.matching.normalise import (
    configuration_diff,
    configuration_similarity,
    extract_configuration,
    title_similarity,
)

__all__ = ["SellerListing", "match_listing"]

_SKU_CONFIDENCE = Decimal("1.00")
_GTIN_CONFIDENCE = Decimal("0.98")
_CONFIG_BASE_CONFIDENCE = Decimal("0.55")
_FAMILY_BONUS = Decimal("0.10")
_MODEL_BONUS = Decimal("0.15")
_CONFIG_AXIS_BONUS = Decimal("0.05")


def _confidence(value: float | Decimal) -> Decimal:
    score = Decimal(str(value)).quantize(Decimal("0.01"))
    return min(max(score, Decimal("0.00")), Decimal("1.00"))


def _candidate_id(listing: "SellerListing", product_id: str | None, method: MatchMethod) -> str:
    payload = "|".join(
        (
            listing.seller_id,
            listing.raw_title,
            str(listing.raw_price_aud),
            listing.source_hash,
            method.value,
            product_id or "",
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"match_{digest[:24]}"


def _candidate(
    listing: "SellerListing",
    method: MatchMethod,
    confidence: Decimal,
    product: Product | None,
    config_diff: list[str],
) -> MatchCandidate:
    if product is None:
        status = MatchStatus.PENDING
    elif method is MatchMethod.FUZZY:
        status = MatchStatus.PENDING
    elif confidence >= AUTO_PUBLISH_MIN_CONFIDENCE:
        status = MatchStatus.AUTO_PUBLISHED
    else:
        status = MatchStatus.PENDING

    return MatchCandidate(
        candidate_id=_candidate_id(listing, product.product_id if product else None, method),
        seller_id=listing.seller_id,
        raw_title=listing.raw_title,
        raw_price_aud=listing.raw_price_aud,
        proposed_product_id=product.product_id if product else None,
        method=method,
        confidence=confidence,
        config_diff=config_diff,
        status=status,
        source_hash=listing.source_hash,
        captured_at=listing.captured_at,
    )


@dataclass(frozen=True, slots=True)
class SellerListing:
    """A normalised seller observation awaiting a catalogue match."""

    seller_id: str
    raw_title: str
    raw_price_aud: Decimal
    source_hash: str
    captured_at: datetime
    apple_sku: str | None = None
    gtin: str | None = None
    configuration: dict[str, str] | None = None

    def effective_configuration(self) -> dict[str, str]:
        return self.configuration if self.configuration is not None else extract_configuration(
            self.raw_title
        )


def _sku_matches(listing: SellerListing, products: Sequence[Product]) -> list[Product]:
    if not listing.apple_sku:
        return []
    return [product for product in products if product.apple_sku == listing.apple_sku]


def _gtin_matches(listing: SellerListing, products: Sequence[Product]) -> list[Product]:
    if not listing.gtin:
        return []
    return [product for product in products if product.gtin == listing.gtin]


def _config_matches(listing: SellerListing, products: Sequence[Product]) -> list[tuple[Product, Decimal, list[str]]]:
    listing_configuration = listing.effective_configuration()
    listing_title = listing.raw_title.casefold()
    results: list[tuple[Product, Decimal, list[str]]] = []

    for product in products:
        if product.family.casefold() not in listing_title:
            continue
        if product.model.casefold() not in listing_title:
            continue

        product_configuration = product.configuration
        similarity = configuration_similarity(listing_configuration, product_configuration)
        matched_axes = sum(
            listing_configuration.get(key) == product_configuration.get(key)
            for key in set(listing_configuration) & set(product_configuration)
        )
        confidence = (
            _CONFIG_BASE_CONFIDENCE
            + _FAMILY_BONUS
            + _MODEL_BONUS
            + (_CONFIG_AXIS_BONUS * matched_axes)
            + (_CONFIG_AXIS_BONUS * similarity)
        )
        confidence = _confidence(confidence)
        results.append(
            (
                product,
                confidence,
                configuration_diff(listing_configuration, product_configuration),
            )
        )

    return sorted(results, key=lambda item: (-item[1], item[0].product_id))


def _fuzzy_matches(listing: SellerListing, products: Sequence[Product]) -> list[tuple[Product, Decimal]]:
    results: list[tuple[Product, Decimal]] = []
    for product in products:
        score = title_similarity(listing.raw_title, product.name)
        if score <= 0:
            continue
        results.append((product, _confidence(score)))
    return sorted(results, key=lambda item: (-item[1], item[0].product_id))


def match_listing(listing: SellerListing, products: Sequence[Product]) -> MatchCandidate:
    """Match one seller listing against the product catalogue.

    The result is deterministic: identical inputs produce an identical
    candidate, including its identifier, confidence and configuration diff.
    """
    sku_matches = _sku_matches(listing, products)
    if sku_matches:
        product = sorted(sku_matches, key=lambda item: item.product_id)[0]
        return _candidate(listing, MatchMethod.APPLE_SKU, _SKU_CONFIDENCE, product, [])

    gtin_matches = _gtin_matches(listing, products)
    if gtin_matches:
        product = sorted(gtin_matches, key=lambda item: item.product_id)[0]
        return _candidate(listing, MatchMethod.GTIN, _GTIN_CONFIDENCE, product, [])

    config_matches = _config_matches(listing, products)
    if config_matches:
        product, confidence, differences = config_matches[0]
        return _candidate(listing, MatchMethod.CONFIG, confidence, product, differences)

    fuzzy_matches = _fuzzy_matches(listing, products)
    if fuzzy_matches:
        product, confidence = fuzzy_matches[0]
        return _candidate(listing, MatchMethod.FUZZY, confidence, product, [])

    return _candidate(listing, MatchMethod.MANUAL, Decimal("0.00"), None, [])
