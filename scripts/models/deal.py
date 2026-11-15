"""Deal detection output and ranked purchase routes.

A :class:`Deal` is a derived claim: it cites the current and reference prices,
the saving and the evidence URL, and it is recomputed every run rather than
hand-edited. A :class:`PurchaseRoute` ranks the ways of buying one product —
Apple retail, education, EPP, business quote, gift card, cashback.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from pydantic import Field, HttpUrl, model_validator

from .common import (
    AUDMoney,
    Confidence,
    NonEmptyStr,
    OrchardModel,
    Percent,
    SignedAUD,
    UtcDatetime,
    round_percent,
)
from .enums import DealType, PurchaseRouteType, VerificationStatus

DISCOUNT_TOLERANCE = Decimal("0.01")


class Deal(OrchardModel):
    """A saving on one product at one seller, with the evidence behind it.

    ``reference_price_aud`` is the price the saving is measured against and
    ``reference_type`` names that basis (``rrp``, ``previous_price``,
    ``all_time_low``, ``market_median``). ``effective_price_aud`` is optional
    and carries a price after gift cards or cashback, never instead of the
    advertised price.
    """

    deal_id: NonEmptyStr
    deal_type: DealType
    product_id: NonEmptyStr
    seller_id: NonEmptyStr
    offer_id: str | None = None
    current_price_aud: AUDMoney
    reference_price_aud: AUDMoney
    reference_type: NonEmptyStr
    saving_aud: AUDMoney
    saving_pct: Percent
    effective_price_aud: AUDMoney | None = None
    confidence: Confidence
    evidence_url: HttpUrl
    captured_at: UtcDatetime
    expires_at: UtcDatetime | None = None
    verification_status: VerificationStatus
    rule_version: NonEmptyStr

    @model_validator(mode="after")
    def _check_saving(self) -> Self:
        if self.reference_price_aud < self.current_price_aud:
            raise ValueError(
                "reference_price_aud must be greater than or equal to current_price_aud; "
                "a price rise is not a deal"
            )
        expected_saving = self.reference_price_aud - self.current_price_aud
        if abs(expected_saving - self.saving_aud) > DISCOUNT_TOLERANCE:
            raise ValueError(
                f"saving_aud {self.saving_aud} does not match reference_price_aud - "
                f"current_price_aud ({expected_saving})"
            )
        expected_pct = round_percent(expected_saving / self.reference_price_aud * 100)
        if abs(expected_pct - self.saving_pct) > DISCOUNT_TOLERANCE:
            raise ValueError(
                f"saving_pct {self.saving_pct} does not match "
                f"(reference_price_aud - current_price_aud) / reference_price_aud ({expected_pct})"
            )
        if (
            self.effective_price_aud is not None
            and self.effective_price_aud > self.current_price_aud
        ):
            raise ValueError("effective_price_aud must not exceed current_price_aud")
        if self.expires_at is not None and self.expires_at <= self.captured_at:
            raise ValueError("expires_at must be later than captured_at")
        return self


class PurchaseRoute(OrchardModel):
    """One way of buying a product, with its effective price and caveats."""

    route_type: PurchaseRouteType
    label: NonEmptyStr
    effective_price_aud: AUDMoney
    saving_vs_rrp_aud: SignedAUD
    requires: list[NonEmptyStr] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: Confidence
    source_urls: list[HttpUrl] = Field(min_length=1)
    rank: int | None = Field(default=None, ge=1)


__all__ = ["Deal", "PurchaseRoute"]
