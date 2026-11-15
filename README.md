# Orchard

**Apple prices in Australia, actually checked.**

Orchard is an Australia-only Apple price tracker. Its job is to record what
Apple AU and Australian retailers charge, keep education pricing separate from
retail, detect deals, value gift cards and cashback, and rank the realistic ways
to buy a product.

Status: **Milestone 1 (foundation) in progress.** The domain models, a 20-product
seed catalogue, the 29-seller registry, the validation CLI, the generated JSON
Schemas and the Astro scaffold are in place. Collectors, the deal engine and the
public pages are Milestones 2–4. Nothing here is a live price feed yet.

## What it is

- A record of **observations**: a price, a seller, a source URL and the UTC time
  it was captured.
- Australia only, AUD only. `Australia/Melbourne` for display, UTC for storage.
- Static by design: the published site will be generated from committed JSON
  and served from GitHub Pages, with no runtime API.
- Deliberately small: no accounts, no payments, no purchasing.

## What it is not

- **Not affiliated with Apple Inc.** No endorsement, no sponsorship, no
  partnership.
- **Not a price guarantee.** Every price is what one page said at one moment.
  Prices change, stock disappears, and a retailer's terms always win.
- **Not a purchasing service.** Orchard never adds to a cart, never checks out
  and never acts on your behalf.
- **Not financial, legal or consumer advice.** Gift-card and cashback maths is
  arithmetic against stated terms, with the risks shown next to the number.
- **Not a review site.** It does not rank products by quality; it reports price
  and route.
- **Not an AI oracle.** No model sits in the pricing path. Every number is
  parsed from a source, and a human decides anything uncertain.

## The trust model

Five fields do the work. If one is missing, the price is not published as
current.

| Field | What it answers | Rule |
|---|---|---|
| `url` | Where did this come from? | Every offer links to the exact page captured. No URL, no offer. |
| `captured_at` | When was it true? | UTC ISO 8601 on every observation. Never inferred from a file timestamp. |
| `verification_status` | How much do we trust it? | `verified`, `curated_unverified`, `unverified` or `indicative`. Stored, not guessed at render. |
| `confidence` | Is it the right product? | Match confidence 0.00–1.00. Below 0.85 the offer goes to a human review queue and is withheld. |
| freshness | Is it still current? | `config/freshness.yml` sets a maximum age per seller class and category. Past it, the stored verdict becomes `indicative`. |

Consequences you can rely on:

- A price beyond its freshness budget, or one never verified, is labelled
  **indicative** and cannot win best price.
- Education pricing is never merged into retail pricing; eligibility is part of
  the price.
- Gift card, cashback, points and trade-in each apply once, on distinct spend.
  Nothing is double-counted.
- Points are shown as points, never as cash.

## Quickstart

```bash
uv sync
uv run pytest -q
uv run python -m scripts.validation.validate_data
cd site && npm ci && npm run dev
```

Availability, honestly:

| Command | State |
|---|---|
| `uv sync` | Works. Python 3.12, dependencies from `uv.lock`. |
| `uv run pytest -q` | Works. 114 tests pass, 2 skipped, no network. |
| `uv run python -m scripts.validation.validate_data` | Works. Exit `0` on the current tree, with 21 warnings for datasets and adapters not yet built. |
| `cd site && npm ci && npm run dev` | Works. Astro 5 with Tailwind 4; Node 22 or later. |

Two more commands that exist and are worth knowing:

```bash
uv run python -m scripts.models.export_schema --check
uv run ruff check
```

Validation exits `0` clean, `1` on a hard failure and `2` on an unexpected
crash. A hard failure blocks publish and deploy. The schema check exits `1` when
`data/schema/` is stale.

## Repository map

The milestone column says when a path arrives. Milestone 1 is in progress and
its paths exist; M2–M4 paths do not.

