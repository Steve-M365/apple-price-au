"""Board integrity tests.

``.kanban/board.json`` is the team's process infrastructure: the director
dispatches from it and every worker reports against it. A malformed card is
therefore a real defect, not a documentation nit. These tests guard the schema
the CLI already assumes, plus the invariants the team relies on when reading the
rendered board.
"""

from __future__ import annotations

import json
import re
import runpy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

CARD_ID = re.compile(r"^APA-\d{3}$")
REQUIRED_FIELDS = ("id", "title", "status", "owner", "priority", "type", "acceptance")


def _kanban_module(repo_root: Path) -> dict[str, Any]:
    return runpy.run_path(str(repo_root / ".kanban" / "kanban.py"))


@pytest.fixture(scope="module")
def board(repo_root: Path) -> dict[str, Any]:
    path = repo_root / ".kanban" / "board.json"
    assert path.is_file(), "board.json is missing; the board is the source of truth"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def cards(board: dict[str, Any]) -> list[dict[str, Any]]:
    raw = board.get("cards")
    assert isinstance(raw, list) and raw, "board.json has no cards"
    return cast(list[dict[str, Any]], raw)


def test_every_card_has_the_required_fields(cards: list[dict[str, Any]]) -> None:
    for card in cards:
        missing = [field for field in REQUIRED_FIELDS if field not in card]
        assert not missing, f"{card.get('id', '<no id>')} is missing {missing}"


def test_card_ids_are_unique_and_well_formed(cards: list[dict[str, Any]]) -> None:
    identifiers = [card["id"] for card in cards]
    assert len(identifiers) == len(set(identifiers)), "duplicate card ids on the board"
    malformed = [identifier for identifier in identifiers if not CARD_ID.match(identifier)]
    assert not malformed, f"card ids must look like APA-001, found {malformed}"


def test_status_owner_priority_and_type_come_from_the_cli_vocabulary(
    cards: list[dict[str, Any]], repo_root: Path
) -> None:
    vocabulary = _kanban_module(repo_root)
    statuses = set(cast(list[str], vocabulary["STATUSES"]))
    owners = set(cast(list[str], vocabulary["OWNERS"]))
    priorities = set(cast(list[str], vocabulary["PRIORITIES"]))
    types = set(cast(list[str], vocabulary["TYPES"]))

    for card in cards:
        assert card["status"] in statuses, f"{card['id']} has unknown status {card['status']!r}"
        assert card["owner"] in owners, f"{card['id']} has unknown owner {card['owner']!r}"
        assert card["priority"] in priorities, f"{card['id']} has unknown priority"
        assert card["type"] in types, f"{card['id']} has unknown type"


def test_every_card_carries_at_least_one_acceptance_criterion(
    cards: list[dict[str, Any]],
) -> None:
    for card in cards:
        criteria = card["acceptance"]
        assert isinstance(criteria, list), f"{card['id']} acceptance must be a list"
        assert criteria, f"{card['id']} has no acceptance criteria, so 'done' is unverifiable"
        assert all(isinstance(item, str) and item.strip() for item in criteria), (
            f"{card['id']} has an empty acceptance criterion"
        )


def test_every_done_card_records_how_it_was_closed(cards: list[dict[str, Any]]) -> None:
    for card in cards:
        if card["status"] != "done":
            continue
        notes = card.get("notes")
        assert isinstance(notes, list) and notes, (
            f"{card['id']} is done but records no closing note"
        )
        assert all(isinstance(note, str) and note.strip() for note in notes)


def test_board_records_a_project_and_a_render(repo_root: Path, board: dict[str, Any]) -> None:
    assert board.get("project"), "board.json does not name the project"
    assert (repo_root / ".kanban" / "board.md").is_file(), "the rendered board.md is missing"


# --------------------------------------------------------------------------- #
# Shared fixture sanity: these fixtures are the harness every later test builds
# on, so a regression in conftest.py must fail loudly here.
# --------------------------------------------------------------------------- #


def test_tmp_data_tree_is_a_mutable_copy(
    repo_root: Path, tmp_data_tree: Path, load_json: Any
) -> None:
    if not (repo_root / "data" / "products.json").is_file():
        pytest.skip("data/products.json has not been committed yet")
    products = tmp_data_tree / "products.json"
    assert products.is_file()
    assert products.resolve().parent != (repo_root / "data").resolve()
    assert (tmp_data_tree.parent / "config" / "validation.yml").is_file()

    committed = load_json("products.json")
    assert isinstance(committed, dict)

    # Mutating the copy must not touch the real tree.
    products.write_text('{"products": []}\n', encoding="utf-8")
    assert json.loads((repo_root / "data" / "products.json").read_text(encoding="utf-8"))


def test_load_json_skips_a_dataset_that_has_not_been_collected_yet(load_json: Any) -> None:
    with pytest.raises(pytest.skip.Exception) as skipped:
        load_json("definitely-not-collected-yet.json")
    assert "does not exist" in str(skipped.value)


def test_melbourne_timezone_tracks_daylight_saving(melbourne_tz: Any) -> None:
    summer = datetime(2026, 11, 15, 12, 0, tzinfo=melbourne_tz)
    winter = datetime(2026, 7, 15, 12, 0, tzinfo=melbourne_tz)
    assert summer.utcoffset() == timedelta(hours=11)
    assert winter.utcoffset() == timedelta(hours=10)
