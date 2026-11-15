"""Orchard domain models.

Import the canonical shapes from here::

    from scripts.models import Product, Seller, Offer

All money is :class:`decimal.Decimal` in AUD; all timestamps are timezone-aware
UTC and serialise with a trailing ``Z``.
"""

from __future__ import annotations

from .common import (
    AUDMoney,
    Confidence,
    NonEmptyStr,
    OrchardModel,
    Percent,
    SignedAUD,
    SourceHash,
    UtcDatetime,
    format_utc,
)
from .deal import Deal, PurchaseRoute
from .education import EducationOffer
from .enums import (
    CardType,
    Category,
    CollectionMethod,
    DealType,
    GiftCardChannel,
    MatchMethod,
    MatchStatus,
    PriceBasis,
    Priority,
    ProductStatus,
    PromotionType,
    PurchaseRouteType,
    RrpVerdict,
    SellerCategory,
    SellerType,
    StockStatus,
    VerificationStatus,
)
from .gift_card import GiftCardOffer
from .matching import MatchCandidate
from .offer import Offer
from .product import Product
from .run import RunError, RunManifest
from .seller import Seller

__all__ = [
    "AUDMoney",
    "CardType",
    "Category",
    "CollectionMethod",
    "Confidence",
    "Deal",
    "DealType",
    "EducationOffer",
    "GiftCardChannel",
    "GiftCardOffer",
    "MatchCandidate",
    "MatchMethod",
    "MatchStatus",
    "NonEmptyStr",
    "Offer",
    "OrchardModel",
    "Percent",
    "PriceBasis",
    "Priority",
    "Product",
    "ProductStatus",
    "PromotionType",
    "PurchaseRoute",
    "PurchaseRouteType",
    "RrpVerdict",
    "RunError",
    "RunManifest",
    "Seller",
    "SellerCategory",
    "SellerType",
    "SignedAUD",
    "SourceHash",
    "StockStatus",
    "UtcDatetime",
    "VerificationStatus",
    "format_utc",
]
