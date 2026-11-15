"""Seller-listing to product match decisions.

Every match below the publish threshold lands here first. A candidate is only
approved by a person (:class:`MatchStatus.APPROVED`, with ``decided_by``) or
auto-published when its confidence clears 0.85.
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from .common import (
    AUTO_PUBLISH_MIN_CONFIDENCE,
    AUDMoney,
    Confidence,
    NonEmptyStr,
    OrchardModel,
    SourceHash,
    UtcDatetime,
)
from .enums import MatchMethod, MatchStatus


class MatchCandidate(OrchardModel):
    """A proposed link between a raw seller listing and a catalogue product."""

    candidate_id: NonEmptyStr
    seller_id: NonEmptyStr
    raw_title: NonEmptyStr
    raw_price_aud: AUDMoney
    proposed_product_id: str | None = None
    method: MatchMethod
    confidence: Confidence
    config_diff: list[NonEmptyStr] = Field(default_factory=list)
    status: MatchStatus = MatchStatus.PENDING
    source_hash: SourceHash
    captured_at: UtcDatetime
    decided_by: str | None = None
    decided_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _check_decision(self) -> Self:
        decided = self.status in (
            MatchStatus.APPROVED,
            MatchStatus.REJECTED,
            MatchStatus.AUTO_PUBLISHED,
        )
        if decided and (not self.decided_by or self.decided_at is None):
            raise ValueError(f"status '{self.status.value}' requires decided_by and decided_at")
        if not decided and (self.decided_by or self.decided_at is not None):
            raise ValueError("a pending candidate cannot carry a decision")
        if self.decided_at is not None and self.decided_at < self.captured_at:
            raise ValueError("decided_at must not be earlier than captured_at")
        if self.proposed_product_id is None and self.status in (
            MatchStatus.APPROVED,
            MatchStatus.AUTO_PUBLISHED,
        ):
            raise ValueError("an approved candidate must name a proposed_product_id")
        if (
            self.status is MatchStatus.AUTO_PUBLISHED
            and self.confidence < AUTO_PUBLISH_MIN_CONFIDENCE
        ):
            raise ValueError(
                f"auto_published requires confidence >= {AUTO_PUBLISH_MIN_CONFIDENCE}; "
                "low-confidence matches go to the review queue"
            )
        if self.method is MatchMethod.FUZZY and self.status is MatchStatus.AUTO_PUBLISHED:
            raise ValueError("a fuzzy match cannot be auto-published; it needs a human decision")
        return self


__all__ = ["MatchCandidate"]
