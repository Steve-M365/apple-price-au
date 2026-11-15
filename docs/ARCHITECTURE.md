# Architecture

**Status:** describes the target pipeline with the milestone that delivers each
stage. Milestone 1 is in progress; stages marked M2–M4 do not exist yet and are
labelled as planned throughout.

**Reading rule:** a stage or row marked M2–M4 describes intended behaviour.
Where the text says a stage "writes", "fetches" or "blocks", read it as "will
write", "will fetch", "will block". Only the validation stage exists after M1.

## Design principles

1. **Evidence over convenience.** Every number is a row captured from a URL at a
   timestamp. Snapshots are the evidence layer; everything else is a projection
   that can be rebuilt from them.
2. **Derived is never hand-edited.** `data/latest_offers.json`,
   `data/deals/*`, `data/gift_cards/current_offers.json`,
   `data/history/*` and `data/health/*` are outputs. Editing one by hand is a
   defect, and validation recomputes what it can to catch it.
3. **Fail closed, fail small.** A bad source degrades to one missing retailer,
   never a missing run. A schema failure blocks publish and deploy.
4. **Deterministic.** Same inputs produce byte-identical outputs. No wall clock,
   no randomness, no dictionary ordering, no locale in derived files.
5. **No database until the files hurt.** Phase 1 is JSON in git, and git is the
   transaction log.
6. **No bypass.** If a source says no, the answer is a curated feed and a
   recorded decision, not a workaround.

## Pipeline

```
                  ┌──────────────────────── GitHub Actions (M4) ────────────────────────┐
   schedule ─────▶│                                                                     │
   dispatch       │  collect ─▶ snapshot ─▶ normalise ─▶ match ─▶ validate ─▶ deal      │
                  │    M2         M2           M2          M2       M1         M3       │
                  │                                                        │            │
                  │                                                        ▼            │
                  │                              publish (M2/M3) ◀──────────┘            │
                  │                                    │                                │
                  │                                    ▼                                │
                  │                      deploy (M1) ─▶ alert (M6, planned)            │
                  └─────────────────────────────────────────────────────────────────────┘
                                                       │
                    data/snapshots/                    │  data/*.json (derived)
                    append-only, hashed                ▼
                                                  site build (M1 scaffold, M3 pages)
                                                       │
                                                       ▼
                                                 GitHub Pages
```

The pipeline runs as separate jobs, not one process. Collection jobs write
snapshots and commit; a dependent job derives and publishes; a deploy job builds
the site from the committed JSON. Each arrow is a commit boundary, so a failure
is visible and resumable.

### Stage summary

| # | Stage | Milestone | Input | Output | Failure behaviour |
|---|---|---|---|---|---|
| 1 | collect | M2 | registry entry, rate limit, robots decision | raw payload per source | per-source status; run continues |
| 2 | snapshot | M2 | raw payload | `data/snapshots/YYYY-MM-DD/<seller_id>.json` + hash | existing snapshot never overwritten |
| 3 | normalise | M2 | snapshot records | typed `Offer` / `EducationOffer` records | record rejected with a typed error, counted |
| 4 | match | M2 | normalised offers | offers with `product_id`, `confidence`, `match_method` | `< 0.85` goes to the review queue |
| 5 | validate | M1 | all committed datasets | report + exit code | hard failure stops publish and deploy |
| 6 | deal | M3 | validated offers, history, rules | `Deal` records | no evidence or expired → not emitted |
| 7 | publish | M2/M3 | validated + derived records | `latest_offers.json`, feeds | atomic write, previous file kept on failure |
| 8 | deploy | M1 (early) | committed JSON, site build | static site on Pages | failed gate leaves the last good site live |
| 9 | alert | M4 | current deals, `sent.json` | Discord message | missing webhook is a warning, not a failure |

### 1. Collect (M2)

- **Where:** `scripts/collectors/`, one module per source named in
  `docs/SOURCE-REGISTRY.md`; shared protocol, rate limiter, robots gate and
  cache in `scripts/collectors/base.py` (`APA-017`).
- **Input:** an enabled registry row, the configured `HTTP_USER_AGENT`, the
  per-source rate limit, and a cached robots decision.
- **Output:** the raw response body plus metadata (`url`, `http_status`,
  `fetched_at`, `etag`, `source_hash`) written through the snapshot writer.
