"""Domain model tests: valid round-trips and every documented rejection rule.

These tests exercise the models directly, not the validation CLI. Money must
stay decimal, timestamps must stay UTC-aware, and the discount arithmetic must
prove itself on every model that carries a percentage.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from scripts.models import (
    Deal,
    DealType,
    EducationOffer,
    GiftCardOffer,
    MatchCandidate,
    Offer,
    Product,
    PurchaseRoute,
    RunManifest,
    Seller,
    SellerType,
    format_utc,
)

SHA = "sha256:" + "a" * 64
SECOND_SHA = "sha256:" + "b" * 64


def valid_product(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "product_id": "macbook-air-13-m4-16-512",
        "apple_sku": "MW1H3X/A",
        "gtin": "0194254000000",
        "category": "mac",
        "family": "MacBook Air",
        "name": "MacBook Air 13-inch (M4, 16GB, 512GB)",
        "model": "MacBook Air 13-inch M4",
        "configuration": {"chip": "M4", "memory": "16GB", "storage": "512GB"},
        "apple_au_url": "https://www.apple.com/au/shop/buy-mac/macbook-air/13-inch-m4",
        "apple_rrp_aud": "1999.00",
        "education_eligible": True,
        "status": "active",
        "priority": "high",
        "specs_source_url": "https://www.apple.com/au/macbook-air/specs/",
        "rrp_verification": "verified",
        "created_at": "2026-11-01T00:00:00Z",
        "updated_at": "2026-11-10T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def valid_seller(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "seller_id": "apple_au",
        "display_name": "Apple Australia",
        "seller_type": "apple_direct",
        "website": "https://www.apple.com/au/",
        "product_search_url": "https://www.apple.com/au/store",
        "country": "AU",
        "supported_regions": ["AU"],
        "categories": ["mac", "ipad", "iphone", "accessory"],
        "collection_method": "automated_jsonld",
        "rate_limit_rps": 0.5,
        "crawl_delay_seconds": 3,
        "authorised_reseller": True,
        "authorised_evidence_url": "https://www.apple.com/au/",
        "warranty_policy_url": "https://www.apple.com/au/legal/warranty/",
        "returns_policy_url": "https://www.apple.com/au/shop/help/returns_refund",
        "delivery_policy_url": "https://www.apple.com/au/shop/help/shipping_delivery",
        "trust_score_inputs": {"brand_recognition": 1.0, "returns_clarity": 1.0},
        "adapter_module": "scripts.collectors.apple_au",
        "enabled": True,
        "notes": "Apple's own storefront.",
    }
    payload.update(overrides)
    return payload


def valid_offer(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "offer_id": "ofr-apple-au-macbook-air-13-m4-20261115",
        "product_id": "macbook-air-13-m4-16-512",
        "seller_id": "apple_au",
        "url": "https://www.apple.com/au/shop/buy-mac/macbook-air/13-inch-m4",
        "price_aud": "1999.00",
        "was_price_aud": None,
        "rrp_aud": "1999.00",
        "price_basis": "advertised",
        "in_stock": True,
        "stock_status": "in_stock",
        "delivery_cost_aud": "0.00",
        "delivery_cost_verified": True,
        "delivery_estimate": "Free delivery",
        "pickup_available": True,
        "pickup_locations": ["Melbourne CBD"],
        "promotion": None,
        "promotion_type": None,
        "bundle": False,
        "bundle_contents": [],
        "confidence": "1.0",
        "match_method": "apple_sku",
        "match_status": "auto_published",
        "verification_status": "verified",
        "freshness_max_age_hours": 24,
        "captured_at": "2026-11-15T08:00:00Z",
        "source_hash": SHA,
        "run_id": "run-20261115T0800",
        "grey_import": False,
        "sold_by": "Apple Pty Ltd",
    }
    payload.update(overrides)
    return payload


def valid_education(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "offer_id": "edu-apple-au-macbook-air-13-m4-20261115",
        "product_id": "macbook-air-13-m4-16-512",
        "seller_id": "apple_education_au",
        "url": "https://www.apple.com/au-edu/shop/buy-mac/macbook-air/13-inch-m4",
        "price_aud": "1819.00",
        "standard_price_aud": "1999.00",
        "saving_aud": "180.00",
        "saving_pct": "9.00",
        "eligibility": ["University students", "Teachers"],
        "verification": "Apple AU Education Store price page",
        "annual_limit_summary": "One Mac per eligible buyer per year",
        "annual_limit_source_url": "https://www.apple.com/au/shop/browse/open/salespolicies/edu",
        "in_stock": True,
        "price_basis": "edu",
        "verification_status": "verified",
        "freshness_max_age_hours": 24,
        "captured_at": "2026-11-15T08:00:00Z",
        "source_hash": SHA,
        "run_id": "run-20261115T0800",
    }
    payload.update(overrides)
    return payload


def valid_gift_card(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "offer_id": "gift-woolworths-apple-500-20261115",
        "card_type": "retailer_gift_card",
        "seller_id": "woolworths",
        "loyalty_program": "Everyday Rewards",
        "channel": "both",
        "face_value_aud": "500.00",
        "purchase_price_aud": "475.00",
        "bonus_points_per_dollar": "10",
        "points_earned": "4750",
        "assumed_point_value_aud": "0.005",
        "effective_value_aud": "451.25",
        "effective_discount_pct": "9.75",
        "daily_limit_cards": 5,
        "starts_at": "2026-11-13T00:00:00Z",
        "ends_at": "2026-11-26T23:59:59Z",
        "source_url": "https://www.woolworths.com.au/shop/productdetails/12345",
        "verification_status": "verified",
        "evidence_snapshot": "data/snapshots/woolworths-20261115T0800.html",
        "terms_url": "https://www.woolworths.com.au/terms",
        "risks": ["Excludes Apple products on promotion", "Points value may change"],
    }
    payload.update(overrides)
    return payload


def valid_deal(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "deal_id": "deal-jbhifi-macbook-air-13-m4-20261115",
        "deal_type": "price_drop",
        "product_id": "macbook-air-13-m4-16-512",
        "seller_id": "apple_au",
        "offer_id": None,
        "current_price_aud": "1899.00",
        "reference_price_aud": "1999.00",
        "reference_type": "rrp",
        "saving_aud": "100.00",
        "saving_pct": "5.00",
        "effective_price_aud": None,
        "confidence": "1.0",
        "evidence_url": "https://www.apple.com/au/shop/buy-mac/macbook-air/13-inch-m4",
        "captured_at": "2026-11-15T08:00:00Z",
        "expires_at": None,
        "verification_status": "verified",
        "rule_version": "2026-11-01",
    }
    payload.update(overrides)
    return payload


def valid_candidate(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "candidate_id": "cand-jbhifi-20261115-001",
        "seller_id": "apple_au",
        "raw_title": "MacBook Air 13 inch M4 512GB Midnight",
        "raw_price_aud": "1899.00",
        "proposed_product_id": "macbook-air-13-m4-16-512",
        "method": "fuzzy",
        "confidence": "0.72",
        "config_diff": ["storage: 512GB == 512GB"],
        "status": "pending",
        "source_hash": SHA,
        "captured_at": "2026-11-15T08:00:00Z",
        "decided_by": None,
        "decided_at": None,
    }
    payload.update(overrides)
    return payload


def valid_run(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": "run-20261115T0800",
        "adapter": "scripts.collectors.apple_au",
        "started_at": "2026-11-15T08:00:00Z",
        "finished_at": "2026-11-15T08:02:30Z",
        "fetched": 12,
        "parsed": 12,
        "matched": 11,
        "rejected": 1,
        "blocked_by_policy": 0,
        "errors": [
            {
                "product_id": "macbook-air-13-m4-16-512",
                "url": "https://www.apple.com/au/shop/buy-mac/macbook-air/13-inch-m4",
                "message": "JSON-LD block missing; fell back to HTML",
                "error_type": "parse_fallback",
            }
        ],
    }
    payload.update(overrides)
    return payload


def valid_route(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "route_type": "apple_education",
        "label": "Apple Education Store",
        "effective_price_aud": "1819.00",
        "saving_vs_rrp_aud": "180.00",
        "requires": ["Student or teacher eligibility", "Apple Education Store verification"],
        "warnings": ["Annual purchase limit applies"],
        "confidence": "0.95",
        "source_urls": [
            "https://www.apple.com/au-edu/shop/buy-mac/macbook-air/13-inch-m4",
            "https://www.apple.com/au/shop/browse/open/salespolicies/edu",
        ],
        "rank": 1,
    }
    payload.update(overrides)
    return payload


def test_every_model_imports_from_the_package() -> None:
    models = (
        Product,
        Seller,
        Offer,
        EducationOffer,
        GiftCardOffer,
        Deal,
        MatchCandidate,
        RunManifest,
        PurchaseRoute,
    )
    assert all(model.model_json_schema()["type"] == "object" for model in models)


def test_product_round_trip_preserves_decimal_money_and_configuration() -> None:
    product = Product.model_validate(valid_product())

    assert isinstance(product.apple_rrp_aud, Decimal)
    assert product.apple_rrp_aud == Decimal("1999.00")
    assert product.configuration["storage"] == "512GB"
    assert product.brand == "Apple"

    payload = json.loads(product.model_dump_json())
    assert payload["apple_rrp_aud"] == 1999.0
    assert isinstance(payload["apple_rrp_aud"], float)
    assert payload["created_at"] == "2026-11-01T00:00:00Z"
    assert Product.model_validate_json(product.model_dump_json()) == product


def test_offer_round_trip_keeps_utc_timestamp_and_hash() -> None:
    offer = Offer.model_validate(valid_offer())

    assert offer.captured_at.tzinfo is UTC
    assert isinstance(offer.price_aud, Decimal)
    payload = json.loads(offer.model_dump_json())
    assert payload["captured_at"].endswith("Z")
    assert payload["source_hash"] == SHA
    assert payload["price_aud"] == 1999.0
    assert isinstance(payload["price_aud"], float)
    assert isinstance(payload["delivery_cost_aud"], float)
    assert Offer.model_validate_json(offer.model_dump_json()) == offer


def test_education_offer_round_trip() -> None:
    education = EducationOffer.model_validate(valid_education())

    assert education.saving_pct == Decimal("9.00")
    assert education.price_basis.value == "edu"
    assert EducationOffer.model_validate_json(education.model_dump_json()) == education


def test_gift_card_offer_round_trip_and_discount_arithmetic() -> None:
    gift_card = GiftCardOffer.model_validate(valid_gift_card())

    expected = (gift_card.face_value_aud - gift_card.effective_value_aud) / gift_card.face_value_aud
    assert gift_card.effective_discount_pct == (expected * 100).quantize(Decimal("0.01"))
    assert GiftCardOffer.model_validate_json(gift_card.model_dump_json()) == gift_card


def test_deal_round_trip() -> None:
    deal = Deal.model_validate(valid_deal())

    assert deal.saving_aud == Decimal("100.00")
    assert deal.deal_type is DealType.PRICE_DROP
    assert Deal.model_validate_json(deal.model_dump_json()) == deal


def test_seller_accepts_legacy_rate_limit_key_and_curated_sellers() -> None:
    legacy = valid_seller(rate_limit_rps=0.33)
    seller = Seller.model_validate(legacy)
    assert seller.rate_limit_rps == 0.33
    assert "rate_limit_rps" in seller.model_dump()

    curated = valid_seller(
        seller_id="jbhifi",
        seller_type="national_reseller",
        collection_method="curated",
        rate_limit_rps=0.0,
        adapter_module="",
        categories=["mac", "ipad", "gift_card", "business"],
    )
    assert Seller.model_validate(curated).adapter_module == ""


@pytest.mark.parametrize("price", ["-0.01", -1, Decimal("-1999.00")])
def test_negative_price_is_rejected(price: object) -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        Offer.model_validate(valid_offer(price_aud=price))


def test_non_numeric_price_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not a valid decimal amount"):
        Offer.model_validate(valid_offer(price_aud="not-a-price"))


@pytest.mark.parametrize(
    "price",
    [{"amount": "1999.00", "currency": "USD"}, "US$1,999.00", "1999.00 EUR"],
)
def test_non_aud_currency_is_rejected(price: object) -> None:
    with pytest.raises(ValidationError, match="only AUD is supported"):
        GiftCardOffer.model_validate(valid_gift_card(face_value_aud=price))


def test_three_decimal_places_are_rejected() -> None:
    with pytest.raises(ValidationError, match="at most 2 decimals"):
        Offer.model_validate(valid_offer(price_aud="1999.001"))


def test_confidence_above_one_is_rejected() -> None:
    with pytest.raises(ValidationError, match="confidence must be between 0 and 1"):
        Offer.model_validate(valid_offer(confidence=1.5))


def test_confidence_below_zero_is_rejected() -> None:
    with pytest.raises(ValidationError, match="confidence must be between 0 and 1"):
        MatchCandidate.model_validate(valid_candidate(confidence="-0.1"))


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Offer.model_validate(valid_offer(captured_at="2026-11-15T08:00:00"))


@pytest.mark.parametrize("bad_hash", ["md5:abc123", "sha256:XYZ", "sha256:", "deadbeef"])
def test_bad_source_hash_is_rejected(bad_hash: str) -> None:
    with pytest.raises(ValidationError, match="source_hash must be"):
        Offer.model_validate(valid_offer(source_hash=bad_hash))


def test_gift_card_with_no_risks_is_rejected() -> None:
    with pytest.raises(ValidationError):
        GiftCardOffer.model_validate(valid_gift_card(risks=[]))


def test_education_discount_mismatch_is_rejected() -> None:
    with pytest.raises(ValidationError, match="saving_pct"):
        EducationOffer.model_validate(valid_education(saving_pct="8.50"))


def test_education_saving_amount_mismatch_is_rejected() -> None:
    with pytest.raises(ValidationError, match="saving_aud"):
        EducationOffer.model_validate(valid_education(saving_aud="150.00"))


def test_gift_card_discount_mismatch_is_rejected() -> None:
    with pytest.raises(ValidationError, match="effective_discount_pct"):
        GiftCardOffer.model_validate(valid_gift_card(effective_discount_pct="5.00"))


def test_product_without_rrp_cannot_claim_verified() -> None:
    with pytest.raises(ValidationError, match="rrp_verification cannot be 'verified'"):
        Product.model_validate(valid_product(apple_rrp_aud=None, rrp_verification="verified"))


def test_verified_rrp_requires_evidence_url() -> None:
    with pytest.raises(ValidationError, match="specs_source_url"):
        Product.model_validate(valid_product(specs_source_url=None))


def test_unknown_enum_value_is_rejected() -> None:
    with pytest.raises(ValidationError, match="category"):
        Product.model_validate(valid_product(category="vision"))
    with pytest.raises(ValidationError, match="stock_status"):
        Offer.model_validate(valid_offer(stock_status="plenty"))


def test_low_confidence_match_cannot_auto_publish() -> None:
    with pytest.raises(ValidationError, match="auto_published"):
        Offer.model_validate(valid_offer(confidence="0.50", match_status="auto_published"))
    with pytest.raises(ValidationError, match="auto_published"):
        MatchCandidate.model_validate(
            valid_candidate(
                confidence="0.50",
                status="auto_published",
                decided_by="apple-data",
                decided_at="2026-11-15T09:00:00Z",
            )
        )


def test_fuzzy_match_cannot_auto_publish() -> None:
    with pytest.raises(ValidationError, match="fuzzy"):
        MatchCandidate.model_validate(
            valid_candidate(
                status="auto_published",
                confidence="0.99",
                decided_by="apple-data",
                decided_at="2026-11-15T09:00:00Z",
            )
        )


def test_pending_candidate_cannot_carry_a_decision() -> None:
    with pytest.raises(ValidationError, match="pending"):
        MatchCandidate.model_validate(valid_candidate(decided_by="apple-data"))


def test_approved_candidate_requires_a_decider() -> None:
    with pytest.raises(ValidationError, match="requires decided_by"):
        MatchCandidate.model_validate(valid_candidate(status="approved"))


def test_out_of_stock_cannot_claim_in_stock() -> None:
    with pytest.raises(ValidationError, match="out_of_stock"):
        Offer.model_validate(valid_offer(in_stock=True, stock_status="out_of_stock"))


def test_was_price_below_price_is_rejected() -> None:
    with pytest.raises(ValidationError, match="was_price_aud"):
        Offer.model_validate(valid_offer(was_price_aud="1850.00"))


def test_bundle_requires_contents() -> None:
    with pytest.raises(ValidationError, match="bundle_contents"):
        Offer.model_validate(valid_offer(bundle=True))


def test_deal_saving_mismatch_is_rejected() -> None:
    with pytest.raises(ValidationError, match="saving_aud"):
        Deal.model_validate(valid_deal(saving_aud="50.00"))


def test_deal_cannot_measure_against_a_lower_price() -> None:
    with pytest.raises(ValidationError, match="not a deal"):
        Deal.model_validate(
            valid_deal(
                current_price_aud="2099.00",
                reference_price_aud="1999.00",
                saving_aud="0.00",
                saving_pct="0.00",
            )
        )


def test_run_manifest_rejects_impossible_counts_and_order() -> None:
    with pytest.raises(ValidationError, match="parsed cannot exceed fetched"):
        RunManifest.model_validate(valid_run(fetched=1, parsed=2))
    with pytest.raises(ValidationError, match="finished_at"):
        RunManifest.model_validate(valid_run(finished_at="2026-11-15T07:59:00Z"))


def test_purchase_route_requires_sources_and_positive_rank() -> None:
    with pytest.raises(ValidationError, match="source_urls"):
        PurchaseRoute.model_validate(valid_route(source_urls=[]))
    with pytest.raises(ValidationError, match="rank"):
        PurchaseRoute.model_validate(valid_route(rank=0))


def test_enum_members_are_the_documented_contract() -> None:
    assert {member.value for member in SellerType} == {
        "apple_direct",
        "national_reseller",
        "specialist_reseller",
        "online_marketplace",
        "telco",
        "business_reseller",
        "education_reseller",
        "gift_card_seller",
    }
    assert format_utc(datetime(2026, 11, 15, 8, 0, tzinfo=UTC)) == "2026-11-15T08:00:00Z"


def test_mutating_a_payload_does_not_leak_between_examples() -> None:
    first = valid_offer()
    second = copy.deepcopy(first)
    second["price_aud"] = "1.00"
    assert Offer.model_validate(first).price_aud == Decimal("1999.00")
    assert Offer.model_validate(second).price_aud == Decimal("1.00")
