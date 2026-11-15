"""Shared validated scalar types for the Orchard domain models.

Money is always :class:`decimal.Decimal` in Australian dollars and quantised to
two decimal places.  Amounts serialise to JSON as numbers (``2099.00``) to match
the canonical dataset examples and the static site's formatting, while the
arithmetic stays in Decimal so no binary floating point error enters the
pipeline.  Timestamps are timezone-aware UTC datetimes serialised as ISO 8601
with a trailing ``Z``.

Every model in this package inherits :class:`OrchardModel`, which forbids
unknown fields, strips surrounding whitespace from strings and re-validates on
assignment.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Final, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PlainSerializer,
    StringConstraints,
    WithJsonSchema,
)

CENT: Final = Decimal("0.01")
MILLI: Final = Decimal("0.001")
TEN_THOUSANDTH: Final = Decimal("0.0001")
AUTO_PUBLISH_MIN_CONFIDENCE: Final = Decimal("0.85")

_CURRENCY_MARKERS: Final = re.compile(
    r"\b(usd|eur|gbp|nzd|cad|hkd|sgd|jpy|cny|chf)\b|\bus\s*\$", re.I
)
_SOURCE_HASH: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_MONEY_PATTERN: Final = r"^-?(0|[1-9][0-9]*)(\.[0-9]{2})$"

# Money and percentages are JSON numbers, not strings. This matches the
# canonical examples in the project brief and keeps the static site's Intl
# formatting simple. Precision is preserved on the Python side by keeping
# Decimal end-to-end and quantising to cents before serialising.
_MONEY_SCHEMA: Final = {
    "type": "number",
    "multipleOf": 0.01,
    "description": "Australian dollar amount with exactly two decimal places.",
}
_SIGNED_MONEY_SCHEMA: Final = {
    "type": "number",
    "multipleOf": 0.01,
    "description": "Signed Australian dollar amount with exactly two decimal places.",
}
_PERCENT_SCHEMA: Final = {
    "type": "number",
    "minimum": 0,
    "maximum": 100,
    "multipleOf": 0.01,
    "description": "Percentage between 0 and 100 with at most two decimal places.",
}
_CONFIDENCE_SCHEMA: Final = {
    "type": "number",
    "minimum": 0,
    "maximum": 1,
    "description": "Match or price confidence in the closed interval [0, 1].",
}
_TIMESTAMP_SCHEMA: Final = {
    "type": "string",
    "format": "date-time",
    "description": "Timezone-aware timestamp, stored in UTC, serialised as ISO 8601 with a trailing Z.",
}
_HASH_SCHEMA: Final = {
    "type": "string",
    "pattern": r"^sha256:[0-9a-f]{64}$",
    "description": "Content hash of the captured evidence: 'sha256:' followed by 64 lowercase hex characters.",
}


class OrchardModel(BaseModel):
    """Base class for every Orchard model. Extra fields are a hard error."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        validate_default=True,
        use_enum_values=False,
        populate_by_name=True,
    )


def _coerce_decimal(value: object) -> object:
    """Accept Decimal, int, float, numeric strings or an AUD amount object."""
    if isinstance(value, bool):
        raise ValueError("amount must be a decimal number, not a boolean")
    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        currency = mapping.get("currency")
        if currency is not None and str(currency).strip().upper() != "AUD":
            raise ValueError(f"only AUD is supported, got currency {currency!r}")
        amount = mapping.get("amount")
        if amount is None:
            raise ValueError("amount object must contain an 'amount' key")
        return _coerce_decimal(amount)
    if isinstance(value, str):
        text = value.strip()
        if _CURRENCY_MARKERS.search(text):
            raise ValueError(f"only AUD is supported, got {value!r}")
        cleaned = text.replace("A$", "").replace("$", "").replace(",", "")
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"{value!r} is not a valid decimal amount") from exc
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _quantise(value: Decimal, quantum: Decimal, label: str) -> Decimal:
    try:
        quantised = value.quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"{label} is out of range: {value}") from exc
    decimals = -cast(int, quantum.as_tuple().exponent)
    if quantised != value:
        raise ValueError(f"{label} must have at most {decimals} decimals")
    return quantised


