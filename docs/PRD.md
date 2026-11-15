# Product requirements

**Status:** current. Phase 1 means launch scope, delivered across Milestones 1–4.
Anything not listed under scope is out.

## Problem

Buying Apple hardware in Australia is a pricing puzzle with six answers and no
working:

- Apple AU's own price, which moves quietly and rarely;
- retailer sale, clearance and bundle prices, which move often and expire;
- education pricing, which is real but ring-fenced by eligibility;
- gift-card promotions, which pay in points or discounted cards rather than in
  the headline price;
- cashback, which pays later, sometimes not at all;
- corporate EPP, business quotes and trade-in, which are quotes, not prices.

The information exists, badly served:

1. **Deal sites omit the evidence.** A screenshot and "was $1,299" is not a
   source URL and a timestamp.
2. **Stale prices are presented as current.** A page cached three weeks ago
   looks identical to one captured this morning.
3. **Education pricing is folded into "best price"**, which is wrong for most
   readers and misleading for all of them.
4. **Gift-card maths is done in prose and gets it wrong.** Points are described
   as cash, percentages are miscalculated, and the same spend is discounted
   twice.
5. **Marketplace and grey-import stock is mixed with authorised retail**, so the
   cheapest number is often the one with the weakest warranty.

Orchard's job is narrow: publish a timestamped, sourced price, say how much it
can be trusted, and rank the routes honestly.

## Users

| User | What they are doing | What they need | What Phase 1 gives them |
|---|---|---|---|
| **Deal-aware Apple buyer** | Replacing a laptop or phone in the next four weeks; knows the RRP and wants to know if this week's sale is real | Current price per retailer, the usual price, the all-time low, stock, and a link | Catalogue, product detail, price history, deals with evidence, best verified price |
| **Parent or student buying education** | Buying for a tertiary student; eligible but unsure what the discount is and what the limits are | Education price versus retail, the saving in dollars and per cent, eligibility, annual limits, verification vendor | Education page with Apple's own eligibility and limits, education pricing never merged into retail |
| **Gift-card optimiser** | Willing to buy discounted cards or route spend through a points programme to reduce the effective price | Effective price after card discount, points and cashback, with expiry, limits and risks visible | Gift-card page, client-side calculator, points shown as points, no double-counting |
| **Business or education buyer** | Needs a quote, not a checkout | A named procurement route with an official contact | Business and education page, quote-only routes clearly marked, no invented price |

Secondary: journalists and researchers who need a citable observation with a
URL and a timestamp. The data model serves them; there is no separate product
for them in Phase 1.

## Scope in Phase 1

In scope:

1. **Catalogue** of current Apple AU products with evidenced RRP, configuration
   (storage, memory, colour, connectivity) and education eligibility.
2. **Collection** from Apple AU and a small, policy-cleared set of Australian
   retailers, with snapshots, hashes and run manifests.
3. **Matching** to the catalogue with confidence, a review queue and human
   overrides.
4. **Freshness** enforced as policy: per-source thresholds, stored status,
   indicative labelling.
5. **Price history** derived from snapshots, including all-time low and previous
   price.
6. **Deals** with rule versions, evidence, expiry and confidence.
7. **Gift cards and cashback** as curated offers with effective-price maths and
   visible risks.
8. **Purchase routes** for a product, ranked, covering retail, education,
   gift card, cashback and — visibly gated — EPP, business quote, trade-in and
   grey import.
9. **A static site** on GitHub Pages, offline-buildable, readable without
   JavaScript, with the non-affiliation statement everywhere.
10. **Operations**: validation gate before deploy, scheduled runs, run summaries,
    adapter health, Discord alerts with dedupe.

Constraints that bound the scope:

- Australia only, AUD only.
- Public, unauthenticated pages only. No scraping behind a login.
- No more than one observation per source per scheduled cycle.
- Snapshots remain the only evidence layer; everything else is derived.
- 90-day snapshot retention with compact history (`APA-052`).

## Out of scope

| Not doing | Why | When/if |
|---|---|---|
| User accounts, profiles, saved searches on a server | Privacy burden, no need for a static site | Never in the current design; watchlist is browser-local (`APA-042`) |
| Payments, checkout, cart, purchasing on the user's behalf | Legal and operational risk; retailers own the transaction | Never |
| Mobile apps | The site is responsive; an app adds a release pipeline for no new capability | Not planned |
| AI or LLM in the hot path | Prices must be deterministic and auditable; a model cannot be a source of truth | Not planned. Any future assistance is off the publish path and labelled |
| Buying, reselling, dropshipping or arbitrage | Not the product | Never |
| Non-Apple products | Dilutes the trust model and the catalogue | Not planned |
| Markets outside Australia | Pricing, tax and consumer law differ | Not planned |
| Retailer reviews, ratings or editorial | Different product, different liability | Not planned |
| Real-time or minute-level pricing | Politeness and value; daily is enough | Not planned |
| Scraping behind logins, CAPTCHAs or bot protection | Non-negotiable | Never |
| Storing personal data | No accounts, no analytics, no cookies, no tracking | Never |

