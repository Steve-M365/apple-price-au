# Source registry

**Status:** generated from `data/sellers.json`, which holds 29 sellers. The
tables below are a rendering of that file, not a second source of truth. If a
row disagrees with the file, the file wins.

> **No per-source robots.txt or terms-of-service finding has been recorded.**
> Every row's policy status is `unverified`. The per-adapter cards
> (`APA-017`–`APA-025` on the current board; the review brief calls this gate
> `APA-030`–`APA-041`) must record a finding before an adapter may fetch.
> **No automated collection may ship before that check.**

## Provenance and counts

Verified against the file:

```bash
python3 - <<'PY'
import json
s = json.load(open('data/sellers.json'))['sellers']
print('count', len(s), 'unique', len({x['seller_id'] for x in s}))
print('enabled', sum(1 for x in s if x['enabled']))
print('quote_only', sum(1 for x in s if x.get('quote_only')))
PY
```

```
count 29 unique 29
enabled 10
quote_only 8
```

| Slice | Count | Meaning |
|---|---:|---|
| Registered sellers | 29 | one row per source or procurement route |
| Enabled | 10 | in Phase 1 scope; **not** a licence to fetch |
| Disabled | 19 | registered with an `enabled_reason` |
| Quote-only | 8 | no public price exists; used to record a quote |
| Automated | 7 | `automated_jsonld` or `automated_html`; all unverified |
| Curated | 12 | human-entered records through the evidence workflow |
| Manual | 10 | quote-only or blocked; no collection |

The seller ids, display names, types, collection methods, rate limits, `enabled`
and `quote_only` flags in the tables below come from `data/sellers.json`. Do not
retype an id; regenerate the table from the file.

**Policy status is not a field on `Seller`.** The model has no
`policy_status`, `policy_evidence_url` or `policy_checked_at`; it carries
`collection_method`, `adapter_module`, `rate_limit_rps`, `enabled`,
`enabled_reason` and `notes`. This document tracks the policy finding, and the
registry records its consequence. A finding is recorded by editing this file,
updating the seller's `notes`, and changing `collection_method` / `enabled` in
`data/sellers.json`.

**`enabled: true` does not mean cleared to fetch.** It means the seller is in
Phase 1 scope. Seven enabled sellers are automated and none has a recorded
finding, so the robots gate (`APA-017`) must refuse every fetch until one exists.
No adapter module exists yet, and the validation CLI reports that as a warning.

## Phase 1 — enabled (10)

| `seller_id` | Display name | Type | Method | Rate limit | Enabled | Quote-only | Policy status | Biggest risk | What would enable it |
|---|---|---|---|---|---|---|---|---|---|
| `apple_au` | Apple Australia | `apple_direct` | `automated_jsonld` | 0.33 rps / 3 s | yes | no | unverified | No collection risk recorded; JSON-LD with HTML fallback is unproven until the adapter lands (APA-011). | Already enabled; the robots/terms finding must be recorded before the adapter may fetch (APA-011). |
| `apple_education_au` | Apple Education Store Australia | `apple_direct` | `automated_jsonld` | 0.33 rps / 3 s | yes | no | unverified | Education pricing is eligibility-gated (student, teacher, institution); it must stay a separate offer type and never merge into retail (APA-012). | Already enabled; the robots/terms finding plus the display-behaviour check (APA-012) before the adapter may fetch. |
| `jbhifi` | JB Hi-Fi | `national_reseller` | `curated` | 0 rps / 5 s | yes | no | unverified | Search answered HTTP 429 to a single non-browser request on 2026-11-15; automated collection is pending a robots/terms review (APA-018). | A recorded robots/terms finding plus an adapter with fixtures (APA-018); curated until then. |
| `officeworks` | Officeworks | `national_reseller` | `automated_html` | 0.2 rps / 3 s | yes | no | unverified | No dedicated warranty page; warranty terms sit in the returns policy, so the URL is null rather than guessed. | Already enabled; the robots/terms finding must be recorded before the adapter may fetch (APA-019). |
| `harvey_norman` | Harvey Norman | `national_reseller` | `automated_html` | 0.2 rps / 3 s | yes | no | unverified | No collection risk recorded; the adapter still needs the robots/terms finding (APA-020). | Already enabled; the robots/terms finding must be recorded before the adapter may fetch (APA-020). |
| `the_good_guys` | The Good Guys | `national_reseller` | `curated` | 0 rps / 5 s | yes | no | unverified | No separate warranty page verified; the warranty URL points at the combined returns, refunds and repairs page. | A recorded robots/terms finding plus an adapter with fixtures (APA-021); curated until then. |
| `bing_lee` | Bing Lee | `national_reseller` | `automated_html` | 0.2 rps / 3 s | yes | no | unverified | The site answers HTTP 403 to non-browser clients; automation is slow (0.2 rps, 3 s) and the robots check is mandatory (APA-022). | Already enabled; the robots/terms finding is mandatory because the site returns 403 (APA-022). |
| `beyond_the_box` | Beyond the Box | `specialist_reseller` | `automated_html` | 0.25 rps / 3 s | yes | no | unverified | Search endpoint answered HTTP 429 to a single request, which is why the rate limit is set low (APA-023). | Already enabled; the robots/terms finding must be recorded before the adapter may fetch (APA-023). |
| `mobileciti` | Mobileciti | `specialist_reseller` | `automated_html` | 0.33 rps / 3 s | yes | no | unverified | Grey-import risk: provenance must be checked per listing; no grey-import unit may be presented as Apple-authorised stock (APA-024). | Already enabled; the robots/terms finding must be recorded before the adapter may fetch (APA-024). |
| `amazon_au` | Amazon Australia | `online_marketplace` | `curated` | 0 rps / 5 s | yes | no | unverified | Search answered HTTP 503 to a non-browser request on 2026-11-15; seller of record varies per offer, so third-party listings default to review (APA-025). | Approved Product Advertising API credentials plus seller-of-record enforcement (APA-025); curated until then. |

