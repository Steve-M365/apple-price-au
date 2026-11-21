"""Tests for the persistent matching review queue and overrides."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from scripts.matching.review_queue import (
    apply_overrides,
    decide_candidate,
    enqueue_candidates,
    load_overrides,
    load_queue,
    save_overrides,
)
from scripts.models.enums import MatchMethod, MatchStatus
from scripts.models.matching import MatchCandidate

CAPTURED_AT = datetime(2026, 11, 22, 0, 0, tzinfo=UTC)
DECIDED_AT = datetime(2026, 11, 22, 1, 0, tzinfo=UTC)
SOURCE_HASH = "sha256:" + "b" * 64


def candidate(candidate_id: str = "match_test") -> MatchCandidate:
    return MatchCandidate(
        candidate_id=candidate_id,
        seller_id="apple_au",
        raw_title="MacBook Air 13 M4 16GB Midnight",
        raw_price_aud=Decimal("1599.00"),
        proposed_product_id="macbook-air-13-m4-16gb",
        method=MatchMethod.CONFIG,
        confidence=Decimal("0.90"),
        config_diff=[],
        status=MatchStatus.PENDING,
        source_hash=SOURCE_HASH,
        captured_at=CAPTURED_AT,
    )


def test_pending_candidate_is_enqueued_once(tmp_path) -> None:
    queue_path = tmp_path / "review_queue.json"
    overrides_path = tmp_path / "overrides.json"
    first = candidate()

    result = enqueue_candidates(queue_path, [first], overrides_path)
    assert result.added == 1
    assert result.queue_size == 1
    assert result.queue_size_delta == 1

    second = enqueue_candidates(queue_path, [first], overrides_path)
    assert second.added == 0
    assert second.already_queued == 1
    assert second.queue_size == 1
    assert second.queue_size_delta == 0


def test_rejected_candidate_does_not_reenter_with_same_source_hash(tmp_path) -> None:
    queue_path = tmp_path / "review_queue.json"
    overrides_path = tmp_path / "overrides.json"
    original = candidate()

    enqueue_candidates(queue_path, [original], overrides_path)
    queue = load_queue(queue_path)
    decided = decide_candidate(
        queue[0],
        decision="rejected",
        decided_by="steve",
        decided_at=DECIDED_AT,
    )
    save_overrides(
        overrides_path,
        [
            type(decided)(
                candidate_id=decided.candidate_id,
                decision="rejected",
                decided_by="steve",
                decided_at=DECIDED_AT,
            )
        ],
    )

    result = enqueue_candidates(queue_path, [original], overrides_path)
    assert result.previously_rejected == 1
    assert result.added == 0


def test_overrides_survive_recollection(tmp_path) -> None:
    queue_path = tmp_path / "review_queue.json"
    overrides_path = tmp_path / "overrides.json"
    original = candidate()

    enqueue_candidates(queue_path, [original], overrides_path)
    queue = load_queue(queue_path)
    approved = decide_candidate(
        queue[0],
        decision="approved",
        decided_by="steve",
        decided_at=DECIDED_AT,
    )
    overrides = {
        approved.candidate_id: type(approved)(
            candidate_id=approved.candidate_id,
            decision="approved",
            decided_by="steve",
            decided_at=DECIDED_AT,
        )
    }

    applied = apply_overrides([original], overrides)
    assert applied[0].status is MatchStatus.APPROVED
    assert applied[0].decided_by == "steve"

    # A later collection run can load and reapply the same override.
    stored = load_overrides(overrides_path)
    assert stored == overrides


def test_queue_file_uses_candidates_key(tmp_path) -> None:
    queue_path = tmp_path / "review_queue.json"
    enqueue_candidates(queue_path, [candidate()], tmp_path / "overrides.json")

    payload = json.loads(queue_path.read_text(encoding="utf-8"))
    assert "candidates" in payload
    assert payload["candidates"][0]["candidate_id"] == "match_test"