- **Failure behaviour:** each source returns a typed outcome — `ok`,
  `blocked_by_policy`, `network_error`, `parse_error`, `rate_limited`,
  `skipped`, `disabled`. `RobotsDenied` is distinct from a network error and
  cannot fall through to a parser. A 403 is retried a bounded number of times
  and then recorded, never escalated. One source failing never fails the job
  (`APA-050` reports it).
- **Not yet:** no adapter exists. The package is an empty module until `APA-011`.

### 2. Snapshot (M2)

- **Where:** the snapshot writer in `scripts/collectors/` (`APA-015`).
- **Input:** raw payloads from one run.
- **Output:** `data/snapshots/YYYY-MM-DD/<seller_id>.json` containing the
  captured records, `source_hash` (SHA-256 of the payload), `captured_at` and
  `run_id`; plus `data/runs/<run_id>.json` validating against `RunManifest`.
- **Failure behaviour:** append-only. An existing snapshot path is never
  overwritten; a second run on the same day writes a distinct file or refuses
  with a clear error. A partial run still writes its manifest, with
  `status: "partial"`.
- **Why it matters:** this is the only layer that is evidence. Deals, history and
  best-price claims must all be reproducible from here.

### 3. Normalise (M2)

- **Where:** `scripts/collectors/`, with the identifier rules and models owned by
  `scripts/models/` and `APA-013`.
- **Input:** snapshot records, `data/products.json`, `data/sellers.json`.
- **Output:** `Offer` or `EducationOffer` objects: integer-normalised storage and
  memory, lowercase colour, AUD amount as `Decimal`, explicit `stock_status`,
  and a `product_id` if the title resolved to a catalogue row.
- **Failure behaviour:** an unparseable or contradictory record raises a typed
  `NormaliseError` and is counted in the run manifest; it never becomes a
  partially filled offer. Unknown currency or negative price is rejected at the
  model layer.

### 4. Match (M2)

- **Where:** `scripts/matching/` (`APA-026`, `APA-027`).
- **Input:** normalised offers and the catalogue.
- **Output:** `product_id`, `confidence`, `match_method`, `config_diff`; unmatched
  or low-confidence records become `MatchCandidate`s in
  `data/matching/review_queue.json`.
- **Failure behaviour:** layers run in a fixed order — Apple SKU, GTIN,
  normalised configuration, fuzzy title, manual. A SKU hit never falls through
  to fuzzy. Anything below `0.85` is withheld from `latest_offers.json`. The
  run continues; the queue grows and is reported as a delta.

### 5. Validate (M1)

- **Where:** `scripts/validation/validate_data.py`, run as
  `uv run python -m scripts.validation.validate_data` (`APA-003`).
- **Input:** every committed dataset, the models, `config/freshness.yml`.
- **Output:** human-readable report on stdout and, with `--json`, a
  machine-readable report; exit code `0` clean, `1` hard failure, `2` crash.
- **Failure behaviour:** a hard failure blocks publish and deploy. Soft failures
  are warnings in the report and the job summary, never silent. The validator
  is deliberately independent of the producers: it re-reads files from disk and
  does not trust in-memory state.
- **Checks:** schema conformance, referential integrity (`product_id`,
  `seller_id`), required freshness fields, duplicate IDs, policy-gate
  consistency, recomputed derived values, no education pricing in retail offers.
- **This is the only pipeline stage that exists in Milestone 1.**

### 6. Deal (M3)

- **Where:** `scripts/deals/` (`APA-030`), thresholds in
  `config/deal_rules.yml`, history from `scripts/` history engine (`APA-029`).
- **Input:** validated current offers, per-product price series built from
  snapshots, gift-card and cashback offers, rule configuration.
- **Output:** `Deal` records with the eleven required fields, `rule_id`,
  `rule_version`, `expires_at`.
- **Failure behaviour:** a deal without an evidence URL and `captured_at` is not
  emitted. An expired promotion is not current. Out-of-stock periods do not
  register as price drops. A rule change changes output by design, and golden
  files are updated with the version bump.

### 7. Publish (M2 for offers, M3 for feeds)