## Phase 2 — disabled consumer and gift-card sources (11)

| `seller_id` | Display name | Type | Method | Rate limit | Enabled | Quote-only | Policy status | Biggest risk | What would enable it |
|---|---|---|---|---|---|---|---|---|---|
| `kogan` | Kogan | `online_marketplace` | `curated` | 0 rps / 5 s | no | no | unverified | Open marketplace with substantial grey-import and third-party stock; domain answered HTTP 403 to non-browser clients. | Phase 2. Would be enabled after (a) the marketplace seller-of-record check is implemented and (b) grey-import provenance rules are signed off, so that Kogan Marketplace stock is never shown as authorised Apple stock. |
| `ebay_au` | eBay Australia | `online_marketplace` | `curated` | 0 rps / 5 s | no | no | unverified | Open marketplace with used, refurbished and grey-import listings; domain answered HTTP 403 and the search path is unconfirmed. | Phase 2. Would be enabled once a per-listing seller allow-list (Apple AU, identified authorised resellers) and a used/refurbished exclusion are implemented. |
| `betta` | Betta Home Living | `national_reseller` | `curated` | 0 rps / 5 s | no | no | unverified | Franchise network, so online availability and stock vary by store; search answered HTTP 429. | Phase 2. Would be enabled once store-level stock reporting is understood, because Betta is a franchise network and online availability varies by store. |
| `next_byte` | Next Byte | `specialist_reseller` | `manual` | 0 rps / 5 s | no | no | unverified | No live storefront could be confirmed: nextbyte.com.au is a parked domain for sale and nextbyte.com is a parked lander. | Phase 2 and blocked: no live storefront could be confirmed. Would be enabled only after a human confirms the current trading domain and that prices are published. |
| `my_byte` | My Byte | `specialist_reseller` | `manual` | 0 rps / 5 s | no | no | unverified | No product search URL was confirmed, so there is no collection entry point. | Phase 2. Would be enabled after a category and stock review plus a robots.txt check, since the site is small and stock depth is shallow. |
| `macfixit_au` | Macfixit Australia | `specialist_reseller` | `curated` | 0 rps / 5 s | no | no | unverified | Most of the range is parts and accessories, not Apple-branded hardware; search answered HTTP 429. | Phase 2. Would be enabled once the parts-and-accessories catalogue is mapped, since most of the range is not Apple-branded hardware and must not be matched to Apple products. |
| `woolworths` | Woolworths (Everyday Rewards gift cards) | `gift_card_seller` | `curated` | 0 rps / 5 s | no | no | unverified | Site answered HTTP 403 to non-browser clients; no gift-card page path was verified, so search and policy URLs are null. | Phase 2. Would be enabled when the gift-card feed under APA-032 is built; it needs a curated, evidence-backed record of the discounted card and its terms before any effective price can be published. |
| `coles` | Coles (Flybuys or Velocity gift cards) | `gift_card_seller` | `curated` | 0 rps / 5 s | no | no | unverified | Site answered HTTP 403; no gift-card path was verified, so search and policy URLs are null. | Phase 2. Would be enabled with the curated gift-card feed (APA-032), once flybuys or Velocity redemption value can be evidenced rather than assumed. |
| `shopback` | ShopBack (Apple cashback) | `gift_card_seller` | `curated` | 0 rps / 5 s | no | no | unverified | No collection risk recorded; cashback rates and effective dates are the open items. | Phase 2. Would be enabled with the curated cashback feed under APA-032; cashback rates change frequently and need a captured source plus an effective date before display. |
| `shopback_gift_cards` | ShopBack Gift Cards | `gift_card_seller` | `curated` | 0 rps / 5 s | no | no | unverified | No collection risk recorded; a listing only counts when a discounted Apple-redeemable card is observed. | Phase 2. Would be enabled with the curated gift-card feed (APA-032) once the discounted card range and its redemption terms are captured with evidence. |
| `prezzee` | Prezzee | `gift_card_seller` | `curated` | 0 rps / 5 s | no | no | unverified | No Apple-specific gift-card path verified (candidate paths return 404); the help centre answered HTTP 403. | Phase 2. Would be enabled with the curated gift-card feed (APA-032); Apple-redeemable cards are not always listed, so the feed needs an observation date and an explicit not-available state. |

