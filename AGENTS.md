# Orchard (apple-price-au) — Agent Team Brief

Shared context for every agent on this project. Loaded into every session; read
it before touching code.

## What this project is

An **Australia-only Apple price tracker**. It collects Apple AU and Australian
retailer prices, tracks the Apple Education Store separately from retail,
detects deals, tracks gift-card and cashback value, and ranks purchase routes
(Apple retail, education, corporate EPP, business quote, trade-in, gift-card,
cashback).

It publishes a static site to GitHub Pages and runs on scheduled GitHub Actions.
AUD only. Melbourne for display, UTC ISO 8601 for storage.

Working brand: **Orchard** (provisional — see `docs/BRAND.md`). Repository slug:
`apple-price-au`.

## What it is not

- Not affiliated with, endorsed by or sponsored by Apple Inc.
- Not a price guarantee. Every price is an observation with a timestamp.
- No accounts, no payments, no automated purchasing, no mobile app, no AI in the
  hot path.

## The team

| Agent | Mode | Owns |
|---|---|---|
| `apple-director` | primary + subagent | the board, triage, dispatch, review, gates |
| `apple-scaffold` | subagent | `pyproject.toml`, `uv.lock`, `package.json`, `astro.config.mjs`, build config, `.env.example` |
| `apple-data` | subagent | `scripts/models/`, `scripts/validation/`, `scripts/deals/`, `scripts/gift_cards/`, `data/products.json`, `data/sellers.json`, `config/*.yml` |
| `apple-adapter` | subagent | `scripts/collectors/`, `scripts/matching/`, `fixtures/`, `data/snapshots/`, `data/matching/` |
| `apple-web` | subagent | `site/` in full |
| `apple-qa` | subagent | `tests/` beyond model tests, `.github/workflows/`, deploy verification, alerts |
| `apple-docs` | subagent | `README.md`, all of `docs/`, `AGENTS.md` |

Ownership is a hard boundary. Two agents must never edit the same file in the
same wave; if a card needs a file it does not own, it raises it to the director.

## The board

`.kanban/board.json` is the single source of truth, rendered to
`.kanban/board.md`. Edit the JSON through the CLI, never by hand.

```bash
python3 .kanban/kanban.py next                     # next actionable card
python3 .kanban/kanban.py list --status ready
python3 .kanban/kanban.py show APA-004
python3 .kanban/kanban.py claim APA-004 --owner apple-data
python3 .kanban/kanban.py move APA-004 in_review --note "validated, 21 sellers"
python3 .kanban/kanban.py block APA-004 --reason "blocked on robots finding"
python3 .kanban/kanban.py render
```

Flow: `backlog → ready → in_progress → in_review → done`, with `blocked` as an
escape hatch. Workers claim their own card. **Only the director moves cards to
`done`, `ready` or across owners.**

Card IDs are sequential `APA-NNN` in milestone order, so ranges shift as cards
are added. Do not assume a range: `python3 .kanban/kanban.py list` is the only
authority on what exists. Current head is around `APA-055`.

## Non-negotiables

These are not style preferences. Violating one is worse than stopping.

1. **Never invent a price, a discount, a stock level or a promotion.** Every
   number traces to a captured source with a URL and a timestamp. If you cannot
   capture it, mark it `unverified` and record what blocked you.
2. **Never present an unverified or stale price as current.** Freshness is
   computed from `captured_at` against `config/freshness.yml`; stale renders as
   "indicative".
3. **Never bypass authentication, CAPTCHAs, bot protection or a site's terms.**
   No proxies, no fingerprint spoofing, no headless-browser evasion. A policy
   denial results in the curated path, recorded as a deliberate decision.
4. **Never publish a low-confidence product match.** Below 0.85 confidence the
   offer goes to `data/matching/review_queue.json`.
5. **Never merge education pricing into retail pricing.** They are separate
   offer types with separate eligibility.
6. **Never double-count a discount.** Gift card + cashback + points + trade-in
   each apply once, on distinct spend.
7. **Validate before deploying.** A critical schema failure blocks the deploy.
8. **Never commit a secret.** Everything sensitive comes from the environment;
   `.env` is gitignored and `.env.example` holds no real values.
9. **Australian English** in all user-facing copy and docs: colour, catalogue,
   normalise, behaviour, organisation, prioritise, licence (noun).

## Dependency policy

`npm audit` is not a build gate. The site is fully static: it renders committed
JSON, accepts no user input and runs no server, so an advisory inside Astro's
build-time tree is a builder-machine exposure, not runtime attack surface.
Advisories are reviewed, recorded on the board with a reason, and acted on when
they gain a runtime path. Major-version upgrades are scheduled work, never a
knee-jerk reaction to an audit line. Current position: Astro 5.18.2 retained
deliberately (APA-054).

## Working a card

1. `python3 .kanban/kanban.py show <ID>` — the acceptance criteria are the
   definition of done, not a suggestion.
2. Claim it, then work on a branch: `git checkout -b agent/<card-id>-<slug>`.
   One card, one branch.
3. Make the change. Run the checks that apply: `uv run pytest -q`,
   `uv run ruff check`, `uv run ruff format --check`, `npm run build` in `site/`.
4. Commit conventionally: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `data:`.
   Reference the card ID in the body.
5. Report back: what changed, which acceptance criteria are met, what you could
   not do and why. Move the card to `in_review`. Do not merge to `main`.

## Data rules that outrank convenience

- `data/snapshots/` is append-only evidence. Never edit a snapshot in place.
- `data/latest_offers.json` is derived. Never hand-edit it.
- Curated feeds are first-class: they go through the same models, the same
  validation and the same freshness rules as automated collection.
- Seed data is not exempt. A seed product's RRP needs an evidence URL too.

## Local commands

```bash
uv sync                            # python 3.12 + deps
uv run pytest -q                   # whole python suite, no network
uv run python -m scripts.validation.validate_data
cd site && npm ci && npm run dev    # site
python3 .kanban/kanban.py render    # refresh board.md
```

## Legal

Code is MIT. Prices, trademarks and product names belong to their owners. The
site states plainly that it is not affiliated with Apple Inc. and that all
prices are indicative, captured at a point in time, and subject to change.