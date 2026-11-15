"""Controlled vocabularies for the Orchard domain models.

Values are the exact strings persisted in ``data/*.json`` and published to the
site. Never rename a member without a data migration; downstream datasets and
rendered pages key off these strings.
"""

from __future__ import annotations

from enum import StrEnum


class Category(StrEnum):
    """Product category tracked by Orchard."""

    MAC = "mac"
    IPAD = "ipad"
    IPHONE = "iphone"
    WATCH = "watch"
    AIRPODS = "airpods"
    TV = "tv"
    HOMEPOD = "homepod"
    ACCESSORY = "accessory"
    APPLECARE = "applecare"


class SellerCategory(StrEnum):
    """Coverage a seller is registered for.

    This is a superset of :class:`Category`: a seller may also cover business
    sales, education, gift cards or cashback, none of which are product
    categories.
    """

    MAC = "mac"
    IPAD = "ipad"
    IPHONE = "iphone"
    WATCH = "watch"
    AIRPODS = "airpods"
    TV = "tv"
    HOMEPOD = "homepod"
    ACCESSORY = "accessory"
    APPLECARE = "applecare"
    BUSINESS = "business"
    EDUCATION = "education"
    GIFT_CARD = "gift_card"
    CASHBACK = "cashback"


class SellerType(StrEnum):
    """Commercial shape of a seller."""

    APPLE_DIRECT = "apple_direct"
    NATIONAL_RESELLER = "national_reseller"
    SPECIALIST_RESELLER = "specialist_reseller"
    ONLINE_MARKETPLACE = "online_marketplace"
    TELCO = "telco"
    BUSINESS_RESELLER = "business_reseller"
    EDUCATION_RESELLER = "education_reseller"
    GIFT_CARD_SELLER = "gift_card_seller"


class StockStatus(StrEnum):
    """Observed stock state at the moment of capture."""

    IN_STOCK = "in_stock"
    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    PREORDER = "preorder"
    UNKNOWN = "unknown"


class PriceBasis(StrEnum):
    """Why the captured price is the price that it is."""

    ADVERTISED = "advertised"
    MEMBER = "member"
    BUNDLE = "bundle"
    CLEARANCE = "clearance"
    QUOTE = "quote"
    EDU = "edu"
    EPP = "epp"


class PromotionType(StrEnum):
    """Mechanism behind a promotion attached to an offer."""

    SALE = "sale"
    CLEARANCE = "clearance"
    BUNDLE = "bundle"
    MEMBER = "member"
    GIFT_CARD = "gift_card"
    CASHBACK = "cashback"


class VerificationStatus(StrEnum):
    """How much evidence backs a record."""

    VERIFIED = "verified"
    INDICATIVE = "indicative"
    CURATED_UNVERIFIED = "curated_unverified"
    UNVERIFIED = "unverified"


class DealType(StrEnum):
    """The kind of saving a deal describes."""

    PRICE_DROP = "price_drop"
    RRP_DISCOUNT = "rrp_discount"
    ALL_TIME_LOW = "all_time_low"
    EDUCATION = "education"
    GIFT_CARD = "gift_card"
    RETAILER_GIFT_CARD = "retailer_gift_card"
    CASHBACK = "cashback"
    BUNDLE = "bundle"
    CLEARANCE = "clearance"
    RESTOCK = "restock"


class MatchMethod(StrEnum):
    """How a listing was tied to a catalogue product."""

    APPLE_SKU = "apple_sku"
    GTIN = "gtin"
    CONFIG = "config"
    FUZZY = "fuzzy"
    MANUAL = "manual"


class CollectionMethod(StrEnum):
    """How a seller's prices are captured."""

    AUTOMATED_HTML = "automated_html"
    AUTOMATED_JSONLD = "automated_jsonld"
    AUTOMATED_API = "automated_api"
    CURATED = "curated"
    MANUAL = "manual"


class MatchStatus(StrEnum):
    """Lifecycle of a seller-listing to product match."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    AUTO_PUBLISHED = "auto_published"


class ProductStatus(StrEnum):
    """Lifecycle state of a catalogue product."""

    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    SUPERSEDED = "superseded"


class Priority(StrEnum):
    """Collection urgency for a product."""

    HIGH = "high"
    NORMAL = "normal"


class RrpVerdict(StrEnum):
    """Whether a product's Apple RRP field is backed by evidence."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class CardType(StrEnum):
    """Which gift card family an offer belongs to."""

    APPLE_GIFT_CARD = "apple_gift_card"
    RETAILER_GIFT_CARD = "retailer_gift_card"


class GiftCardChannel(StrEnum):
    """Where the gift card can be bought."""

    IN_STORE = "in_store"
    ONLINE = "online"
    BOTH = "both"


class PurchaseRouteType(StrEnum):
    """A way of buying Apple hardware in Australia."""

    APPLE_RETAIL = "apple_retail"
    APPLE_EDUCATION = "apple_education"
    EPP = "epp"
    BUSINESS_QUOTE = "business_quote"
    GIFT_CARD = "gift_card"
    CASHBACK = "cashback"


__all__ = [
    "CardType",
    "Category",
    "CollectionMethod",
    "DealType",
    "GiftCardChannel",
    "MatchMethod",
    "MatchStatus",
    "PriceBasis",
    "Priority",
    "ProductStatus",
    "PromotionType",
    "PurchaseRouteType",
    "RrpVerdict",
    "SellerCategory",
    "SellerType",
    "StockStatus",
    "VerificationStatus",
]
