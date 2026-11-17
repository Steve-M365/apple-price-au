"""The run-manifest dataset is a directory, not a file.

Each run writes its own manifest so parallel adapters never contend for one
index file. These tests pin the loader behaviour: a valid manifest counts, and a
broken one fails the gate **by filename**, because "runs[3]" is not actionable
when the files are named after run ids.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.validation.validate_data import run_validation

MANIFEST = {
    "run_id": "20261117T010000Z-aaaa1111",
    "adapter": "AppleAuAdapter",
    "started_at": "2026-11-17T01:00:00Z",
    "finished_at": "2026-11-17T01:05:00Z",
    "fetched": 45,
    "parsed": 45,
    "matched": 45,
    "rejected": 0,
    "blocked_by_policy": 0,
    "errors": [],
}


def _tree(tmp_path: Path, manifest: dict[str, object] | None = None) -> Path:
    data = tmp_path / "data"
    (data / "runs").mkdir(parents=True)
    (data / "products.json").write_text('{"products": []}\n', encoding="utf-8")
    (data / "sellers.json").write_text('{"sellers": []}\n', encoding="utf-8")
    (data / "runs" / "apple_au-20261117T010000Z-aaaa1111.json").write_text(
        json.dumps(MANIFEST if manifest is None else manifest), encoding="utf-8"
    )
    return data


def test_a_valid_manifest_directory_is_counted(tmp_path: Path) -> None:
    report = run_validation(_tree(tmp_path), now=datetime(2026, 11, 17, 2, 0, tzinfo=UTC))

    assert report.stats["run_manifests"] == 1
    assert not report.hard


def test_a_broken_manifest_fails_the_gate_by_filename(tmp_path: Path) -> None:
    broken = dict(MANIFEST)
    broken["parsed"] = 99  # more parsed than fetched is impossible
    report = run_validation(_tree(tmp_path, broken), now=datetime(2026, 11, 17, 2, 0, tzinfo=UTC))

    assert report.hard, "a broken manifest must fail the gate"
    files = {issue.file for issue in report.hard}
    assert files == {"runs/apple_au-20261117T010000Z-aaaa1111.json"}
    # The model-level error has no field path, so the message must carry it.
    assert "parsed" in report.hard[0].message


def test_a_manifest_that_is_not_json_fails_by_filename(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "runs").mkdir(parents=True)
    (data / "runs" / "junk.json").write_text("{not json", encoding="utf-8")
    (data / "products.json").write_text('{"products": []}\n', encoding="utf-8")
    (data / "sellers.json").write_text('{"sellers": []}\n', encoding="utf-8")

    report = run_validation(data, now=datetime(2026, 11, 17, 2, 0, tzinfo=UTC))

    assert {issue.file for issue in report.hard} == {"runs/junk.json"}
    assert report.hard[0].code == "invalid_json"