| Path | What lives there | Milestone |
|---|---|---|
| `scripts/models/` | Pydantic v2 models, enums, JSON Schema export — the schema contract | M1 |
| `scripts/validation/` | Dataset validation CLI, invariants and the freshness policy | M1 |
| `scripts/collectors/` | One adapter per source, shared rate limiter and robots gate | M2 |
| `scripts/matching/` | Layered product matching and the review queue | M2 |
| `scripts/deals/` | Deterministic deal detection | M3 |
| `scripts/gift_cards/` | Gift-card and effective-price maths | M3 |
| `scripts/alerts/` | Discord alert selection and dedupe | M4 |
| `data/products.json`, `data/sellers.json` | Seed catalogue (20 products) and source registry (29 sellers) | M1 |
| `data/evidence/` | Capture records backing each Apple AU RRP | M1 |
| `data/schema/` | Generated JSON Schema for every model — never hand-edited | M1 |
| `data/snapshots/` | Append-only raw captures, one directory per day | M2 |
| `data/latest_offers.json` | Derived published offer set — never hand-edited | M2 |
| `data/matching/` | Review queue and match decisions | M2 |
| `data/history/`, `data/deals/`, `data/gift_cards/`, `data/health/` | Derived feeds | M3/M4 |
| `config/freshness.yml` | Freshness budget by seller type and category, with a per-record fallback | M1 |
| `config/deal_rules.yml`, `config/alerts.yml` | Deal and alert thresholds | M3/M4 |
| `site/` | Astro frontend, static output | M1 scaffold, M3 pages |
| `fixtures/` | Saved retailer HTML and expected parses, for offline tests | M2 |
| `tests/` | pytest suite, offline by default | M1 |
| `.github/workflows/` | Validation (M1), collection, deploy and alert workflows | M1/M4 |
| `.kanban/` | The team board — the source of truth for work in flight | M1 |

## Documentation

| Document | Covers | State |
|---|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Pipeline stages, trust boundaries, adapter isolation, determinism, why no database | Written for the full pipeline; stages marked by milestone |
| [DATA-MODEL.md](docs/DATA-MODEL.md) | Every field, type, nullability and enum from `scripts/models/`; the generated schemas; the SQLite target DDL | Current, generated from the code |
| [SOURCE-REGISTRY.md](docs/SOURCE-REGISTRY.md) | All 29 sellers from `data/sellers.json`, methods, rate limits, policy status and risks | Current; **no robots/terms finding recorded** |
| [PRD.md](docs/PRD.md) | Problem, users, scope, non-scope, success measures, MVP acceptance | Current |
| [BRAND.md](docs/BRAND.md) | Provisional name, trademark position, rename checklist | Current |
| [AGENTS.md](AGENTS.md) | Team, board usage, non-negotiables, how to work a card | Current |
| `docs/OPERATIONS.md` | Runbook, collection and deployment procedures | Planned (M4, `APA-049`) |
| `docs/GIFT-CARD-RULES.md` | Gift-card valuation rules referenced by `.env.example` | Planned (M3, `APA-031`) |
| `docs/ROADMAP.md` | Future work beyond Milestone 4 | Planned |

## How the repository is built

Work is tracked on a Kanban board in `.kanban/`, with one agent per file
ownership boundary. The board, not a chat log, decides what is in progress.
Before changing anything, read `AGENTS.md`; the non-negotiables there are hard
rules, not preferences.

```bash
python3 .kanban/kanban.py next
python3 .kanban/kanban.py show APA-008
```

## Disclaimers

- Orchard is **not affiliated with, endorsed by or sponsored by Apple Inc.**
  Apple, Mac, MacBook, iPhone, iPad, Apple Watch, AirPods, Apple TV and HomePod
  are trademarks of Apple Inc., used here only to describe the products being
  tracked.
- All prices are **indicative observations** captured at a point in time and
  subject to change without notice. Confirm the price, stock, warranty and
  delivery terms on the retailer's own page before buying.
- Retailer terms, promotion conditions, gift-card terms and cashback exclusions
  govern your purchase. Orchard's arithmetic does not override them.
- Education pricing requires eligibility verification by Apple or its vendor.
  Orchard does not verify eligibility and does not sell education pricing.
- Links to retailers are not endorsements, and no referral arrangement is
  claimed.
- The software is provided "as is", without warranty of any kind.

## Licence

Code is MIT. Prices, trademarks and product names belong to their owners. Text
and data in this repository are published for personal reference; if you reuse
the data, keep the attribution and the capture timestamps intact.