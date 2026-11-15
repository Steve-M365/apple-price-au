"""Catalogue product models.

A :class:`Product` is one configuration of an Apple product sold in Australia:
the variant axes live in ``configuration`` and the Apple RRP is only marked
verified when it is backed by an evidence URL.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, HttpUrl, StringConstraints, model_validator

from .common import AUDMoney, NonEmptyStr, OrchardModel, StringMap, UtcDatetime
from .enums import Category, Priority, ProductStatus, RrpVerdict

Gtin = Annotated[str, StringConstraints(pattern=r"^(\d{8}|\d{12,14})$")]

KNOWN_CONFIGURATION_KEYS = (
    "chip",
    "colour",
    "connectivity",
    "cellular",
    "generation",
    "memory",
    "screen_size",
    "size",
    "storage",
)


class Product(OrchardModel):
    """One Apple product configuration.

    ``configuration`` carries the variant axes. Known keys are listed in
    :data:`KNOWN_CONFIGURATION_KEYS`; extra keys are permitted so new axes do
    not need a model change. Values are always strings (``"512GB"``,
    ``"16GB"``), never numbers.
    """

    product_id: NonEmptyStr
    apple_sku: str | None = None
    gtin: Gtin | None = None
    category: Category
    family: NonEmptyStr
    name: NonEmptyStr
    brand: Literal["Apple"] = "Apple"
    model: NonEmptyStr
    configuration: StringMap = Field(default_factory=dict)
    apple_au_url: HttpUrl
    apple_rrp_aud: AUDMoney | None = None
    education_eligible: bool = False
    status: ProductStatus = ProductStatus.ACTIVE
    priority: Priority = Priority.NORMAL
    specs_source_url: HttpUrl | None = None
    rrp_verification: RrpVerdict = RrpVerdict.UNVERIFIED
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @model_validator(mode="after")
    def _check_rrp_evidence(self) -> Self:
        if self.apple_rrp_aud is None and self.rrp_verification is RrpVerdict.VERIFIED:
            raise ValueError(
                "rrp_verification cannot be 'verified' when apple_rrp_aud is null; "
                "record the price or downgrade the verdict"
            )
        if self.rrp_verification is RrpVerdict.VERIFIED and self.specs_source_url is None:
            raise ValueError(
                "a verified apple_rrp_aud requires specs_source_url evidence; "
                "every RRP must trace to a captured Apple AU page"
            )
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        return self


__all__ = ["KNOWN_CONFIGURATION_KEYS", "Gtin", "Product"]
