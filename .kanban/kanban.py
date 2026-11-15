#!/usr/bin/env python3
"""Kanban board for the Orchard (apple-price-au) agent team.

The board is the single source of truth for what the team is building. The
director agent picks work off it, workers report progress onto it, and every
card carries acceptance criteria so "done" is verifiable rather than asserted.

State lives in ``board.json``. ``board.md`` is a rendered view for humans and
is regenerated with ``render``.

Usage
-----
    python3 .kanban/kanban.py list [--status S] [--owner O] [--type T]
    python3 .kanban/kanban.py show <ID>
    python3 .kanban/kanban.py next [--owner O]
    python3 .kanban/kanban.py claim <ID> --owner <agent>
    python3 .kanban/kanban.py move <ID> <status> [--owner O] [--note TEXT]
    python3 .kanban/kanban.py add --title T [--type T] [--priority P]
                                    [--owner O] [--desc D] [--acceptance A]
    python3 .kanban/kanban.py block <ID> --reason TEXT
    python3 .kanban/kanban.py stats
    python3 .kanban/kanban.py render

Statuses follow the flow: backlog -> ready -> in_progress -> in_review -> done.
``blocked`` is an escape hatch used from any status.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BOARD_DIR = Path(__file__).resolve().parent
BOARD_FILE = BOARD_DIR / "board.json"
RENDER_FILE = BOARD_DIR / "board.md"

STATUSES = ["backlog", "ready", "in_progress", "in_review", "done", "blocked"]
FLOW = ["backlog", "ready", "in_progress", "in_review", "done"]
PRIORITIES = ["P0", "P1", "P2", "P3"]
TYPES = ["bug", "feature", "chore", "docs", "research"]
OWNERS = [
    "apple-director",
    "apple-scaffold",
    "apple-data",
    "apple-adapter",
    "apple-web",
    "apple-qa",
    "apple-docs",
    "unassigned",
]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BoardError(Exception):
    """Raised for invalid board operations."""


def load() -> dict:
    if not BOARD_FILE.exists():
        raise BoardError(f"board file not found: {BOARD_FILE}")
    with BOARD_FILE.open(encoding="utf-8") as fh:
        board = json.load(fh)
    board.setdefault("cards", [])
    return board


def save(board: dict) -> None:
    """Write the board atomically so a crashed agent cannot truncate it."""
    board["updated"] = now()
    BOARD_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(BOARD_DIR), prefix=".board-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(board, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, BOARD_FILE)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def find(board: dict, card_id: str) -> dict:
    for card in board["cards"]:
        if card["id"].upper() == card_id.upper():
            return card
    raise BoardError(f"card not found: {card_id}")


def next_id(board: dict) -> str:
    highest = 0
    for card in board["cards"]:
        suffix = card["id"].split("-")[-1]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"APA-{highest + 1:03d}"


def sort_key(card: dict) -> tuple:
    status_rank = STATUSES.index(card.get("status", "backlog"))
    prio_rank = PRIORITIES.index(card.get("priority", "P3"))
    return (status_rank, prio_rank, card.get("id", ""))


def cmd_list(board: dict, args: argparse.Namespace) -> int:
    cards = board["cards"]
    if args.status:
        cards = [c for c in cards if c.get("status") == args.status]
    if args.owner:
        cards = [c for c in cards if c.get("owner") == args.owner]
    if args.type:
        cards = [c for c in cards if c.get("type") == args.type]
    if not cards:
        print("no cards match")
        return 0
    for card in sorted(cards, key=sort_key):
        print(f"{card['id']}  {card.get('priority',''):3s} {card.get('status',''):12s} "
              f"{card.get('owner',''):15s} {card.get('title','')}")
    return 0


def cmd_show(board: dict, args: argparse.Namespace) -> int:
    card = find(board, args.id)
    print(json.dumps(card, indent=2, ensure_ascii=False))
    return 0


def cmd_next(board: dict, args: argparse.Namespace) -> int:
    """Pick the next card a worker should pick up.

    Prefers ``ready`` over ``backlog`` so a card only starts once it has been
    triaged, and prefers ``in_progress`` cards that are mid-flight.
    """
    pool = [c for c in board["cards"] if c.get("status") in ("ready", "in_progress")]
    if args.owner:
        owned = [c for c in pool if c.get("owner") == args.owner]
        pool = owned or pool
    if not pool:
        print("nothing ready")
        return 0
    pool.sort(key=lambda c: (0 if c.get("status") == "in_progress" else 1,
                             PRIORITIES.index(c.get("priority", "P3")),
                             c.get("id", "")))
    print(json.dumps(pool[0], indent=2, ensure_ascii=False))
    return 0


def cmd_claim(board: dict, args: argparse.Namespace) -> int:
    card = find(board, args.id)
    if card.get("status") in ("done",):
        raise BoardError(f"{card['id']} is already done")
    card["status"] = "in_progress"
    card["owner"] = args.owner
    card["updated"] = now()
    card.setdefault("notes", []).append(f"claimed by {args.owner} at {now()}")
    save(board)
    print(f"{card['id']} -> in_progress ({args.owner})")
    return 0


def cmd_move(board: dict, args: argparse.Namespace) -> int:
    card = find(board, args.id)
    if args.status not in STATUSES:
        raise BoardError(f"invalid status {args.status!r}; use one of {', '.join(STATUSES)}")
    if args.owner:
        card["owner"] = args.owner
    previous = card.get("status", "backlog")
    card["status"] = args.status
    card["updated"] = now()
    note = args.note or f"{previous} -> {args.status}"
    card.setdefault("notes", []).append(note)
    if args.branch:
        card["branch"] = args.branch
    if args.pr:
        card["pr"] = args.pr
    save(board)
    print(f"{card['id']} {previous} -> {args.status}")
    return 0


def cmd_block(board: dict, args: argparse.Namespace) -> int:
    card = find(board, args.id)
    card["status"] = "blocked"
    card["updated"] = now()
    card.setdefault("notes", []).append(f"BLOCKED: {args.reason}")
    save(board)
    print(f"{card['id']} -> blocked")
    return 0


def cmd_add(board: dict, args: argparse.Namespace) -> int:
    card = {
        "id": next_id(board),
        "title": args.title,
        "type": args.type,
        "priority": args.priority,
        "status": args.status,
        "owner": args.owner,
        "labels": args.label or [],
        "description": args.desc or "",
        "acceptance": args.acceptance or [],
        "created": now(),
        "updated": now(),
        "notes": [],
    }
    board["cards"].append(card)
    save(board)
    print(f"created {card['id']}")
    return 0


def cmd_stats(board: dict, args: argparse.Namespace) -> int:
    counts = {status: 0 for status in STATUSES}
    for card in board["cards"]:
        counts[card.get("status", "backlog")] = counts.get(card.get("status", "backlog"), 0) + 1
    print(f"total: {len(board['cards'])}")
    for status in STATUSES:
        print(f"  {status:12s} {counts.get(status, 0)}")
    return 0


def cmd_render(board: dict, args: argparse.Namespace) -> int:
    """Render the human-readable board view."""
    lines = [
        "# Orchard (apple-price-au) - Team Kanban",
        "",
        "Generated by `.kanban/kanban.py render`. Edit `board.json` or use the CLI,",
        "never this file.",
        "",
        f"_Last updated: {board.get('updated', 'unknown')}_",
        "",
    ]
    by_status: dict[str, list[dict]] = {status: [] for status in STATUSES}
    for card in board["cards"]:
        by_status.setdefault(card.get("status", "backlog"), []).append(card)

    for status in FLOW + (["blocked"] if by_status.get("blocked") else []):
        cards = sorted(by_status.get(status, []), key=sort_key)
        if not cards:
            continue
        lines.append(f"## {status.replace('_', ' ').title()} ({len(cards)})")
        lines.append("")
        for card in cards:
            lines.append(f"### {card['id']} - {card.get('title','')}")
            meta = [card.get("priority", ""), card.get("type", ""), card.get("owner", "")]
            lines.append(f"`{' | '.join(m for m in meta if m)}`")
            if card.get("labels"):
                lines.append("")
                lines.append("Labels: " + ", ".join(card["labels"]))
            if card.get("description"):
                lines.append("")
                lines.append(card["description"])
            if card.get("acceptance"):
                lines.append("")
                lines.append("Acceptance:")
                for item in card["acceptance"]:
                    lines.append(f"- [ ] {item}")
            if card.get("branch"):
                lines.append("")
                lines.append(f"Branch: `{card['branch']}`")
            if card.get("pr"):
                lines.append("")
                lines.append(f"PR: {card['pr']}")
            if card.get("notes"):
                lines.append("")
                lines.append("Notes:")
                for note in card["notes"]:
                    lines.append(f"- {note}")
            lines.append("")
        lines.append("---")
        lines.append("")

    RENDER_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"rendered {RENDER_FILE}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Orchard team kanban")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list cards")
    p_list.add_argument("--status")
    p_list.add_argument("--owner")
    p_list.add_argument("--type")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="show one card as JSON")
    p_show.add_argument("id")
    p_show.set_defaults(func=cmd_show)

    p_next = sub.add_parser("next", help="show the next card to work on")
    p_next.add_argument("--owner")
    p_next.set_defaults(func=cmd_next)

    p_claim = sub.add_parser("claim", help="claim a card")
    p_claim.add_argument("id")
    p_claim.add_argument("--owner", required=True)
    p_claim.set_defaults(func=cmd_claim)

    p_move = sub.add_parser("move", help="move a card to a status")
    p_move.add_argument("id")
    p_move.add_argument("status")
    p_move.add_argument("--owner")
    p_move.add_argument("--note")
    p_move.add_argument("--branch")
    p_move.add_argument("--pr")
    p_move.set_defaults(func=cmd_move)

    p_block = sub.add_parser("block", help="mark a card blocked")
    p_block.add_argument("id")
    p_block.add_argument("--reason", required=True)
    p_block.set_defaults(func=cmd_block)

    p_add = sub.add_parser("add", help="add a card")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--type", default="feature", choices=TYPES)
    p_add.add_argument("--priority", default="P2", choices=PRIORITIES)
    p_add.add_argument("--status", default="backlog", choices=STATUSES)
    p_add.add_argument("--owner", default="unassigned", choices=OWNERS)
    p_add.add_argument("--label", action="append")
    p_add.add_argument("--desc")
    p_add.add_argument("--acceptance", action="append")
    p_add.set_defaults(func=cmd_add)

    p_stats = sub.add_parser("stats", help="card counts by status")
    p_stats.set_defaults(func=cmd_stats)

    p_render = sub.add_parser("render", help="regenerate board.md")
    p_render.set_defaults(func=cmd_render)

    args = parser.parse_args()
    try:
        board = load()
        return args.func(board, args)
    except BoardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
