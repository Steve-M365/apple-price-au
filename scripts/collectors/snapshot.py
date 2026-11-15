"""Append-only snapshot evidence for one seller's collection run.

``data/snapshots/YYYY-MM-DD/<seller_id>.json`` is the only layer in Orchard that
is *evidence*: it holds the records a run actually captured, the run that
captured them and the instant it happened. Deals, history and best-price claims
are derived from these files, so a snapshot is never edited and never silently
replaced.

Two rules shape :func:`write_snapshot`:

1. **Append-only.** If the target path already holds different content the write
   is refused with :class:`SnapshotConflictError` and nothing is touched. A second run
   on the same day must write a distinct filename (the CLI offers
   ``apple_au-<run_id>.json`` for exactly that) rather than overwrite the first.
2. **Idempotent when identical.** Re-writing byte-identical content is a no-op,
   so a retried job that captured nothing new cannot fail on its own evidence.

The write is atomic: the payload lands in a sibling temp file and is then
``os.replace``d, so a crash mid-write can never leave a half-written snapshot.
Offers are sorted by ``offer_id`` and serialised through the model, which keeps
the bytes deterministic for a given run.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from scripts.models import Offer
from scripts.models.common import format_utc

__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "SnapshotConflictError",
    "build_snapshot",
    "render_snapshot",
    "snapshot_path",
    "write_snapshot",
]

#: Bumped only when the snapshot envelope changes shape. A reader keys off it.
SNAPSHOT_SCHEMA_VERSION: Final = 1


class SnapshotConflictError(RuntimeError):
    """An existing snapshot holds different content; refusing to overwrite it.

    This is a data-integrity refusal, not a transient fault: the caller must
    choose a new path (a new capture date, or a run-suffixed filename).
    """

    def __init__(self, path: Path, *, existing_bytes: int, new_bytes: int) -> None:
        super().__init__(
            f"{path} already holds a snapshot ({existing_bytes} bytes) and the new "
            f"capture differs ({new_bytes} bytes); snapshots are append-only, so "
            "write the new capture to a distinct filename instead of overwriting it"
        )
        self.path = path


def snapshot_path(root: Path, *, date: str, seller_id: str, run_id: str | None = None) -> Path:
    """Return ``<root>/<date>/<seller_id>.json``, optionally run-suffixed.

    ``date`` is an ISO date (``2026-11-15``) rather than a datetime so the
    directory name is stable across timezones; callers derive it from the UTC
    capture instant.
    """
    filename = f"{seller_id}.json" if run_id is None else f"{seller_id}-{run_id}.json"
    return root / date / filename


def build_snapshot(
    seller_id: str,
    run_id: str,
    offers: Sequence[Offer],
    captured_at: datetime,
) -> dict[str, object]:
    """Assemble the snapshot envelope: provenance plus the captured offers.

    Every offer must belong to ``seller_id``; a mixed file would make the
    seller's snapshot claim evidence it did not capture.
    """
    if not seller_id.strip():
        raise ValueError("seller_id must be non-empty")
    if not run_id.strip():
        raise ValueError("run_id must be non-empty")
    if captured_at.tzinfo is None:
        raise ValueError("captured_at must be timezone-aware")
    for offer in offers:
        if offer.seller_id != seller_id:
            raise ValueError(
                f"{offer.offer_id} belongs to seller {offer.seller_id!r}, not {seller_id!r}"
            )
    ordered = sorted(offers, key=lambda item: item.offer_id)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "seller_id": seller_id,
        "run_id": run_id,
        "captured_at": format_utc(captured_at),
        "captured_date": captured_at.astimezone(UTC).date().isoformat(),
        "offer_count": len(ordered),
        "offers": [json.loads(offer.model_dump_json()) for offer in ordered],
    }


def render_snapshot(
    seller_id: str,
    run_id: str,
    offers: Sequence[Offer],
    captured_at: datetime,
) -> str:
    """Render the snapshot as deterministic, human-diffable JSON text."""
    payload = build_snapshot(seller_id, run_id, offers, captured_at)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def write_snapshot(
    path: Path,
    seller_id: str,
    run_id: str,
    offers: Sequence[Offer],
    captured_at: datetime,
) -> Path:
    """Write one seller's snapshot, refusing to replace different evidence.

    Returns the path written (or already matching). Raises
    :class:`SnapshotConflictError` when the path exists with different content.
    """
    text = render_snapshot(seller_id, run_id, offers, captured_at)
    data = text.encode("utf-8")
    if path.exists():
        existing = path.read_bytes()
        if existing == data:
            return path
        raise SnapshotConflictError(path, existing_bytes=len(existing), new_bytes=len(data))

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        # mkstemp creates 0600; evidence should be as readable as the rest of
        # the committed tree once it is published.
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:  # pragma: no cover - defensive cleanup
        Path(temporary).unlink(missing_ok=True)
        raise
    return path
