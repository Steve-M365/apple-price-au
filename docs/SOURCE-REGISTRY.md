# Source registry

**Status:** planning registry for Phase 1 and Phase 2. `data/sellers.json` is
the machine-readable version and is authoritative once it exists (`APA-004`);
this document explains the reasoning behind each row.

> **No per-source robots.txt or terms check has been performed yet.**
> Every `policy_status` below is `unverified`. Verifying each source is a
> gating task in Milestones 2–3 (`APA-017`, `APA-018`, `APA-019`,
> `APA-020`–`APA-025`). **No automated collection may ship before that check**,
> and no row in this table is evidence that collection is permitted.

## Counts and reconciliation

**29 sources across 28 `seller_id` values.** The 29th row is the Apple gift-card
terms page, which shares the `apple_au` seller.

- 21 Phase 1/Phase 2 collection targets: 5 Apple-owned channels + 16 Australian
  retailers and marketplaces.
- 8 gift-card and cashback sources: 7 programmes plus the Apple gift-card terms
  page.

The `APA-004` card text says "21 sellers". That count predates the gift-card
layer; the registry here is the fuller list, and `sellers.json` must match it.
This discrepancy is flagged to the director rather than silently resolved.

**Inclusion means "considered as a source", not a verified claim** that the
retailer currently stocks every Apple category, that the listed URL is the right
entry point, or that the rate limit is accepted. Range, entry point and
permission are all checked in the Milestone 2 coverage and policy pass, and any
row may be demoted to curated or dropped on the evidence.

## Rules that apply to every row

| Rule | Consequence |
|---|---|
| **robots.txt gate first** | An adapter fetches `/robots.txt` before its first request and re-checks daily. Disallow means no fetch. |
| **No CAPTCHA or bot-protection bypass** | No solving, no fingerprint spoofing, no headless evasion, no proxy rotation. A challenge is a stop, not a puzzle. |
| **No authenticated scraping** | No logins, no cookies borrowed from a browser, no account-bound prices. If a price needs an account, it is not collected. |
| **Curated fallback** | When automation is not permitted, a human-entered curated record is used, with the same models, validation and freshness rules. |
| **Policy denial is a decision, not a bug** | A denial is recorded with the evidence URL, date and reviewer, and the curated path ships. It is never retried behind a workaround. |
| **Rate limits are ceilings** | The values below are planning defaults. The robots finding may lower them; it can never raise them. |
| **One adapter per source** | An adapter cannot fetch a second source to fill a gap. |

## Registry

Rate limit and crawl delay are **proposed** values for the automated path; `—`
means no automated request is planned.

