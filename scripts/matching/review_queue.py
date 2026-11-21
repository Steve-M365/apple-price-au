"""Persistent matching review queue and manual overrides.

Uncertain matches are written to ``data/matching/review_queue.json``. Human
approvals and rejections are recorded in ``data/matching/overrides.json`` and
are reapplied after every collection run.

A rejected candidate cannot re-enter the queue unless its source hash changes,
because the candidate identity includes that hash.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, Field

from scripts.models.common import OrchardModel, UtcDatetime
from scripts.models.enums import MatchStatus
from scripts.models.matching import MatchCandidate

__all__ = [
    "EnqueueResult",
    "MatchDecision",
    "MatchOverride",
    "ReviewQueueFile",
    "ReviewOverridesFile",
    "apply_overrides",
    "decide_candidate",
    "enqueue_candidates",
    "load_overrides",
    "load_queue",
    "save_overrides",
    "save_queue",
]

MatchDecision = Literal["approved", "rejected"]


class MatchOverride(OrchardModel):
    """A durable human decision for one matching candidate."""

    candidate_id: str
    decision: MatchDecision
    decided_by: str
    decided_at: UtcDatetime
    note: str | None = None


class ReviewQueueFile(OrchardModel):
    """The on-disk review queue container."""

    candidates: list[MatchCandidate] = Field(default_factory=list)


class ReviewOverridesFile(OrchardModel):
    """The on-disk manual override container."""

    overrides: list[MatchOverride] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    """Counts describing one review-queue refresh."""

    added: int
    already_queued: int
    previously_rejected: int
    queue_size: int
    queue_size_delta: int

    def as_dict(self) -> dict[str, int]:
        return {
            "added": self.added,
            "already_queued": self.already_queued,
            "previously_rejected": self.previously_rejected,
            "queue_size": self.queue_size,
            "queue_size_delta": self.queue_size_delta,
        }


def _atomic_write_json(path: Path, payload: object) -> None:
    """Write JSON atomically so a interrupted run cannot corrupt the queue."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def load_queue(path: Path) -> list[MatchCandidate]:
    """Load pending and decided candidates from the review queue."""
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ReviewQueueFile.model_validate(payload).candidates


def save_queue(path: Path, candidates: Sequence[MatchCandidate]) -> None:
    """Persist the review queue deterministically."""
    ordered = sorted(candidates, key=lambda candidate: candidate.candidate_id)
    _atomic_write_json(
        path,
        ReviewQueueFile(candidates=list(ordered)).model_dump(mode="json", exclude_none=True),
    )


def load_overrides(path: Path) -> dict[str, MatchOverride]:
    """Load manual decisions keyed by candidate id."""
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    overrides = ReviewOverridesFile.model_validate(payload).overrides
    return {override.candidate_id: override for override in overrides}


def save_overrides(path: Path, overrides: Sequence[MatchOverride]) -> None:
    """Persist manual decisions deterministically."""
    ordered = sorted(overrides, key=lambda override: (override.candidate_id, override.decided_at))
    _atomic_write_json(
        path,
        ReviewOverridesFile(overrides=list(ordered)).model_dump(mode="json", exclude_none=True),
    )


def decide_candidate(
    candidate: MatchCandidate,
    *,
    decision: MatchDecision,
    decided_by: str,
    decided_at: datetime,
    note: str | None = None,
) -> MatchCandidate:
    """Return a candidate with a durable human decision applied."""
    if decision == "approved":
        status = MatchStatus.APPROVED
    else:
        status = MatchStatus.REJECTED

    payload = candidate.model_dump()
    payload.update(
        {
            "status": status,
            "decided_by": decided_by,
            "decided_at": decided_at,
            "note": note,
        }
    )
    return MatchCandidate.model_validate(payload)


def apply_overrides(
    candidates: Sequence[MatchCandidate],
    overrides: dict[str, MatchOverride],
) -> list[MatchCandidate]:
    """Apply persisted human decisions before queue publication."""
    updated: list[MatchCandidate] = []
    for candidate in candidates:
        override = overrides.get(candidate.candidate_id)
        if override is None:
            updated.append(candidate)
            continue

        payload = candidate.model_dump()
        payload.update(
            {
                "status": (
                    MatchStatus.APPROVED
                    if override.decision == "approved"
                    else MatchStatus.REJECTED
                ),
                "decided_by": override.decided_by,
                "decided_at": override.decided_at,
                "note": override.note,
            }
        )
        updated.append(MatchCandidate.model_validate(payload))
    return updated


def enqueue_candidates(
    queue_path: Path,
    candidates: Sequence[MatchCandidate],
    overrides_path: Path,
) -> EnqueueResult:
    """Add pending candidates to the review queue without losing prior decisions.

    Candidates already present are retained unchanged. Candidates previously
    rejected with the same source hash are excluded, preventing a rejected
    match from returning merely because collection ran again.
    """
    existing = load_queue(queue_path)
    overrides = load_overrides(overrides_path)
    existing_ids = {candidate.candidate_id for candidate in existing}

    added: list[MatchCandidate] = []
    already_queued = 0
    previously_rejected = 0

    for candidate in candidates:
        override = overrides.get(candidate.candidate_id)
        if override is not None and override.decision == "rejected":
            previously_rejected += 1
            continue
        if candidate.candidate_id in existing_ids:
            already_queued += 1
            continue
        added.append(candidate)
        existing_ids.add(candidate.candidate_id)

    queue = [*existing, *added]
    queue = apply_overrides(queue, overrides)
    save_queue(queue_path, queue)

    previous_size = len(existing)
    return EnqueueResult(
        added=len(added),
        already_queued=already_queued,
        previously_rejected=previously_rejected,
        queue_size=len(queue),
        queue_size_delta=len(queue) - previous_size,
    )
