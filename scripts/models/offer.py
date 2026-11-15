"""Retail price observation models.

An :class:`Offer` is one captured price for one product at one seller, with the
evidence needed to publish it: URL, timestamp, content hash and a verification
verdict. Education pricing never appears here — see
:mod:`scripts.models.education`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from pydantic import Field, HttpUrl, model_validator

from .common import (
    AUTO_PUBLISH_MIN_CONFIDENCE,
    AUDMoney,
    Confidence,
    NonEmptyStr,
    OrchardModel,
    SourceHash,
    UtcDatetime,
)
from .enums import (
    MatchMethod,
    MatchStatus,
    PriceBasis,
    PromotionType,
    StockStatus,
    VerificationStatus,
)

_IN_STOCK_STATUSES = frozenset(
    {StockStatus.IN_STOCK, StockStatus.LOW_STOCK, StockStatus.PREORDER},
)


class Offer(OrchardModel):
    """A single captured retail price observation.

    ``freshness_max_age_hours`` is the budget this observation was collected
    under; the validator turns it into a soft warning when the record is stale.
    ``confidence`` is the product match confidence, not price confidence.
    """

    offer_id: NonEmptyStr
    product_id: NonEmptyStr
    seller_id: NonEmptyStr
    url: HttpUrl
    price_aud: AUDMoney
    was_price_aud: AUDMoney | None = None
    rrp_aud: AUDMoney | None = None
    price_basis: PriceBasis
    in_stock: bool
    stock_status: StockStatus = StockStatus.UNKNOWN
    delivery_cost_aud: AUDMoney = Field(default=Decimal("0.00"))
    delivery_cost_verified: bool = False
    delivery_estimate: str = ""
    pickup_available: bool = False
    pickup_locations: list[NonEmptyStr] = Field(default_factory=list)
    promotion: str | None = None
    promotion_type: PromotionType | None = None
    bundle: bool = False
    bundle_contents: list[NonEmptyStr] = Field(default_factory=list)
    confidence: Confidence
    match_method: MatchMethod
    match_status: MatchStatus = MatchStatus.PENDING
    verification_status: VerificationStatus
    freshness_max_age_hours: int = Field(default=24, gt=0, le=720)
    captured_at: UtcDatetime
    source_hash: SourceHash
    run_id: NonEmptyStr
    grey_import: bool = False
    sold_by: str | None = None

    @model_validator(mode="after")
    def _check_offer_consistency(self) -> Self:
        if self.was_price_aud is not None and self.was_price_aud < self.price_aud:
            raise ValueError("was_price_aud must be greater than or equal to price_aud")
        if self.stock_status in _IN_STOCK_STATUSES and not self.in_stock:
            raise ValueError(f"stock_status '{self.stock_status.value}' contradicts in_stock=false")
        if self.stock_status is StockStatus.OUT_OF_STOCK and self.in_stock:
            raise ValueError("stock_status 'out_of_stock' contradicts in_stock=true")
        if self.promotion_type is not None and not self.promotion:
            raise ValueError("promotion_type requires promotion text describing the offer")
        if self.promotion_type is PromotionType.BUNDLE and not self.bundle:
            raise ValueError("promotion_type 'bundle' requires bundle=true")
        if self.bundle and not self.bundle_contents:
            raise ValueError("bundle=true requires bundle_contents naming what is included")
        if (
            self.match_status is MatchStatus.AUTO_PUBLISHED
            and self.confidence < AUTO_PUBLISH_MIN_CONFIDENCE
        ):
            raise ValueError(
                f"match_status 'auto_published' requires confidence >= "
                f"{AUTO_PUBLISH_MIN_CONFIDENCE}; below that the match must stay pending review"
            )
        return self


__all__ = ["Offer"]