## Business, education and quote-only routes (8)

| `seller_id` | Display name | Type | Method | Rate limit | Enabled | Quote-only | Policy status | Biggest risk | What would enable it |
|---|---|---|---|---|---|---|---|---|---|
| `apple_business` | Apple Business Store | `apple_direct` | `manual` | 0 rps / 5 s | no | yes | unverified | Business pricing varies by agreement and volume; any figure is a quote with a reference number and expiry, not a list price. | Nothing — quote-only by design; used to record a quote, never a price. |
| `apple_business_manager` | Apple Business Manager (Apps and Books / VPP) | `apple_direct` | `manual` | 0 rps / 5 s | no | yes | unverified | Requires an authenticated organisation account, which this project must never access; values come from a buyer-supplied quote. | Nothing — quote-only by design; used to record a quote, never a price. |
| `apple_trade_in` | Apple Trade In | `apple_direct` | `manual` | 0 rps / 5 s | no | yes | unverified | Trade-in credit depends on device condition and is quoted at assessment; estimates are indicative only. | Nothing — quote-only by design; used to record a quote, never a price. |
| `mac_centre` | Mac Centre | `business_reseller` | `manual` | 0 rps / 5 s | no | yes | unverified | No public catalogue search was confirmed (tested paths return 404). | Nothing — quote-only by design; used to record a quote, never a price. |
| `ct` | CT | `business_reseller` | `manual` | 0 rps / 5 s | no | yes | unverified | No public Apple price catalogue was confirmed. | Nothing — quote-only by design; used to record a quote, never a price. |
| `edunet` | Edunet | `education_reseller` | `manual` | 0 rps / 5 s | no | yes | unverified | Education pricing is eligibility-gated and must never merge with retail; institutional quote only. | Nothing — quote-only by design; used to record a quote, never a price. |
| `first_focus` | First Focus | `business_reseller` | `manual` | 0 rps / 5 s | no | yes | unverified | Hardware is generally quoted as part of a managed service, so no public price exists to collect. | Nothing — quote-only by design; used to record a quote, never a price. |
| `synnex` | Synnex | `business_reseller` | `manual` | 0 rps / 5 s | no | yes | unverified | Two-tier distribution: pricing is visible only to account holders, and authenticated access is prohibited. | Nothing — quote-only by design; used to record a quote, never a price. |

## Collection posture

These rules apply to every source, including the ones marked `curated`.

| Rule | Consequence |
|---|---|
| **Robots/terms gate first** | No adapter fetches before a finding is recorded. The gate is `APA-017`; a denial means no fetch, not a retry. |
| **No CAPTCHA or bot-protection bypass** | No solving, no fingerprint spoofing, no headless evasion, no proxy rotation. A challenge is a stop. |
| **No authenticated scraping** | No logins, no borrowed cookies, no account-bound prices. This is why `apple_business_manager` and `synnex` are quote-only. |
| **Curated fallback** | When automation is not permitted, a human records the price through the evidence workflow. The same models, validation and freshness rules apply. |
| **Policy denial is a decision, not a bug** | Record it here, in the seller's `notes`, and in `enabled_reason`; keep the row. Never retry behind a workaround. |
| **Rate limits are ceilings** | `rate_limit_rps` and `crawl_delay_seconds` come from the file. The gate may lower them; it cannot raise them. |
| **One adapter per automated seller** | `adapter_module` names the only module allowed to fetch that seller. It is empty for curated and manual sellers. |
| **`enabled` is scope, not permission** | The 10 enabled rows are Phase 1 scope. Seven are automated and all remain unverified, so nothing may fetch yet. |

Rate limit `0 rps` means no automated request is planned; the crawl delay is the
minimum a human or a future adapter must observe.

## Recording a finding

When a source is checked, record the outcome in four places:

1. This document: replace `unverified` in that row and state the finding.
2. `data/sellers.json` → `notes`: what was read, the URL, the date, the reviewer.
3. `data/sellers.json` → `collection_method`: the method that will actually ship.
4. `data/sellers.json` → `enabled` and `enabled_reason`: keep or disable with a
   reason.

A denial keeps the row with `enabled: false` and a reason. It is not deleted: the
denial is the record, and a future reader needs to know the question was asked.

The gate is not satisfied by a 200 response. A page that answers is not a page
that permits collection.

## What ships in Phase 1

- **No collector exists today.** `scripts/collectors/` has no adapter modules;
  the validator warns for each enabled seller that names one.
- The registry is the written posture the first adapter must satisfy.
- Curated sources can produce evidence-backed records without an adapter, which
  is why `jbhifi`, `the_good_guys` and `amazon_au` are enabled as curated.
- When the gate clears, the first automated sources are Apple AU and Apple
  Education; the rest follow their own cards and fixtures.