- **Where:** the publish writers under `scripts/` (`APA-028`, `APA-033`).
- **Input:** validated offers, matches and overrides, deal and gift-card output.
- **Output:** `data/latest_offers.json`, `data/deals/current_deals.json` and
  `history.json`, `data/gift_cards/current_offers.json`, `data/health/*`,
  `data/history/<product_id>.json`.
- **Failure behaviour:** publish runs only after validation passes. Writes are
  atomic (temp file + rename) and sorted deterministically. Only
  published-confidence offers appear. Best price per product is derived, never
  written by hand.

### 8. Deploy (implemented, pulled forward from M4 to M1)

- **Where:** `.github/workflows/deploy-site.yml`. This landed at the end of M1
  so the published site could be verified end to end; the data-driven parts of
  the stage still arrive with the collectors.
- **Input:** committed JSON, the site source.
- **Output:** a GitHub Pages artefact and a live site whose footer records the
  data commit SHA.
- **Failure behaviour:** `deploy-site` depends on validation. A schema failure
  provably blocks the deploy, and the previously deployed site stays live. The
  artefact upload is atomic.

### 9. Alert (M4)

- **Where:** `scripts/alerts/` and the workflow summary (`APA-047`, `APA-048`).
- **Input:** current deals, `config/alerts.yml`, `data/alerts/sent.json`.
- **Output:** a Discord message per new qualifying deal, with product, seller,
  current and previous price, saving, effective price, source, captured time,
  expiry and link; dedupe state appended to `sent.json`.
- **Failure behaviour:** a missing `DISCORD_WEBHOOK_URL` exits 0 with a warning.
  Alerts are at-most-once per deal: repeat runs send nothing. Alert failure
  never blocks publish or deploy.

## Why no database in Phase 1

The unit of truth is a small, reviewable file under `data/`, and the reviewer is
a pull request diff. That gives:

- **Reviewability.** A price change or a banned promotion shows up as a line in
  a diff. A database row change does not.
- **Reproducibility.** Checking out a commit reproduces the exact site and the
  exact published numbers. There is no external state to restore.
- **No infrastructure.** No server, no credentials, no backup regime, no
  connection pool in CI. The only secrets are a webhook and optional API keys.
- **Atomic history.** Git is the append-only log, with blame and revert for
  free.
- **Simple static hosting.** The site build reads files; there is no runtime API
  to keep alive.

The costs are real and accepted for now: whole-file rewrites, no joins, no
indexes, and a repository that grows with snapshots. The mitigations are
deterministic regeneration, 90-day snapshot retention with compact history
series (`APA-052`), and a CI size guard.

