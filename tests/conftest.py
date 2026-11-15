"""Shared pytest fixtures for the Orchard test suite.

These fixtures are the reusable harness the rest of ``tests/`` builds on:

* ``repo_root`` — absolute path to the checkout, so tests never depend on the
  current working directory.
* ``data_dir`` — the committed ``data/`` directory.
* ``load_json`` — a small loader that parses a committed JSON file and fails
  loudly if it is malformed. A dataset that has not been collected yet skips
  the test instead of erroring; the foundation milestone legitimately has no
  ``data/latest_offers.json``.
* ``tmp_data_tree`` — a throwaway copy of the data tree, laid out like the real
  repo (``tmp/data`` plus ``tmp/config``) so a test can mutate records and run
  the validation CLI without ever touching committed evidence.
* ``melbourne_tz`` — the display timezone, kept in one place.

No fixture here reaches the network.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
MELBOURNE = ZoneInfo("Australia/Melbourne")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root."""
    assert (REPO_ROOT / "pyproject.toml").is_file()
    return REPO_ROOT


@pytest.fixture(scope="session")
def data_dir(repo_root: Path) -> Path:
    """The committed data directory, even while it is still being populated."""
    return repo_root / "data"


@pytest.fixture(scope="session")
def melbourne_tz() -> ZoneInfo:
    """The timezone used for display; storage stays UTC."""
    return MELBOURNE


@pytest.fixture(scope="session")
def load_json(repo_root: Path, data_dir: Path) -> Callable[[str], Any]:
    """Return a loader for a committed JSON file.

    ``name`` may be relative to the repository root (``data/products.json``,
    ``fixtures/expected/product.json``) or relative to ``data/``
    (``products.json``, ``gift_cards/offers.json``).

    A file that exists must parse: malformed committed JSON is a test failure,
    not a skip. A file that does not exist yet skips the calling test, because
    several datasets only appear once their collector card lands.
    """

    def _load(name: str) -> Any:
        candidates = (repo_root / name, data_dir / name)
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            tried = ", ".join(str(candidate) for candidate in candidates)
            pytest.skip(f"{name} does not exist yet (tried {tried})")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{path} is not valid JSON: {exc}") from exc

    return _load


@pytest.fixture
def tmp_data_tree(tmp_path: Path, data_dir: Path, repo_root: Path) -> Path:
    """A mutable copy of the committed data tree, isolated under ``tmp_path``.

    Returns the copy's data directory. ``config/`` is copied alongside it
    because the validation CLI resolves configuration relative to the parent of
    the data directory. Snapshots are copied read-only in spirit: tests must
    mutate the copy, never the originals.
    """
    destination = tmp_path / "data"
    destination.mkdir(parents=True, exist_ok=True)
    if data_dir.is_dir():
        for source in sorted(data_dir.rglob("*.json")):
            target = destination / source.relative_to(data_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    config_source = repo_root / "config"
    if config_source.is_dir():
        config_target = tmp_path / "config"
        config_target.mkdir(parents=True, exist_ok=True)
        for source in sorted(config_source.glob("*.yml")):
            shutil.copy2(source, config_target / source.name)

    return destination
