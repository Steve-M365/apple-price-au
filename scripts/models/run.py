"""Collection run manifests.

One manifest per adapter run, written even when the run partly failed. The
counts let the health page and the alerting workflow tell a quiet run from a
blocked one without reading snapshots.
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, HttpUrl, model_validator

from .common import NonEmptyStr, OrchardModel, UtcDatetime


class RunError(OrchardModel):
    """A single failure recorded during a collection run."""

    product_id: str | None = None
    url: HttpUrl | None = None
    message: NonEmptyStr
    error_type: NonEmptyStr


class RunManifest(OrchardModel):
    """Outcome of one adapter run, including policy blocks and errors."""

    run_id: NonEmptyStr
    adapter: NonEmptyStr
    started_at: UtcDatetime
    finished_at: UtcDatetime
    fetched: int = Field(ge=0)
    parsed: int = Field(ge=0)
    matched: int = Field(ge=0)
    rejected: int = Field(ge=0)
    blocked_by_policy: int = Field(ge=0)
    errors: list[RunError] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_run_counts(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not be earlier than started_at")
        if self.parsed > self.fetched:
            raise ValueError("parsed cannot exceed fetched")
        if self.matched > self.parsed:
            raise ValueError("matched cannot exceed parsed")
        return self


__all__ = ["RunError", "RunManifest"]
