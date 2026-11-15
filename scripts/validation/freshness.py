"""Executable freshness policy.

The rule "never present an unverified or stale price as current" is enforced
here, not by prose. :func:`effective_verification` turns a captured timestamp
into the verdict a page may render, and the validation CLI calls the same
function so the site contract and the gate cannot drift apart.

Threshold precedence, highest first:

1. ``by_category`` for the product category (an accessory price stays useful
   for longer than a phone price),
2. ``by_seller_type`` for the seller class,
3. ``default_max_age_hours``,
4. the record's own ``freshness_max_age_hours``, used when the config is silent.

A threshold of ``0`` means *no expiry*: quote-only and education routes have no
public price that goes stale, so their stored verdict is never downgraded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol, cast

import yaml

from scripts.models.enums import VerificationStatus

MICROSECONDS_PER_HOUR = Decimal(3_600_000_000)
NO_EXPIRY_HOURS = Decimal(0)
DEFAULT_REVIEW_QUEUE_MAX_AGE_HOURS = Decimal(24)


class FreshnessRecord(Protocol):
    """The fields freshness needs from an offer or an education offer."""

    captured_at: datetime
    verification_status: VerificationStatus
    freshness_max_age_hours: int


@dataclass(frozen=True, slots=True)
class FreshnessConfig:
    """Freshness budgets read from ``config/freshness.yml``."""

    default_hours: Decimal | None = None
    by_seller_type: dict[str, Decimal] = field(default_factory=dict)
    by_category: dict[str, Decimal] = field(default_factory=dict)
    review_queue_max_age_hours: Decimal = DEFAULT_REVIEW_QUEUE_MAX_AGE_HOURS


@dataclass(frozen=True, slots=True)
class FreshnessVerdict:
    """The result of applying the freshness policy to one captured price.

    ``stale`` is the flag the caller acts on: when true the record must render
    as indicative. ``clock_skew`` is true when ``captured_at`` was in the
    future; age is clamped to zero so a bad clock cannot make a price look old.
    """

    status: VerificationStatus
    age_hours: Decimal
    threshold_hours: Decimal
    stale: bool
    clock_skew: bool = False
    reason: str = ""

    @property
    def threshold(self) -> Decimal:
        """The applied budget in hours (alias kept for the policy's vocabulary)."""
        return self.threshold_hours


def measure_age_hours(captured_at: datetime, now: datetime) -> tuple[Decimal, bool]:
    """Return the age in hours, clamped at zero, plus a clock-skew flag.

    Ages are computed from whole microseconds so boundary tests are exact; no
    binary floating point enters the comparison.
    """
    delta = now - captured_at
    microseconds = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    if microseconds < 0:
        return Decimal(0), True
    return Decimal(microseconds) / MICROSECONDS_PER_HOUR, False


def resolve_threshold_hours(
    *,
    seller_type: str | None,
    category: str | None,
    config: FreshnessConfig,
    fallback_hours: int | Decimal,
) -> Decimal:
    """Resolve the freshness budget in hours using the documented precedence."""
    if category is not None:
        by_category = config.by_category.get(category)
        if by_category is not None:
            return _clamp_hours(by_category)
    if seller_type is not None:
        by_seller_type = config.by_seller_type.get(seller_type)
        if by_seller_type is not None:
            return _clamp_hours(by_seller_type)
    if config.default_hours is not None:
        return _clamp_hours(config.default_hours)
    return _clamp_hours(Decimal(str(fallback_hours)))


def effective_verification(
    offer_or_education: FreshnessRecord,
    *,
    seller_type: str | None,
    category: str | None,
    config: FreshnessConfig,
    now: datetime,
) -> FreshnessVerdict:
    """Apply the freshness policy to one captured offer.

    Precedence of the rules themselves:

    1. a record already marked ``unverified`` stays ``unverified`` at any age,
    2. a future ``captured_at`` is clamped to age zero and flagged,
    3. a zero threshold never expires,
    4. within budget the stored status is returned unchanged,
    5. beyond budget the status becomes ``indicative`` and ``stale`` is true.
    """
    stored = offer_or_education.verification_status
    age_hours, clock_skew = measure_age_hours(offer_or_education.captured_at, now)
    threshold = resolve_threshold_hours(
        seller_type=seller_type,
        category=category,
        config=config,
        fallback_hours=offer_or_education.freshness_max_age_hours,
    )

    if stored is VerificationStatus.UNVERIFIED:
        return FreshnessVerdict(
            status=stored,
            age_hours=age_hours,
            threshold_hours=threshold,
            stale=False,
            clock_skew=clock_skew,
            reason="stored status is unverified; age cannot make it publishable",
        )

    if clock_skew:
        return FreshnessVerdict(
            status=stored,
            age_hours=age_hours,
            threshold_hours=threshold,
            stale=False,
            clock_skew=True,
            reason="captured_at is in the future; age clamped to 0",
        )

    if threshold == NO_EXPIRY_HOURS:
        return FreshnessVerdict(
            status=stored,
            age_hours=age_hours,
            threshold_hours=threshold,
            stale=False,
            reason="threshold 0 means no expiry for this class",
        )

    if age_hours <= threshold:
        return FreshnessVerdict(
            status=stored,
            age_hours=age_hours,
            threshold_hours=threshold,
            stale=False,
            reason=f"within the {_format_hours(threshold)} h freshness budget",
        )

    return FreshnessVerdict(
        status=VerificationStatus.INDICATIVE,
        age_hours=age_hours,
        threshold_hours=threshold,
        stale=True,
        reason=(
            f"captured {_format_hours(age_hours)} h ago, beyond the "
            f"{_format_hours(threshold)} h freshness budget"
        ),
    )


def load_freshness_config(path: Path) -> tuple[FreshnessConfig, list[str]]:
    """Read ``config/freshness.yml``; return the config plus any problems.

    A missing file is not a problem: callers fall back to each record's own
    ``freshness_max_age_hours``. Parse errors are returned as plain strings so
    this module does not depend on the validation report types.
    """
    if not path.is_file():
        return FreshnessConfig(), []

    try:
        payload: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return FreshnessConfig(), [f"could not parse {path}: {exc}"]

    if payload is None:
        return FreshnessConfig(), []
    if not isinstance(payload, dict):
        return FreshnessConfig(), [f"{path} must contain a mapping"]

    mapping = cast(dict[str, object], payload)

    default_hours = _as_decimal(mapping.get("default_max_age_hours"))
    if default_hours is None:
        defaults = mapping.get("defaults")
        if isinstance(defaults, dict):
            default_hours = _as_decimal(cast(dict[str, object], defaults).get("max_age_hours"))
    if default_hours is None:
        default_hours = _as_decimal(mapping.get("max_age_hours"))

    review_queue_hours = _as_decimal(mapping.get("review_queue_max_age_hours"))
    if review_queue_hours is None:
        review_queue_hours = DEFAULT_REVIEW_QUEUE_MAX_AGE_HOURS

    config = FreshnessConfig(
        default_hours=None if default_hours is None else _clamp_hours(default_hours),
        by_seller_type=_as_hours_map(mapping.get("by_seller_type") or mapping.get("seller_type")),
        by_category=_as_hours_map(mapping.get("by_category") or mapping.get("category")),
        review_queue_max_age_hours=_clamp_hours(review_queue_hours),
    )
    return config, []


def _clamp_hours(value: Decimal) -> Decimal:
    return max(value, NO_EXPIRY_HOURS)


def _format_hours(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(int(value))
    return f"{value:.1f}"


def _as_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float, str)):
        try:
            return Decimal(str(value).strip())
        except ArithmeticError:
            return None
    if isinstance(value, dict):
        nested = cast(dict[str, object], value)
        for key in ("max_age_hours", "hours", "value", "threshold"):
            if key in nested:
                return _as_decimal(nested[key])
    return None


def _as_hours_map(value: object) -> dict[str, Decimal]:
    if not isinstance(value, dict):
        return {}
    mapping = cast(dict[object, object], value)
    converted: dict[str, Decimal] = {}
    for key, raw in mapping.items():
        parsed = _as_decimal(raw)
        if parsed is not None:
            converted[str(key)] = _clamp_hours(parsed)
    return converted


__all__ = [
    "DEFAULT_REVIEW_QUEUE_MAX_AGE_HOURS",
    "FreshnessConfig",
    "FreshnessRecord",
    "FreshnessVerdict",
    "effective_verification",
    "load_freshness_config",
    "measure_age_hours",
    "resolve_threshold_hours",
]
