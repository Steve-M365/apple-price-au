"""Seller registry models.

Every seller that Orchard collects from, or quotes in a purchase route, has one
record here. An automated seller names the only adapter module allowed to fetch
its prices; a curated or manual seller leaves ``adapter_module`` empty because
its prices are entered through the evidence workflow rather than scraped.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import AliasChoices, Field, HttpUrl, StringConstraints, model_validator

from .common import NonEmptyStr, OrchardModel, UtcDatetime
from .enums import CollectionMethod, SellerCategory, SellerType

CountryCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=2, max_length=2)
]

_AUTOMATED_METHODS = frozenset(
    {
        CollectionMethod.AUTOMATED_HTML,
        CollectionMethod.AUTOMATED_JSONLD,
        CollectionMethod.AUTOMATED_API,
    }
)


class Seller(OrchardModel):
    """A retailer, marketplace, telco or gift-card seller serving Australia."""

    seller_id: NonEmptyStr
    display_name: NonEmptyStr
    seller_type: SellerType
    website: HttpUrl | None = None
    product_search_url: HttpUrl | None = None
    country: CountryCode = "AU"
    supported_regions: list[NonEmptyStr] = Field(default_factory=lambda: ["AU"], min_length=1)
    categories: list[SellerCategory] = Field(min_length=1)
    collection_method: CollectionMethod
    rate_limit_rps: float = Field(
        ge=0,
        le=20,
        validation_alias=AliasChoices("rate_limit_rps", "rate_limit_rps_float"),
        description="Requests per second permitted for this seller's adapter; 0 for curated-only sellers.",
    )
    crawl_delay_seconds: float = Field(ge=0, le=120)
    authorised_reseller: bool | None = None
    authorised_evidence_url: HttpUrl | None = None
    warranty_policy_url: HttpUrl | None = None
    returns_policy_url: HttpUrl | None = None
    delivery_policy_url: HttpUrl | None = None
    trust_score_inputs: dict[str, str | int | float | bool] = Field(default_factory=dict)
    adapter_module: str = Field(
        default="",
        description="Dotted module path for automated collection; empty for curated/manual sellers.",
    )
    enabled: bool = True
    enabled_reason: str | None = None
    quote_only: bool = False
    last_success_at: UtcDatetime | None = None
    last_failure_at: UtcDatetime | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _check_registry_consistency(self) -> Self:
        if self.authorised_reseller is True and self.authorised_evidence_url is None:
            raise ValueError(
                "authorised_reseller=true requires authorised_evidence_url; "
                "an authorisation claim must cite the manufacturer's page"
            )
        if not self.enabled and not self.enabled_reason:
            raise ValueError("a disabled seller must record enabled_reason")
        if self.enabled and self.website is None:
            raise ValueError("an enabled seller requires a website")
        if self.enabled and self.product_search_url is None:
            raise ValueError(
                "an enabled seller requires a product_search_url; "
                "without one the collector has no entry point"
            )
        if self.collection_method in _AUTOMATED_METHODS:
            if not self.adapter_module:
                raise ValueError(
                    f"collection_method '{self.collection_method.value}' requires adapter_module"
                )
            if self.rate_limit_rps <= 0:
                raise ValueError(
                    f"collection_method '{self.collection_method.value}' requires "
                    "rate_limit_rps greater than 0"
                )
        return self


__all__ = ["CountryCode", "Seller"]