| # | `seller_id` | Type | Method | Rate limit (proposed) | Crawl delay | Policy status | Biggest risk | What would move it to automated |
|---|---|---|---|---|---|---|---|---|
| 1 | `apple_au` | `apple_direct` | automated | 10/min | 2 s | unverified | Buy-flow pages can render price client-side; JSON-LD is inconsistent across categories; locale redirects | Nothing — automated is planned, subject to the robots/terms gate and fixture coverage |
| 2 | `apple_education` | `education` | automated | 6/min | 5 s | unverified | Education pricing may be gated behind UNiDAYS, hiding the price; eligibility text changes without notice | Nothing — automated is planned; the observed display behaviour must be written here before asserting prices (`APA-012`) |
| 3 | `apple_refurbished` | `apple_direct` | automated | 6/min | 5 s | unverified | Stock churns hourly; listings vanish between runs; refurbished SKUs are not stable | Nothing — automated is planned; model must treat disappearance as out-of-stock, not a price change |
| 4 | `apple_business` | `business` | quote-only | — | — | `quote_only_by_design` | No public price; a quote requires an account and a sales conversation | An official public price list or quote endpoint. Until then it is a purchase route, not an offer |
| 5 | `apple_trade_in` | `apple_direct` | quote-only | — | — | `quote_only_by_design` | Trade-in value is device-condition-specific and displayed as a range | An official valuation endpoint with a documented request shape |
| 6 | `jb_hifi` | `authorised_reseller` | curated | 10/min | 3 s | unverified | Search pages may be bot-protected; prices can be client-rendered; promotion churn | Recorded allowance + a stable server-rendered price field + fixtures for price present/absent/out-of-stock (`APA-018`) |
| 7 | `the_good_guys` | `authorised_reseller` | curated | 10/min | 3 s | unverified | Shares a corporate group with JB Hi-Fi, so one policy finding may cover both; clearance and bundle listings mix with bare products | A recorded group-level decision + adapter fixtures covering clearance and bundle (`APA-021`) |
| 8 | `officeworks` | `authorised_reseller` | automated | 10/min | 3 s | unverified | Pickup availability is per store and changes faster than price | Recorded allowance + fixtures for in-stock, out-of-stock and was-price (`APA-019`) |
| 9 | `harvey_norman` | `authorised_reseller` | automated | 10/min | 3 s | unverified | Per-store prices and stock; merging them into one offer would be wrong | Recorded allowance + a documented rule for deriving one national status (`APA-020`) |
| 10 | `bing_lee` | `authorised_reseller` | automated | 6/min | 5 s | unverified | Small catalogue, fragile markup, possible bot protection | Recorded allowance + fixtures including out-of-stock (`APA-022`) |
| 11 | `mobileciti` | `specialist_reseller` | automated | 6/min | 5 s | unverified | Grey-import stock; warranty provider varies by listing | Recorded allowance + enforced grey-import flag and warranty capture (`APA-024`) |
| 12 | `beyond_the_box` | `specialist_reseller` | automated | 6/min | 5 s | unverified | Low stock; listings disappear; small site with unstable markup | Recorded allowance + fixtures and a specialist trust input (`APA-023`) |
| 13 | `amazon_au` | `marketplace` | curated | 12/min | 2 s | unverified | Third-party sellers and used stock mixed into the same catalogue; PA-API needs approval | Approved PA-API credentials (`AMAZON_PAAPI_*`) plus `sold_by` enforcement (`APA-025`) |
| 14 | `ebay_au` | `marketplace` | curated | — | — | unverified | Auctions, used items, seller variance, no stable product identity | An official Browse API path with a policy decision; otherwise it stays curated |
| 15 | `big_w` | `department_store` | automated | 6/min | 5 s | unverified | Limited Apple range; catalogue rotations; low stock depth | Recorded allowance + fixtures. Low value until range widens |
| 16 | `target_au` | `department_store` | automated | 6/min | 5 s | unverified | Limited Apple range; marketplace items may appear under the same domain | Recorded allowance + a marketplace filter. Low value until range widens |
| 17 | `kogan` | `marketplace` | curated | — | — | unverified | Grey imports, marketplace sellers, ambiguous warranty | A policy decision plus enforced grey-import labelling; curated until then |
| 18 | `digidirect` | `specialist_reseller` | automated | 6/min | 5 s | unverified | Promotional bundles and stock churn | Recorded allowance + bundle-vs-bare fixtures |
| 19 | `mwave` | `specialist_reseller` | automated | 6/min | 5 s | unverified | Stock levels volatile; some items build-to-order | Recorded allowance + out-of-stock fixtures |
| 20 | `umart` | `specialist_reseller` | automated | 6/min | 5 s | unverified | Per-store pickup stock and pricing | Recorded allowance + a per-store-to-national rule |
| 21 | `centre_com` | `specialist_reseller` | automated | 6/min | 5 s | unverified | Per-store stock; small markup changes | Recorded allowance + fixtures |
| 22 | `everyday_rewards` | `gift_card_program` | curated | — | — | unverified | Offers are weekly, app-only and capped; terms are not durable | A public, unauthenticated promo page with stable structure. Otherwise curated indefinitely (`APA-032`) |
| 23 | `flybuys` | `gift_card_program` | curated | — | — | unverified | Offers are often personalised and require a login to see | A public offer page that does not depend on account state |
| 24 | `woolworths` | `supermarket` | curated | — | — | unverified | Gift-card promotions are often in-store only and rotate weekly | A stable public catalogue page with promotion terms (`APA-032`) |
| 25 | `coles` | `supermarket` | curated | — | — | unverified | Online exclusions; promotions vary by state and week | A stable public catalogue page with promotion terms |
| 26 | `shopback_au` | `cashback_platform` | curated | — | — | unverified | Rates change without notice; Apple may be excluded from some rates; attribution needs cookies | A public rate page plus a documented exclusion list. Never automated behind a login |
| 27 | `cashrewards` | `cashback_platform` | curated | — | — | unverified | Same as ShopBack: rate churn and exclusions | A public rate page plus an exclusion list |
| 28 | `prezzee` | `gift_card_program` | curated | — | — | unverified | Resale discount varies; delivery delay; card terms | A public catalogue with terms and stock; low priority |
| 29 | `apple_au` | `apple_direct` | curated | — | — | unverified | Apple gift-card terms page: terms and face values only; no discount to collect | Automated only if Apple publishes a stable terms page; low value |

## What ships in Phase 1

Milestone 1 ships **no collectors at all**. `scripts/collectors/` is an empty
package until `APA-011`. The registry exists so that:

1. `sellers.json` can be validated against a policy gate.
2. The first adapter has a written posture before it has code.
3. A policy denial has somewhere to be recorded.

After the Milestone 2 gate, the expected enabled set is small and boring:
Apple AU retail, then a retailer only after its own policy check and fixtures.
The curated path is not a temporary embarrassment; for several sources it is the
permanent design.

## Recording a policy decision

When a source is checked, append a row here and update `sellers.json`:

| Field | Content |
|---|---|
| `policy_status` | `verified_allowed`, `verified_denied` or `quote_only_by_design` |
| `policy_evidence_url` | The exact robots.txt or terms URL that was read |
| `policy_checked_at` | UTC timestamp |
| `collection_method` | The method that will actually ship |
| `enabled` | `true` only for `verified_allowed` / `quote_only_by_design` |
| `enabled_reason` | Required whenever `enabled` is `false` |

A `verified_denied` row stays in the registry with `enabled: false`. It is not
deleted: the denial is the record, and future readers need to know the question
was asked.

For `quote_only_by_design` sources there is no crawl to permit, so
`policy_evidence_url` records the official quote or contact page instead of a
robots.txt, and `enabled: false` keeps them out of every scheduled run.