"""Run manifests: one per run, including the runs that produced nothing.

A missing manifest cannot answer "which sources were quiet this week, and why",
so the manifest is written even when a run fails or is cut short. These tests
cover that contract and the count invariants the model enforces.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.collectors.snapshot import write_run_manifest
from scripts.models import RunManifest

START = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
END = START + timedelta(minutes=5)


def _write(tmp_path: Path, **overrides: object) -> Path:
    kwargs: dict[str, object] = {
        "seller_id": "apple_au",
        "adapter": "AppleAuAdapter",
        "run_id": "20261117T010000Z-aaaa1111",
        "started_at": START,
        "finished_at": END,
        "fetched": 45,
        "parsed": 45,
        "matched": 45,
        "rejected": 0,
        "blocked_by_policy": 0,
        "root": tmp_path,
    }
    kwargs.update(overrides)
    return write_run_manifest(**kwargs)  # type: ignore[arg-type]


def test_manifest_validates_against_the_model(tmp_path: Path) -> None:
    path = _write(tmp_path)
    manifest = RunManifest.model_validate_json(path.read_text(encoding="utf-8"))

    assert manifest.adapter == "AppleAuAdapter"
    assert manifest.fetched == 45
    assert manifest.finished_at == END


def test_manifest_is_written_when_the_run_produced_nothing(tmp_path: Path) -> None:
    """A failed run is the one whose manifest matters most."""
    path = _write(tmp_path, fetched=45, parsed=0, matched=0, rejected=45)
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["parsed"] == 0
    assert manifest["rejected"] == 45
    assert path.is_file()


def test_manifest_records_policy_blocks_separately_from_errors(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        fetched=45,
        parsed=0,
        matched=0,
        rejected=0,
        blocked_by_policy=1,
    )
    assert json.loads(path.read_text(encoding="utf-8"))["blocked_by_policy"] == 1


def test_errors_are_recorded_with_type_and_message(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        fetched=3,
        parsed=2,
        matched=2,
        rejected=1,
        errors=[
            {
                "product_id": "apple-iphone-18-pro-6-3-inch-256gb-black",
                "url": "https://www.apple.com/au/shop/buy-iphone/iphone-18-pro",
                "message": "title no longer matches the catalogue",
                "error_type": "title_drift",
            }
        ],
    )
    manifest = RunManifest.model_validate_json(path.read_text(encoding="utf-8"))

    assert len(manifest.errors) == 1
    assert manifest.errors[0].error_type == "title_drift"
    assert manifest.errors[0].product_id == "apple-iphone-18-pro-6-3-inch-256gb-black"


def test_a_malformed_error_url_does_not_lose_the_error(tmp_path: Path) -> None:
    """A bad URL in diagnostics must not destroy the diagnostic."""
    path = _write(
        tmp_path,
        fetched=1,
        parsed=0,
        matched=0,
        rejected=1,
        errors=[{"message": "boom", "error_type": "parse_error", "url": "not a url"}],
    )
    manifest = RunManifest.model_validate_json(path.read_text(encoding="utf-8"))

    assert len(manifest.errors) == 1
    assert manifest.errors[0].url is None
    assert manifest.errors[0].message == "boom"


def test_two_runs_write_two_manifests(tmp_path: Path) -> None:
    first = _write(tmp_path, run_id="run-one")
    second = _write(tmp_path, run_id="run-two")

    assert first != second
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_counts_cannot_claim_more_parsed_than_fetched(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _write(tmp_path, fetched=2, parsed=3, matched=3)


def test_finish_cannot_precede_start(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _write(tmp_path, started_at=END, finished_at=START)


def test_manifest_files_are_world_readable(tmp_path: Path) -> None:
    """Evidence is committed and published, so 0600 from mkstemp is wrong."""
    path = _write(tmp_path)
    assert path.stat().st_mode & 0o044 == 0o044