Migration path (detail and DDL in
[DATA-MODEL.md](DATA-MODEL.md#sqlite-target-ddl)):

1. **SQLite** becomes a rebuildable build artefact once joins or scans hurt.
   JSON remains the committed contract; the database is loaded from JSON and
   never hand-edited.
2. **DuckDB over Parquet** exports of snapshots for history analysis once the
   report needs months of data.
3. Any move is proven by a golden test: same inputs, byte-identical published
   JSON.

Snapshots stay append-only in every phase. A database is an index over evidence,
not a replacement for it.

## Append-only snapshot model

- One directory per capture date: `data/snapshots/YYYY-MM-DD/`.
- One file per source per run: `<seller_id>.json` (a second run the same day
  disambiguates by `run_id`).
- Every record carries `source_hash` (SHA-256 of the raw payload), `captured_at`
  and `run_id`.
- Files are immutable. Corrections are new captures, not edits. A bad snapshot is
  quarantined by a follow-up record, never rewritten.
- Derived series read snapshots and never write back to them.
- `data/history/<product_id>.json` is a compact projection; pruning old
  snapshots must not change it (`APA-052`).

## Adapter isolation

- One adapter per source, implementing one protocol (`APA-017`): fetch, parse,
  emit typed records.
- Adapters never import each other. Shared behaviour lives in the base module
  only.
- The base module owns rate limiting, retries with jitter, conditional requests,
  the disk cache and the robots gate. An adapter cannot opt out.
- Failure is contained by construction: the runner catches per-source outcomes,
  records them in the manifest, and proceeds. One retailer cannot fail the run.
- Concurrency is capped (`COLLECTOR_MAX_CONCURRENCY`, default 2) so politeness
  is a global property, not a per-adapter opinion.
- A policy denial produces `blocked_by_policy` and a curated path, recorded in
  the registry and the adapter health file — not a quiet workaround.

## Trust boundaries

| Boundary | Untrusted side | Control |
|---|---|---|
| B1 network → adapter | retailer HTML, JSON, headers | fixed UA, rate limit, robots gate, no JS execution, no CAPTCHA solving |
| B2 adapter → normaliser | parsed field values | typed records only; price, currency and stock are validated before use |
| B3 matcher → publisher | product identity | confidence ≥ 0.85, explicit method, human overrides for the rest |
| B4 producers → validator | everything written by a collector | independent re-read from disk; producers cannot mark their own work valid |
| B5 repository → site | committed JSON | site reads files only; no write path; no runtime API |
| B6 human review | overrides file | append-only decisions with reviewer, timestamp, reason and source hash |
| B7 secrets | environment | `.env` gitignored, `.env.example` has no real values, no secret in a snapshot or log |

Additional rules: a marketplace listing is not trusted because it is on a big
site — `sold_by` is captured and third-party listings default to review. A
grey-import listing carries the flag into every projection. An education price
is never merged into a retail price, because eligibility is part of the price.

## Determinism requirements

The suite must be able to assert that a second run over the same snapshots
produces byte-identical published files (`APA-028`, `APA-029`, `APA-033`).

- **Stable sort.** Every list has an explicit key and tiebreaker: products by
  `product_id`, offers by `(product_id, price_aud, seller_id)`, deals by
  `(first_detected_at, deal_id)`, routes by `(rank, route_id)`.
- **No wall clock in derived output.** `generated_at` and `evaluated_at` are
  passed in by the caller and written as data. Freshness is decided at publish,
  not at render. The site never calls the clock to decide whether a price is
  current.
- **No randomness.** No random sampling in published output. Run IDs use a
  recorded timestamp plus a content-derived suffix, not `uuid4`.
- **Canonical serialisation.** UTF-8, `\n` newlines, two-space indent, keys in
  model order, money as decimal strings. Diff noise is a bug.
- **Hash the inputs.** `config_hash` in the manifest covers registry,
  configuration and rule versions.
- **Idempotent regeneration.** A second identical run produces no git diff. CI
  asserts this on the golden fixtures.
- **No locale dependence.** Formatting for humans happens in the site layer,
  never in data files.

## Static-site projection (M3)

- The site is planned as Astro, built to `site/dist`, deployed to GitHub Pages.
  Only the empty scaffold exists after M1.
- It reads **committed JSON only**, at build time, through a thin typed loader.
  There is no runtime API, no database and no client fetch to a retailer.
- Search is served from a prebuilt index under 20 KB gzipped (`APA-036`), not a
  service.
- Baseline content renders without JavaScript; the chart and the calculator are
  progressive enhancements (`APA-037`, `APA-040`).
- The build is offline-capable: no network call at build time, so a build is
  reproducible from the commit alone.
- Freshness labels come from published `verification_status` and `evaluated_at`.
  A stale price renders as "indicative" and cannot win best price.
- The footer records the data commit SHA so a reader can tell which data produced
  the page (`APA-046`).

## Environment and deployment

| Concern | Decision |
|---|---|
| Runtime | Python 3.12 via `uv`; Node 20+ for the site, pinned when `APA-009` lands |
| Schedules | GitHub Actions, separate workflows for Apple, retailers, gift cards, deals, deploy, validation and a nightly full refresh (`APA-045`) |
| Concurrency | one in-flight run per workflow; collection capped at 2 concurrent fetches |
| Commits | only changed data files, conventional message, card ID in the body |
| Secrets | `DISCORD_WEBHOOK_URL`; optional Amazon PA-API keys; nothing else |
| Rollback | revert the data commit, redeploy; snapshots are immutable so this is safe |
| Observability | run manifest, job summary, adapter health, data-quality report |

## What Milestone 1 delivers

- Domain models and exported JSON Schema (`scripts/models/`, `data/schema/`).
- The validation CLI and its hard/soft failure split.
- `data/sellers.json`, `data/products.json`, `config/freshness.yml`.
- The pytest harness with an offline default.
- An Astro build with a placeholder page and no prices.
- This document set.

No collector, no snapshot, no deal engine, no deployed site, no alert exists
after M1. Those are M2–M4 and are labelled as planned wherever they appear.