## Success measures

Every measure is computed from committed files, so it can be checked without
asking anyone.

| Measure | Definition | Source of truth | Phase 1 target |
|---|---|---|---|
| **Verified freshness coverage** | Published offers with `verification_status = verified` ÷ all published offers, at the published `evaluated_at` | `data/latest_offers.json` | ≥ 95% on every run after M2 |
| **Match confidence distribution** | Count per bucket: ≥ 0.99, 0.85–0.99, < 0.85 | `data/latest_offers.json`, `data/health/confidence.json` | 0 published below 0.85; ≥ 90% at ≥ 0.99 by M4 |
| **Time to detect a price drop** | `Deal.first_detected_at` minus the earliest snapshot showing the changed price, for the same seller and product | `data/deals/*` + `data/snapshots/*` | ≤ 1 collection cycle for enabled sources (≤ 24 h) |
| **Review queue age** | Median age of `pending` candidates | `data/matching/review_queue.json` | < 7 days |
| **Catalogue evidence** | Products with an RRP and both `rrp_evidence_url` and `rrp_captured_at` ÷ all priced products | `data/products.json` | 100% |
| **Retailer coverage** | Products with ≥ 2 verified sellers offering them | `data/latest_offers.json` | ≥ 50% of the catalogue by M4 |
| **Source health** | Enabled sources with a `ok` outcome in the last 7 days ÷ enabled sources | run manifests, `data/health/adapter_health.json` | ≥ 90% |
| **Deploy safety** | Deploys that ran without a passing validation | workflow history | 0, always |
| **Alert precision** | Duplicate alerts sent for the same `deal_id` | `data/alerts/sent.json` | 0 |
| **Determinism** | Repeated runs over unchanged snapshots that produce a diff in published files | CI golden check | 0 diffs |
| **Evidence on deals** | Deals with a non-empty `evidence_url` and `captured_at` ÷ all deals | `data/deals/current_deals.json` | 100% |

A measure with no data yet reads as "not yet measured", never as zero or as a
pass.

## MVP acceptance

The MVP is done when all of the following are demonstrable on `main`. Each is a
check, not an opinion.

**Data and trust**

- [ ] `uv run python -m scripts.validation.validate_data` exits 0 on a clean tree
      and 1 on a deliberately broken dataset, naming the file and field.
- [ ] Every priced product carries `rrp_evidence_url` and `rrp_captured_at`.
- [ ] Every published offer has `url`, `captured_at`, `source_hash` and
      `verification_status`.
- [ ] No published offer has confidence below 0.85.
- [ ] Stale and unverified offers are labelled indicative and cannot win best
      price.
- [ ] Education pricing appears only as education offers, with eligibility and
      limits sourced from Apple.

**Pipeline**

- [ ] At least one automated Apple AU source and two retailer sources are live,
      each with a recorded policy decision and offline fixtures.
- [ ] One source failing does not fail the run; it produces a `partial` manifest
      and a health signal.
- [ ] Regenerating published files from unchanged snapshots produces no diff.
- [ ] A rejected match cannot re-enter the review queue without a changed
      `source_hash`; an approval survives re-collection.

**Product**

- [ ] Catalogue, product detail, deals, education and gift-card pages render
      from committed JSON with no runtime API.
- [ ] The gift-card calculator uses the documented point rate (default $0.005),
      labels points as points, and refuses to double-count a route.
- [ ] Every page carries the non-affiliation and indicative-price disclaimers;
      the footer shows the data commit SHA.
- [ ] Baseline pages render and are usable without JavaScript.

**Operations**

- [ ] A failing validation provably blocks deploy (demonstrated on a branch).
- [ ] Scheduled collection runs, and a repeat alert run sends zero duplicates.
- [ ] A missing `DISCORD_WEBHOOK_URL` exits 0 with a warning.
- [ ] The documentation set listed in `README.md` is current and every command
      in it works.

## Assumptions and risks

| Assumption | If wrong |
|---|---|
| Apple AU publishes prices publicly and server-side | The Apple source falls back to curated records, with the reason recorded |
| Some retailers permit polite automated access | Those sources ship curated; the site still launches with fewer sellers |
| Gift-card promotions are publicly advertised | Only publicly stated offers are published; the rest are absent, not guessed |
| Daily collection is enough to be useful | Cadence rises per source, within the registry's rate limits |
| A static site can carry search, charts and a calculator | Features degrade to prebuilt indexes and progressive enhancement, never to a server |

The largest product risk is not technology: it is publishing a confident number
that is wrong. The confidence gate, the review queue and the freshness policy
exist to make that failure visible before a reader sees it.