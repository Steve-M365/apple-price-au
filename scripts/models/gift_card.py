"""Gift card and loyalty programme offers.

A gift card offer records what a gift card costs, what points it earns at the
assumed point value, and the effective discount once both are combined. Every
gift card record must state its risks, because these promotions commonly carry
activation limits, exclusions and expiry traps.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from pydantic import Field, HttpUrl, model_validator

from .common import (
    AUDMoney,
    NonEmptyStr,
    NonNegativeDecimal,
    OrchardModel,
    Percent,
    PointValue,
    UtcDatetime,
    round_percent,
)
from .enums import CardType, GiftCardChannel, VerificationStatus

DISCOUNT_TOLERANCE = Decimal("0.01")


class GiftCardOffer(OrchardModel):
    """A purchased-discount gift card or loyalty points promotion.

    ``effective_value_aud`` is the cash cost of the card after the assumed value
    of any points earned. ``effective_discount_pct`` must match
    ``(face_value_aud - effective_value_aud) / face_value_aud`` within one basis
    point tolerance.
    """

    offer_id: NonEmptyStr
    card_type: CardType
    seller_id: NonEmptyStr
    loyalty_program: NonEmptyStr
    channel: GiftCardChannel
    face_value_aud: AUDMoney
    purchase_price_aud: AUDMoney
    bonus_points_per_dollar: NonNegativeDecimal = Field(default=Decimal("0"))
    points_earned: NonNegativeDecimal = Field(default=Decimal("0"))
    assumed_point_value_aud: PointValue = Field(default=Decimal("0.005"))
    effective_value_aud: AUDMoney
    effective_discount_pct: Percent
    daily_limit_cards: int | None = Field(default=None, ge=0)
    starts_at: UtcDatetime
    ends_at: UtcDatetime
    source_url: HttpUrl
    verification_status: VerificationStatus
    evidence_snapshot: str | None = None
    terms_url: HttpUrl | None = None
    risks: list[NonEmptyStr] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_gift_card_value(self) -> Self:
        if self.face_value_aud <= 0:
            raise ValueError("face_value_aud must be greater than 0")
        if self.purchase_price_aud > self.face_value_aud:
            raise ValueError("purchase_price_aud must not exceed face_value_aud")
        if self.effective_value_aud > self.face_value_aud:
            raise ValueError("effective_value_aud must not exceed face_value_aud")
        expected_pct = round_percent(
            (self.face_value_aud - self.effective_value_aud) / self.face_value_aud * 100,
        )
        if abs(expected_pct - self.effective_discount_pct) > DISCOUNT_TOLERANCE:
            raise ValueError(
                f"effective_discount_pct {self.effective_discount_pct} does not match "
                f"(face_value_aud - effective_value_aud) / face_value_aud ({expected_pct})"
            )
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        if self.verification_status is VerificationStatus.VERIFIED and not self.evidence_snapshot:
            raise ValueError(
                "verification_status 'verified' requires evidence_snapshot naming the captured evidence"
            )
        return self


__all__ = ["GiftCardOffer"]
