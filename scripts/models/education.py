"""Apple Education Store offers.

Education pricing is a separate offer type with its own eligibility rules and
annual purchase limits. It is never merged into a retail :class:`~scripts.models.offer.Offer`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from pydantic import Field, HttpUrl, model_validator

from .common import (
    AUDMoney,
    NonEmptyStr,
    OrchardModel,
    Percent,
    SourceHash,
    UtcDatetime,
    round_percent,
)
from .enums import PriceBasis, VerificationStatus

DISCOUNT_TOLERANCE = Decimal("0.01")


class EducationOffer(OrchardModel):
    """A captured Apple Education Store price for an eligible buyer.

    ``saving_aud`` and ``saving_pct`` must agree with the prices to within one
    cent, so a stale or mistyped discount cannot be published.
    """

    offer_id: NonEmptyStr
    product_id: NonEmptyStr
    seller_id: NonEmptyStr
    url: HttpUrl
    price_aud: AUDMoney
    standard_price_aud: AUDMoney
    saving_aud: AUDMoney
    saving_pct: Percent
    eligibility: list[NonEmptyStr] = Field(min_length=1)
    verification: NonEmptyStr
    annual_limit_summary: NonEmptyStr
    annual_limit_source_url: HttpUrl
    in_stock: bool
    price_basis: PriceBasis = PriceBasis.EDU
    verification_status: VerificationStatus
    freshness_max_age_hours: int = Field(default=24, gt=0, le=720)
    captured_at: UtcDatetime
    source_hash: SourceHash
    run_id: NonEmptyStr

    @model_validator(mode="after")
    def _check_education_pricing(self) -> Self:
        if self.price_basis is not PriceBasis.EDU:
            raise ValueError("EducationOffer.price_basis must be 'edu'")
        if self.standard_price_aud < self.price_aud:
            raise ValueError("standard_price_aud must be greater than or equal to price_aud")
        expected_saving = self.standard_price_aud - self.price_aud
        if abs(expected_saving - self.saving_aud) > DISCOUNT_TOLERANCE:
            raise ValueError(
                f"saving_aud {self.saving_aud} does not match standard_price_aud - price_aud "
                f"({expected_saving})"
            )
        expected_pct = round_percent(expected_saving / self.standard_price_aud * 100)
        if abs(expected_pct - self.saving_pct) > DISCOUNT_TOLERANCE:
            raise ValueError(
                f"saving_pct {self.saving_pct} does not match "
                f"(standard_price_aud - price_aud) / standard_price_aud ({expected_pct})"
            )
        return self


__all__ = ["EducationOffer"]