def _aud_money(value: Decimal) -> Decimal:
    money = _quantise(value, CENT, "AUD amount")
    if money < 0:
        raise ValueError(f"AUD amount must be greater than or equal to 0, got {money}")
    return money


def _signed_aud(value: Decimal) -> Decimal:
    return _quantise(value, CENT, "AUD amount")


def _percent(value: Decimal) -> Decimal:
    percentage = _quantise(value, CENT, "percentage")
    if not Decimal(0) <= percentage <= Decimal(100):
        raise ValueError(f"percentage must be between 0 and 100, got {percentage}")
    return percentage


def _confidence(value: Decimal) -> Decimal:
    if not Decimal(0) <= value <= Decimal(1):
        raise ValueError(f"confidence must be between 0 and 1, got {value}")
    return value


def _per_point(value: Decimal) -> Decimal:
    points_value = _quantise(value, TEN_THOUSANDTH, "point value")
    if points_value < 0:
        raise ValueError(f"point value must be greater than or equal to 0, got {points_value}")
    return points_value


def _non_negative(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError(f"value must be greater than or equal to 0, got {value}")
    return value


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware; naive datetimes are rejected")
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Render a UTC timestamp as ISO 8601 with a trailing ``Z``."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _source_hash(value: str) -> str:
    if not _SOURCE_HASH.match(value):
        raise ValueError("source_hash must be 'sha256:' followed by 64 lowercase hex characters")
    return value


def _coerce_string_map(value: object) -> object:
    if not isinstance(value, dict):
        return value
    mapping = cast(dict[object, object], value)
    converted: dict[str, str] = {}
    for key, item in mapping.items():
        if isinstance(item, str):
            converted[str(key)] = item
        elif isinstance(item, bool) or item is None or isinstance(item, (int, float, Decimal)):
            converted[str(key)] = str(item)
        else:
            raise ValueError("map values must be strings or scalars")
    return converted


def round_money(value: Decimal) -> Decimal:
    """Round to cents using half-up rounding (the rounding used in validation rules)."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def round_percent(value: Decimal) -> Decimal:
    """Round a percentage to two decimal places using half-up rounding."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

AUDMoney = Annotated[
    Decimal,
    BeforeValidator(_coerce_decimal),
    AfterValidator(_aud_money),
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
    WithJsonSchema(_MONEY_SCHEMA),
]

SignedAUD = Annotated[
    Decimal,
    BeforeValidator(_coerce_decimal),
    AfterValidator(_signed_aud),
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
    WithJsonSchema(_SIGNED_MONEY_SCHEMA),
]

Percent = Annotated[
    Decimal,
    BeforeValidator(_coerce_decimal),
    AfterValidator(_percent),
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
    WithJsonSchema(_PERCENT_SCHEMA),
]

Confidence = Annotated[
    Decimal,
    BeforeValidator(_coerce_decimal),
    AfterValidator(_confidence),
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
    WithJsonSchema(_CONFIDENCE_SCHEMA),
]

PointValue = Annotated[
    Decimal,
    BeforeValidator(_coerce_decimal),
    AfterValidator(_per_point),
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
    WithJsonSchema(_SIGNED_MONEY_SCHEMA),
]

NonNegativeDecimal = Annotated[
    Decimal,
    BeforeValidator(_coerce_decimal),
    AfterValidator(_non_negative),
]

UtcDatetime = Annotated[
    datetime,
    AfterValidator(_to_utc),
    PlainSerializer(format_utc, return_type=str, when_used="json"),
    WithJsonSchema(_TIMESTAMP_SCHEMA),
]

SourceHash = Annotated[
    str,
    AfterValidator(_source_hash),
    WithJsonSchema(_HASH_SCHEMA),
]

StringMap = Annotated[dict[str, str], BeforeValidator(_coerce_string_map)]
