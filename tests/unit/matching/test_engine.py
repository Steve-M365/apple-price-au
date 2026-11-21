"""Tests for the layered product matching engine."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scripts.matching.engine import SellerListing, match_listing
from scripts.models.enums import Category, MatchMethod, MatchStatus
from scripts.models.product import Product

CAPTURED_AT = datetime(2026, 11, 22, 0, 0, tzinfo=UTC)
SOURCE_HASH = "sha256:" + "a" * 64


def product(
    product_id: str,
    *,
    apple_sku: str | None = None,
    gtin: str | None = None,
    configuration: dict[str, str] | None = None,
    name: str = "MacBook Air 13 M4",
) -> Product:
    return Product(
        product_id=product_id,
        apple_sku=apple_sku,
        gtin=gtin,
        category=Category.MAC,
        family="MacBook Air",
        name=name,
        model="MacBook Air",
        configuration=configuration or {},
        apple_au_url="https://www.apple.com/au/",
        created_at=CAPTURED_AT,
        updated_at=CAPTURED_AT,
    )


def listing(**overrides: object) -> SellerListing:
    values: dict[str, object] = {
        "seller_id": "apple_au",
        "raw_title": "MacBook Air 13 M4 16GB Midnight",
        "raw_price_aud": Decimal("1599.00"),
        "source_hash": SOURCE_HASH,
        "captured_at": CAPTURED_AT,
    }
    values.update(overrides)
    return SellerListing(**values)  # type: ignore[arg-type]


def test_apple_sku_match_has_maximum_confidence() -> None:
    catalogue = [product("macbook-air-13-m4-16gb", apple_sku="MCDA4X/A")]
    candidate = match_listing(listing(apple_sku="MCDA4X/A"), catalogue)

    assert candidate.method is MatchMethod.APPLE_SKU
    assert candidate.confidence == Decimal("1.00")
    assert candidate.status is MatchStatus.AUTO_PUBLISHED
    assert candidate.proposed_product_id == "macbook-air-13-m4-16gb"


def test_sku_match_never_falls_through_to_fuzzy() -> None:
    catalogue = [
        product("wrong-product", name="iPhone 16 Pro"),
        product("macbook-air-13-m4-16gb", apple_sku="MCDA4X/A"),
    ]
    candidate = match_listing(listing(apple_sku="MCDA4X/A"), catalogue)

    assert candidate.method is MatchMethod.APPLE_SKU
    assert candidate.proposed_product_id == "macbook-air-13-m4-16gb"


def test_gtin_match_auto_publishes() -> None:
    catalogue = [product("macbook-air-13-m4-16gb", gtin="0195949012345")]
    candidate = match_listing(listing(gtin="0195949012345"), catalogue)

    assert candidate.method is MatchMethod.GTIN
    assert candidate.confidence == Decimal("0.98")
    assert candidate.status is MatchStatus.AUTO_PUBLISHED


def test_exact_configuration_match_auto_publishes() -> None:
    catalogue = [
        product(
            "macbook-air-13-m4-16gb",
            configuration={"memory": "16GB", "colour": "midnight"},
        )
    ]
    candidate = match_listing(
        listing(
            configuration={"memory": "16GB", "colour": "midnight"},
        ),
        catalogue,
    )

    assert candidate.method is MatchMethod.CONFIG
    assert candidate.confidence >= Decimal("0.85")
    assert candidate.status is MatchStatus.AUTO_PUBLISHED
    assert candidate.config_diff == []


def test_configuration_mismatch_is_withheld() -> None:
    catalogue = [
        product(
            "macbook-air-13-m4-16gb",
            configuration={"memory": "16GB", "colour": "midnight"},
        )
    ]
    candidate = match_listing(
        listing(
            configuration={"memory": "16GB", "colour": "starlight"},
        ),
        catalogue,
    )

    assert candidate.method is MatchMethod.CONFIG
    assert candidate.status is MatchStatus.PENDING
    assert candidate.config_diff == ["colour: 'starlight' != 'midnight'"]


def test_fuzzy_match_is_always_pending() -> None:
    catalogue = [product("macbook-air-13-m4-16gb", name="MacBook Air 13 M4 16GB Midnight")]
    candidate = match_listing(listing(), catalogue)

    assert candidate.method is MatchMethod.FUZZY
    assert candidate.status is MatchStatus.PENDING


def test_no_match_requires_manual_review() -> None:
    candidate = match_listing(listing(raw_title="Unknown Apple Accessory"), [])

    assert candidate.method is MatchMethod.MANUAL
    assert candidate.status is MatchStatus.PENDING
    assert candidate.proposed_product_id is None
    assert candidate.confidence == Decimal("0.00")


def test_matching_is_deterministic() -> None:
    catalogue = [
        product(
            "macbook-air-13-m4-16gb",
            configuration={"memory": "16GB", "colour": "midnight"},
        )
    ]
    first = match_listing(listing(), catalogue)
    second = match_listing(listing(), catalogue)

    assert first == second


def test_invalid_low_confidence_auto_publish_is_rejected() -> None:
    catalogue = [product("macbook-air-13-m4-16gb")]
    candidate = match_listing(listing(), catalogue)

    with pytest.raises(ValueError):
        candidate.model_copy(
            update={
                "method": MatchMethod.CONFIG,
                "confidence": Decimal("0.50"),
                "status": MatchStatus.AUTO_PUBLISHED,
            }
        )
