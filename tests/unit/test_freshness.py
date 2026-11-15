"""Freshness policy tests.

The policy is the project's promise that a stale price is never shown as
current, so these tests pin the boundaries exactly: one second matters.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.models.enums import VerificationStatus
from scripts.validation.freshness import (
    FreshnessConfig,
    FreshnessVerdict,
    effective_verification,
    load_freshness_config,
    measure_age_hours,
    resolve_threshold_hours,
)

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[2]

# Higher rank means the price may be presented with more confidence.
VERIFICATION_RANK = {
    VerificationStatus.UNVERIFIED: 0,
    VerificationStatus.INDICATIVE: 1,
    VerificationStatus.CURATED_UNVERIFIED: 2,
    VerificationStatus.VERIFIED: 3,
}


@dataclass(frozen=True)
class StubOffer:
    """Stand-in for Offer/EducationOffer: only freshness fields matter here."""

    captured_at: datetime
    verification_status: VerificationStatus = VerificationStatus.VERIFIED
    freshness_max_age_hours: int = 24


def shipped_config() -> FreshnessConfig:
    config, problems = load_freshness_config(REPO_ROOT / "config" / "freshness.yml")
    assert problems == []
    return config


def verdict(
    *,
    hours_old: float | Decimal = 0,
    stored: VerificationStatus = VerificationStatus.VERIFIED,
    seller_type: str | None = "national_reseller",
    category: str | None = None,
    config: FreshnessConfig | None = None,
    budget: int = 24,
    captured_at: datetime | None = None,
) -> FreshnessVerdict:
    captured = captured_at or (NOW - timedelta(hours=float(hours_old)))
    record = StubOffer(
        captured_at=captured,
        verification_status=stored,
        freshness_max_age_hours=budget,
    )
    return effective_verification(
        record,
        seller_type=seller_type,
        category=category,
        config=config if config is not None else shipped_config(),
        now=NOW,
    )


def test_fresh_offer_keeps_its_stored_status() -> None:
    result = verdict(hours_old=10)

    assert result.status is VerificationStatus.VERIFIED
    assert result.stale is False
    assert result.clock_skew is False
    assert result.age_hours == Decimal(10)
    assert result.threshold_hours == Decimal(36)


def test_exactly_at_the_boundary_is_not_stale() -> None:
    result = verdict(hours_old=36)

    assert result.age_hours == Decimal(36)
    assert result.stale is False
    assert result.status is VerificationStatus.VERIFIED


def test_one_second_past_the_boundary_is_stale() -> None:
    result = verdict(captured_at=NOW - timedelta(hours=36, seconds=1))

    assert result.age_hours > Decimal(36)
    assert result.age_hours < Decimal("36.001")
    assert result.stale is True
    assert result.status is VerificationStatus.INDICATIVE
    assert "freshness budget" in result.reason


def test_one_microsecond_past_the_boundary_is_stale() -> None:
    result = verdict(captured_at=NOW - timedelta(hours=36, microseconds=1))

    assert result.age_hours > Decimal(36)
    assert result.stale is True


def test_threshold_zero_never_expires() -> None:
    result = verdict(hours_old=1000, seller_type="business_reseller")

    assert result.threshold_hours == Decimal(0)
    assert result.stale is False
    assert result.status is VerificationStatus.VERIFIED
    assert "no expiry" in result.reason


def test_education_reseller_also_has_no_expiry() -> None:
    result = verdict(hours_old=5000, seller_type="education_reseller")

    assert result.stale is False
    assert result.status is VerificationStatus.VERIFIED


def test_unverified_never_improves_with_age() -> None:
    for hours in (0, 36, 1000):
        result = verdict(hours_old=hours, stored=VerificationStatus.UNVERIFIED)
        assert result.status is VerificationStatus.UNVERIFIED
        assert result.stale is False
        assert "unverified" in result.reason


def test_unverified_also_keeps_the_clock_skew_flag() -> None:
    result = verdict(
        captured_at=NOW + timedelta(hours=2),
        stored=VerificationStatus.UNVERIFIED,
    )

    assert result.clock_skew is True
    assert result.status is VerificationStatus.UNVERIFIED
    assert result.stale is False


def test_future_timestamp_is_clamped_and_flagged() -> None:
    result = verdict(captured_at=NOW + timedelta(hours=2))

    assert result.clock_skew is True
    assert result.age_hours == Decimal(0)
    assert result.stale is False
    assert result.status is VerificationStatus.VERIFIED
    assert "future" in result.reason


def test_by_seller_type_beats_the_default() -> None:
    config = FreshnessConfig(
        default_hours=Decimal(36),
        by_seller_type={"apple_direct": Decimal(48)},
    )

    result = verdict(hours_old=40, seller_type="apple_direct", config=config)

    assert result.threshold_hours == Decimal(48)
    assert result.stale is False
    assert result.status is VerificationStatus.VERIFIED


def test_by_category_beats_by_seller_type() -> None:
    config = FreshnessConfig(
        default_hours=Decimal(36),
        by_seller_type={"apple_direct": Decimal(48)},
        by_category={"accessory": Decimal(72)},
    )

    result = verdict(hours_old=60, seller_type="apple_direct", category="accessory", config=config)

    assert result.threshold_hours == Decimal(72)
    assert result.stale is False


def test_unlisted_seller_type_falls_back_to_the_default() -> None:
    config = FreshnessConfig(default_hours=Decimal(36))

    result = verdict(hours_old=37, seller_type="telco", config=config)

    assert result.threshold_hours == Decimal(36)
    assert result.stale is True


def test_config_silence_falls_back_to_the_offer_budget() -> None:
    result = verdict(hours_old=25, config=FreshnessConfig(), budget=24)

    assert result.threshold_hours == Decimal(24)
    assert result.stale is True
    assert result.status is VerificationStatus.INDICATIVE


@pytest.mark.parametrize(
    ("seller_type", "category", "expected"),
    [
        ("apple_direct", "accessory", Decimal(72)),
        ("apple_direct", None, Decimal(48)),
        ("online_marketplace", None, Decimal(24)),
        (None, None, Decimal(36)),
    ],
)
def test_resolve_threshold_precedence(
    seller_type: str | None, category: str | None, expected: Decimal
) -> None:
    config = shipped_config()

    assert (
        resolve_threshold_hours(
            seller_type=seller_type,
            category=category,
            config=config,
            fallback_hours=12,
        )
        == expected
    )


def test_resolve_falls_back_when_config_has_no_default() -> None:
    assert resolve_threshold_hours(
        seller_type=None,
        category=None,
        config=FreshnessConfig(),
        fallback_hours=12,
    ) == Decimal(12)


def test_stale_record_becomes_indicative() -> None:
    result = verdict(hours_old=100)

    assert result.status is VerificationStatus.INDICATIVE
    assert result.stale is True


def test_indicative_record_stays_indicative_when_stale() -> None:
    result = verdict(hours_old=100, stored=VerificationStatus.INDICATIVE)

    assert result.status is VerificationStatus.INDICATIVE
    assert result.stale is True


def test_curated_unverified_also_becomes_indicative_when_stale() -> None:
    """Freshness outranks curation: a curated price past its budget is indicative."""
    result = verdict(hours_old=100, stored=VerificationStatus.CURATED_UNVERIFIED)

    assert result.status is VerificationStatus.INDICATIVE
    assert result.stale is True


def test_status_is_monotonic_in_age() -> None:
    """Property: a price never becomes more publishable as it gets older."""
    for stored in (VerificationStatus.VERIFIED, VerificationStatus.INDICATIVE):
        previous_rank: int | None = None
        for hours in range(0, 200, 4):
            result = verdict(hours_old=hours, stored=stored)
            rank = VERIFICATION_RANK[result.status]
            if previous_rank is not None:
                assert rank <= previous_rank, f"status improved at {hours} h for {stored}"
            previous_rank = rank


def test_age_grows_monotonically_for_the_same_record() -> None:
    ages = [verdict(hours_old=hours).age_hours for hours in range(0, 80, 2)]

    assert ages == [Decimal(hours) for hours in range(0, 80, 2)]
    assert all(later > earlier for earlier, later in itertools.pairwise(ages))


def test_verdict_exposes_the_threshold_vocabulary() -> None:
    result = verdict(hours_old=10)

    assert result.threshold == result.threshold_hours


def test_measure_age_hours_clamps_future_timestamps() -> None:
    age, clock_skew = measure_age_hours(NOW + timedelta(hours=3), NOW)

    assert age == Decimal(0)
    assert clock_skew is True


def test_measure_age_hours_is_exact_at_a_whole_hour() -> None:
    age, clock_skew = measure_age_hours(NOW - timedelta(hours=36), NOW)

    assert age == Decimal(36)
    assert clock_skew is False


def test_shipped_config_carries_the_documented_values() -> None:
    config = shipped_config()

    assert config.default_hours == Decimal(36)
    assert config.by_seller_type["apple_direct"] == Decimal(48)
    assert config.by_seller_type["national_reseller"] == Decimal(36)
    assert config.by_seller_type["specialist_reseller"] == Decimal(48)
    assert config.by_seller_type["online_marketplace"] == Decimal(24)
    assert config.by_seller_type["gift_card_seller"] == Decimal(24)
    assert config.by_seller_type["business_reseller"] == Decimal(0)
    assert config.by_seller_type["education_reseller"] == Decimal(0)
    assert config.by_category["accessory"] == Decimal(72)
    assert config.by_category["applecare"] == Decimal(72)
    assert config.review_queue_max_age_hours == Decimal(24)


def test_missing_config_file_is_not_an_error(tmp_path: Path) -> None:
    config, problems = load_freshness_config(tmp_path / "freshness.yml")

    assert problems == []
    assert config.default_hours is None
    assert config.by_seller_type == {}


def test_non_mapping_config_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "freshness.yml"
    path.write_text("- apple_direct\n- national_reseller\n", encoding="utf-8")

    config, problems = load_freshness_config(path)

    assert config.default_hours is None
    assert problems and "mapping" in problems[0]


def test_negative_threshold_is_clamped_to_no_expiry(tmp_path: Path) -> None:
    path = tmp_path / "freshness.yml"
    path.write_text("by_seller_type:\n  business_reseller: -5\n", encoding="utf-8")

    config, problems = load_freshness_config(path)

    assert problems == []
    assert config.by_seller_type["business_reseller"] == Decimal(0)
