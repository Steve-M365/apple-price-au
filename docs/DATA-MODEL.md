# Data model

**Status:** contract for Milestone 1 (`APA-002`, `APA-003`, `APA-006`). The models
described here will be implemented in `scripts/models/`; the field names and enum
values in this document are normative. If code and this document disagree, the
document is wrong only after the change is made here first.

Nothing in this document is a captured price. All examples are **format
examples** — they show shape, not market data.

## Conventions

| Topic | Rule |
|---|---|
| Currency | AUD only. `currency` is `Literal["AUD"]`; there is no FX handling. |
| Money | `Decimal` in Python, JSON **string** in committed files (`"1899.00"`), integer **cents** in SQLite. Never a float. |
| Timestamps | UTC ISO 8601 with a `Z` suffix (`2026-11-15T21:04:00Z`). Timezone conversion happens at render only. |
| Display timezone | `Australia/Melbourne`, applied by the site; never stored. |
| Nullability | `null` means **unknown**, not zero. A model must distinguish "no data" from "free". |
| Extra fields | Canonical shapes are closed. Additive fields require a `schema_version` bump. |
| Schema version | Every file carries `schema_version: "1.0"` at the top level. |
| Arrays | Ordered only where the spec says so; otherwise compared as sets. |
| IDs | Immutable once published. See [Identifiers](#identifiers). |

## Identifiers

IDs are stable. A rename of a display name never changes an ID. Regenerating a
dataset must be a no-op, and a collision must raise rather than overwrite
(`APA-013`).

**`product_id`** — lowercase `[a-z0-9-]`, hyphen-separated, no double hyphens,
96 characters maximum, ordered segments:

```
apple-{family}-{line or size}-{chip or generation}-{year}-{memory}-{storage}-{connectivity}-{colour}
```

Absent segments are omitted; connectivity appears only where it disambiguates.
`family` is a lowercase slug (`macbook_air`, `ipad_pro`, `iphone`, `watch`,
`airpods`, `apple_tv`, `homepod`, `display`, `accessory`). Underscores become
hyphens inside the ID, so `family: macbook_air` yields `apple-macbook-air-...`.
The literal segment `education` in an education `offer_id` is part of the ID
grammar, not a `price_basis` value.

| Example `product_id` | Reads as |
|---|---|
| `apple-macbook-air-13-m3-2024-8gb-256gb-midnight` | MacBook Air 13-inch, M3, 2024, 8 GB / 256 GB, midnight |
| `apple-ipad-pro-11-m4-2024-256gb-wifi-silver` | iPad Pro 11-inch, M4, 2024, 256 GB, Wi-Fi, silver |
| `apple-iphone-16-128gb-black` | iPhone 16, 128 GB, black |
| `apple-watch-series-10-42mm-gps-jet-black` | Apple Watch Series 10, 42 mm, GPS, jet black |
| `apple-airpods-pro-2-usbc` | AirPods Pro (2nd generation), USB-C |

**`offer_id`** — `ofr_{seller_id}__{product_id}__{price_basis}`. Double
underscore separates components; `seller_id` contains single underscores and
`product_id` contains hyphens, so the string parses unambiguously.

```
ofr_jb_hifi__apple-macbook-air-13-m3-2024-8gb-256gb-midnight__sale
ofr_apple_education__apple-macbook-air-13-m3-2024-8gb-256gb-midnight__education
```

There is at most **one current offer per `(seller_id, product_id, price_basis)`**.
A new capture of the same offer is a new snapshot row with the same `offer_id`
and a later `captured_at`; it is not a new offer. A change of `price_basis`
(sale ends, retail resumes) is a different `offer_id`, which is what makes
history readable.

Other identifiers:

| Entity | Format | Example |
|---|---|---|
| `gift_card_offer_id` | `gc_{seller_id}__{card_type}__{slug}` | `gc_woolworths__apple_gift_card__20x_points` |
| `deal_id` | `deal_{deal_type}__{product_id}__{yyyymmdd}` | `deal_price_drop__apple-iphone-16-128gb-black__20261115` |
| `candidate_id` | `mc_{seller_id}__{source_hash[:12]}` | `mc_jb_hifi__9f2c1ab34d0e` |
| `route_id` | `route_{product_id}__{route_type}` | `route_apple-iphone-16-128gb-black__education` |
| `run_id` | `run_{YYYYMMDDTHHMMSSZ}_{8 hex}` | `run_20261115T210000Z_3f9c1a2b` |
| `seller_id` | `[a-z0-9_]+`, fixed at registry time | `jb_hifi` |

`candidate_id` is derived from the source payload hash on purpose: a rejected
candidate cannot re-enter the review queue until the source content changes
(`APA-027`).

## Enum catalogue

These are the only permitted values. Code constants will live in
`scripts/models/enums.py`.

| Enum | Values | Used by |
|---|---|---|
| `category` | `mac`, `ipad`, `iphone`, `watch`, `airpods`, `apple_tv`, `homepod`, `display`, `accessory` | Product, Seller |
| `seller_type` | `apple_direct`, `authorised_reseller`, `specialist_reseller`, `marketplace`, `carrier`, `department_store`, `education`, `business`, `gift_card_program`, `cashback_platform`, `supermarket` | Seller |
| `stock_status` | `in_stock`, `low_stock`, `out_of_stock`, `preorder`, `unknown`, `discontinued` | Offer, EducationOffer |
| `price_basis` | `rrp`, `retail`, `sale`, `clearance`, `business_quote`, `epp`, `refurbished`, `bundle` | Offer |
| `promotion_type` | `none`, `price_drop`, `sale`, `clearance`, `bundle`, `cashback`, `gift_card`, `education`, `trade_in`, `member_price`, `coupon` | Offer |
| `verification_status` | `verified`, `unverified`, `stale` | Product RRP, Offer, EducationOffer, GiftCardOffer, Deal |
| `deal_type` | `price_drop`, `rrp_discount`, `all_time_low`, `education_saving`, `gift_card`, `cashback`, `bundle_value`, `clearance`, `restock` | Deal |
| `match_method` | `apple_sku`, `gtin`, `configuration`, `fuzzy`, `manual` | Offer, EducationOffer, MatchCandidate |
| `collection_method` | `automated`, `curated`, `quote_only`, `manual` | Seller |
| `policy_status` | `verified_allowed`, `verified_denied`, `unverified`, `quote_only_by_design` | Seller |
| `lifecycle_status` | `active`, `discontinued`, `superseded` | Product |
| `condition` | `new`, `refurbished`, `used` | Offer |
| `connectivity` | `wifi`, `cellular`, `wifi_cellular`, `gps`, `gps_cellular`, `wired`, `bluetooth` | ProductConfiguration |
| `grey_import_risk` | `none`, `possible`, `known`, `unknown` | Seller, Offer |
| `warranty_provider` | `apple_au`, `seller`, `unknown` | Seller, Offer |
| `offer_type` | `retail`, `education`, `gift_card` | Published offer union |
| `route_type` | `apple_retail`, `apple_education`, `corporate_epp`, `business_quote`, `trade_in`, `gift_card`, `cashback`, `refurbished`, `grey_import` | PurchaseRoute |
| `eligibility_category` | `tertiary_student`, `parent_guardian`, `teacher`, `education_staff`, `homeschool` | EducationOffer |
| `card_type` | `apple_gift_card`, `retailer_gift_card`, `prepaid_visa`, `loyalty_points` | GiftCardOffer |
| `candidate_status` | `pending`, `approved`, `rejected`, `auto_matched`, `expired` | MatchCandidate |
| `review_decision` | `approve`, `reject` | Override file |
| `run_status` | `success`, `partial`, `failed` | RunManifest |
| `source_run_status` | `ok`, `blocked_by_policy`, `network_error`, `parse_error`, `rate_limited`, `skipped`, `disabled` | SourceRunResult |
| `trigger` | `schedule`, `workflow_dispatch`, `local`, `backfill` | RunManifest |
| `region` | `national`, `VIC`, `NSW`, `QLD`, `WA`, `SA`, `TAS`, `ACT`, `NT` | Seller |

`verification_status` is a **stored** value. `stale` is assigned by the
freshness policy when `now - captured_at` exceeds the seller's threshold; the
site never recomputes it at render. The site label "indicative" is a **display
projection** of `unverified` and `stale`, not a fourth status.

`condition = used` can never match a new product and must never be published
(`APA-025`). `grey_import = true` can never rank above an authorised reseller
without the flag visible (`APA-024`).

---

## Product

`data/products.json` — one array of products, to be generated in `APA-005` and
expanded in `APA-014`.

| Field | Type | Null | Example | Notes |
|---|---|---|---|---|
| `product_id` | `str` | no | `apple-macbook-air-13-m3-2024-8gb-256gb-midnight` | Immutable, see Identifiers |
| `canonical_name` | `str` | no | `MacBook Air 13-inch (M3, 2024)` | Apple's marketing name, title case |
| `family` | `str` | no | `macbook_air` | Lowercase slug, used for grouping |
| `category` | `category` | no | `mac` | One of nine |
| `model_year` | `int` | yes | `2024` | `null` where Apple does not state a year |
| `configuration` | `ProductConfiguration` | no | see below | `{}` only for accessories with no variant |
| `apple_sku` | `str` | yes | `MXTQ3X/A` | Apple part number where published; format example |
| `gtins` | `list[str]` | no | `["0194253949880"]` | EAN-13 or GTIN-14; matching layer 2. Empty when none published |
| `rrp_aud` | `Decimal` | yes | `"1899.00"` | Apple AU standard price at `rrp_captured_at` |
| `rrp_verification` | `verification_status` | no | `verified` | Default `unverified` until an evidence URL exists |
| `rrp_evidence_url` | `str` | yes | `https://www.apple.com/au/shop/buy-mac/macbook-air` | Required when `rrp_aud` is not null |
| `rrp_captured_at` | `datetime` | yes | `2026-11-15T20:58:00Z` | Required when `rrp_aud` is not null |
| `apple_au_url` | `str` | no | `https://www.apple.com/au/shop/buy-mac/macbook-air` | Canonical Apple AU product page |
| `education_eligible` | `bool` | yes | `true` | From Apple's published eligibility, never assumed; `null` = not checked |
| `lifecycle_status` | `lifecycle_status` | no | `active` | Discontinued products are marked, not deleted |
| `superseded_by` | `str` | yes | `apple-macbook-air-13-m4-2025-16gb-256gb-midnight` | Set only when status is `superseded` |
| `tags` | `list[str]` | no | `["m3", "fanless"]` | Free-form, lowercase |
| `first_seen_at` | `datetime` | no | `2026-11-15T20:58:00Z` | When the catalogue row first appears |
| `last_updated_at` | `datetime` | no | `2026-11-15T20:58:00Z` | Last field change, not last capture |

`ProductConfiguration`:

| Field | Type | Null | Example | Notes |
|---|---|---|---|---|
| `chip` | `str` | yes | `M3` | Apple silicon or A-series generation |
| `screen_inches` | `Decimal` | yes | `"13.6"` | Nominal diagonal |
| `storage_gb` | `int` | yes | `256` | Normalised: `256GB` and `256 GB` both become `256` |
| `memory_gb` | `int` | yes | `8` | Unified memory / RAM |
| `connectivity` | `connectivity` | yes | `wifi` | Null for Macs and phones |
| `size_mm` | `int` | yes | `42` | Watch case size |
| `colour` | `str` | yes | `midnight` | Lowercase Apple colour name |

Invariant: a priced RRP always has an evidence URL and a capture time. A null
RRP always carries `rrp_verification: "unverified"` and the blocking reason in
the run summary and the card that raised it, per `APA-005`.

## Seller

`data/sellers.json` — the registry, delivered by `APA-004`. `enabled` guards
collection; a disabled seller is documented, not hidden.

| Field | Type | Null | Example | Notes |
|---|---|---|---|---|
| `seller_id` | `str` | no | `jb_hifi` | Stable, snake_case |
| `display_name` | `str` | no | `JB Hi-Fi` | As the retailer writes it |
| `seller_type` | `seller_type` | no | `authorised_reseller` | Drives trust and freshness defaults |
| `website` | `str` | no | `https://www.jbhifi.com.au` | Origin, no query string |
| `search_url` | `str` | yes | `https://www.jbhifi.com.au/search?query=macbook` | Entry point for collection, `null` for quote-only |
| `country` | `Literal["AU"]` | no | `AU` | Only AU |
| `regions` | `list[region]` | no | `["national"]` | `national` excludes state entries |
| `categories` | `list[category]` | no | `["mac", "ipad", "iphone"]` | Categories the source actually stocks |
| `collection_method` | `collection_method` | no | `curated` | The **shipped** method, not the aspiration |
| `requests_per_minute` | `int` | yes | `10` | Proposed cap, `null` for no automated calls |
| `burst` | `int` | yes | `2` | Token-bucket burst |
| `crawl_delay_seconds` | `float` | no | `3.0` | Minimum gap between requests |
| `policy_status` | `policy_status` | no | `unverified` | Until a robots/terms check is recorded |
| `policy_evidence_url` | `str` | yes | `https://www.jbhifi.com.au/robots.txt` | URL of the checked document |
| `policy_checked_at` | `datetime` | yes | `2026-11-20T02:00:00Z` | When it was checked |
| `authorised` | `bool` | no | `true` | Listed as an Apple reseller |
| `authorisation_evidence_url` | `str` | yes | `https://www.apple.com/au/shop/browse/reseller` | Evidence, not assertion |
| `warranty_url` | `str` | yes | `https://www.jbhifi.com.au/pages/warranty` | Customer-facing policy |
| `returns_url` | `str` | yes | `https://www.jbhifi.com.au/pages/returns` | Customer-facing policy |
| `delivery_url` | `str` | yes | `https://www.jbhifi.com.au/pages/delivery` | Customer-facing policy |
| `trust_score` | `Decimal` | no | `"0.90"` | 0.00–1.00, inputs documented in the registry |
| `grey_import_risk` | `grey_import_risk` | no | `none` | `known` for import specialists |
| `warranty_provider` | `warranty_provider` | no | `apple_au` | Who honours the warranty |
| `adapter_module` | `str` | yes | `scripts.collectors.jb_hifi` | `null` while no adapter exists |
| `enabled` | `bool` | no | `false` | `true` only with a recorded policy decision |
| `enabled_reason` | `str` | yes | `Awaiting recorded robots decision (APA-018)` | **Required** when `enabled` is false |
| `notes` | `str` | yes | `Shares a corporate group with The Good Guys` | Free text |

Rules:

- `enabled: true` requires `policy_status` of `verified_allowed` or
  `quote_only_by_design`, plus a `policy_evidence_url` and `policy_checked_at`.
  This is a validation hard failure.
- `collection_method: quote_only` sellers are never scheduled; they exist so
  purchase routes can name them.
- Disabled sellers keep their full row. Deleting a seller breaks historical
  `seller_id` references in snapshots.

## Offer

A retail observation. Collectors will write it to `data/snapshots/`, and publish
will project it into `data/latest_offers.json`.

| Field | Type | Null | Example | Notes |
|---|---|---|---|---|
| `offer_id` | `str` | no | `ofr_jb_hifi__apple-iphone-16-128gb-black__sale` | See Identifiers |
| `offer_type` | `Literal["retail"]` | no | `retail` | Discriminator for the published union |
| `product_id` | `str` | no | `apple-iphone-16-128gb-black` | Must exist in `products.json` |
| `seller_id` | `str` | no | `jb_hifi` | Must exist in `sellers.json` |
| `price_aud` | `Decimal` | no | `"1197.00"` | Greater than zero |
| `currency` | `Literal["AUD"]` | no | `AUD` | Constant |
| `price_basis` | `price_basis` | no | `sale` | How the price was formed; education pricing is a separate entity and not a value here |
| `was_price_aud` | `Decimal` | yes | `"1299.00"` | Only when the source states it; never inferred |
| `stock_status` | `stock_status` | no | `in_stock` | `unknown` is honest, `in_stock` is a claim |
| `pickup_available` | `bool` | yes | `true` | `null` where the page does not say |
| `pickup_locations` | `list[str]` | yes | `["Melbourne CBD", "Chadstone"]` | Store names as published |
| `condition` | `condition` | no | `new` | `used` never publishes against a new product |
| `sold_by` | `str` | yes | `JB Hi-Fi` | Marketplace seller name; `null` for direct retail |
| `url` | `str` | no | `https://www.jbhifi.com.au/products/...` | The exact page captured |
| `captured_at` | `datetime` | no | `2026-11-15T21:04:00Z` | Observation time, UTC |
| `source_hash` | `str` | no | 64 hex chars | SHA-256 of the raw payload |
| `run_id` | `str` | no | `run_20261115T210000Z_3f9c1a2b` | Links to the RunManifest |
| `verification_status` | `verification_status` | no | `verified` | `stale` assigned by policy |
| `confidence` | `Decimal` | no | `"0.98"` | Match confidence, 0.00–1.00 |
| `match_method` | `match_method` | no | `apple_sku` | Which layer matched |
| `promotion_type` | `promotion_type` | no | `sale` | `none` when no promotion text exists |
| `promotion_text` | `str` | yes | `Save $102` | Verbatim from the source |
| `promotion_ends_at` | `datetime` | yes | `2026-11-24T13:00:00Z` | Only when stated |
| `grey_import` | `bool` | no | `false` | Flag, not a footnote |
| `grey_import_risk` | `grey_import_risk` | no | `none` | Mirrors the seller unless the listing differs |
| `warranty_provider` | `warranty_provider` | no | `apple_au` | As stated on the listing |
| `delivery_cost_aud` | `Decimal` | yes | `"0.00"` | `null` when not captured; `0` means free |
| `notes` | `str` | yes | `Price excludes member-only discount` | Anything a human needs |

Invariants enforced by validation (`APA-003`):

1. `offers[].product_id` exists in `products.json`.
2. `offers[].seller_id` exists in `sellers.json`.
3. `captured_at`, `source_hash` and `verification_status` are present on every offer.
4. `confidence >= 0.85` for anything in `latest_offers.json`.
5. No `Offer` carries education pricing — there is no `education` value in
   `price_basis`. Education pricing has its own entity and its own file.
6. No two current offers share `(seller_id, product_id, price_basis)`.

## EducationOffer

Never merged into retail. Separate eligibility, separate limits, separate file.

| Field | Type | Null | Example | Notes |
|---|---|---|---|---|
| `offer_id` | `str` | no | `ofr_apple_education__apple-ipad-pro-11-m4-2024-256gb-wifi-silver__education` | |
| `offer_type` | `Literal["education"]` | no | `education` | |
| `product_id` | `str` | no | `apple-ipad-pro-11-m4-2024-256gb-wifi-silver` | |
| `seller_id` | `str` | no | `apple_education` | |
| `standard_price_aud` | `Decimal` | no | `"1699.00"` | Apple AU retail at capture |
| `education_price_aud` | `Decimal` | no | `"1529.00"` | Education store price at capture |
| `saving_aud` | `Decimal` | no | `"170.00"` | Derived: `standard - education` |
| `saving_pct` | `Decimal` | no | `"10.01"` | Derived, two decimal places |
| `eligibility` | `list[eligibility_category]` | no | `["tertiary_student", "parent_guardian"]` | Verbatim from Apple |
| `verification_required` | `bool` | no | `true` | Whether the store gates checkout |
| `verification_vendor` | `str` | yes | `UNiDAYS` | `null` if Apple does not state one |
| `annual_limit_summary` | `str` | yes | `One Mac per year per eligible customer` | Sourced, not paraphrased from memory |
| `limits_source_url` | `str` | yes | `https://www.apple.com/au/shop/browse/open/salespolicies` | Required when a limit is stated |
| `stock_status` | `stock_status` | no | `in_stock` | |
| `url` | `str` | no | `https://www.apple.com/au/shop/buy-ipad/ipad-pro` | |
| `captured_at` | `datetime` | no | `2026-11-15T21:10:00Z` | |
| `source_hash` | `str` | no | 64 hex chars | |
| `run_id` | `str` | no | `run_20261115T210000Z_3f9c1a2b` | |
| `verification_status` | `verification_status` | no | `verified` | |
| `confidence` | `Decimal` | no | `"0.99"` | |
| `match_method` | `match_method` | no | `apple_sku` | |

`saving_aud` and `saving_pct` are recomputed in tests to two decimal places
(`APA-012`). A zero or negative saving is not a deal; it may still be stored as
an observation but is never ranked.

## GiftCardOffer

`data/gift_cards/current_offers.json`. Curated in Phase 1 (`APA-032`).

| Field | Type | Null | Example | Notes |
|---|---|---|---|---|
| `gift_card_offer_id` | `str` | no | `gc_woolworths__apple_gift_card__20x_points` | |
| `seller_id` | `str` | no | `woolworths` | Who runs the promotion |
| `card_type` | `card_type` | no | `apple_gift_card` | |
| `face_value_aud` | `Decimal` | no | `"100.00"` | The card's face value |
| `discount_pct` | `Decimal` | no | `"0.00"` | Percentage off face value; `0` for a points-only promotion |
| `points_earned` | `int` | yes | `2000` | Loyalty points awarded |
| `point_value_aud` | `Decimal` | yes | `"0.005"` | Value of one point; see points note |
| `points_program` | `str` | yes | `Everyday Rewards` | Named loyalty programme, never "cash" |
| `points_value_aud` | `Decimal` | yes | `"10.00"` | Derived: `points_earned x point_value_aud` |
| `cashback_pct` | `Decimal` | yes | `null` | Separate mechanism from points |
| `effective_cost_aud` | `Decimal` | no | `"90.00"` | Derived: face less discount, points and cashback |
| `effective_discount_pct` | `Decimal` | no | `"10.00"` | Derived, asserted within 0.01 |
| `starts_at` | `datetime` | no | `2026-11-12T00:00:00Z` | |
| `ends_at` | `datetime` | yes | `2026-11-18T13:59:00Z` | `null` = no stated end; still expires by freshness |
| `limits` | `str` | yes | `Max 10 cards per customer per day` | Verbatim |
| `redemption_scope` | `list[str]` | no | `["apple_au", "jb_hifi"]` | Where the card can be spent |
| `risks` | `list[str]` | no | `["Points post 14 days after purchase"]` | Never empty (`APA-031`) |
| `source_url` | `str` | no | `https://www.woolworths.com.au/shop/catalogue` | |
| `captured_at` | `datetime` | no | `2026-11-15T22:00:00Z` | |
| `source_hash` | `str` | no | 64 hex chars | |
| `verification_status` | `verification_status` | no | `verified` | |
| `run_id` | `str` | no | `run_20261115T210000Z_3f9c1a2b` | |

### Points valuation: the discrepancy is deliberate

The project brief's worked example — 2,000 points at an assumed
`point_value_aud` of `0.05` on a $100 card, described as a "10% discount" — is
**internally inconsistent**: 2,000 × 0.05 = $100, which is 100% of a $100 card,
not 10%.

The implemented default is **$0.005 per point**:

```
2,000 points x $0.005 = $10.00 on a $100 card = 10.00% effective discount
```

Rules that follow from it:

1. `point_value_aud` is configuration, driven by `LOYALTY_POINT_VALUE_AUD`
   (default `0.005` in `.env.example`), and is written into the output of every
   calculation that uses it.
2. Points are **always labelled as points**, never as cash, in data or on the
   site (`APA-031`, `APA-040`). The site may say "worth about $10 at the
   configured rate", and must state the rate.
3. A point valuation only counts when the points are redeemable against the
   spend in question. If the programme cannot offset an Apple purchase, the
   valuation is `0` with a `risks` entry saying why.
4. The example in the brief is retained in `docs/` history as a caution: any
   arithmetic in a doc or test is recomputed, never copied.

## Deal

Output of the deal engine (`APA-030`). The eleven required fields are the first
eleven rows below; all are non-null.

| Field | Type | Null | Example | Notes |
|---|---|---|---|---|
| `deal_id` | `str` | no | `deal_price_drop__apple-iphone-16-128gb-black__20261115` | |
| `deal_type` | `deal_type` | no | `price_drop` | |
| `product_id` | `str` | no | `apple-iphone-16-128gb-black` | |
| `seller_id` | `str` | no | `jb_hifi` | |
| `current_price_aud` | `Decimal` | no | `"1197.00"` | The observed price |
| `reference_price_aud` | `Decimal` | no | `"1299.00"` | RRP, previous price or ATL comparison |
| `saving_aud` | `Decimal` | no | `"102.00"` | Derived |
| `saving_pct` | `Decimal` | no | `"7.85"` | Derived, two decimal places |
| `confidence` | `Decimal` | no | `"0.97"` | Inherited from the offer match, never fabricated |
| `evidence_url` | `str` | no | `https://www.jbhifi.com.au/products/...` | |
| `captured_at` | `datetime` | no | `2026-11-15T21:04:00Z` | |
| `effective_price_aud` | `Decimal` | yes | `"1077.30"` | After gift card/cashback, when a route applies |
| `starts_at` | `datetime` | yes | `2026-11-15T00:00:00Z` | Promotion start if stated |
| `expires_at` | `datetime` | yes | `2026-11-24T13:00:00Z` | An expired deal is never "current" |
| `rule_id` | `str` | no | `price_drop_5pct_7d` | Which rule fired |
| `rule_version` | `str` | no | `"1.0"` | Reproducibility |
| `verification_status` | `verification_status` | no | `verified` | |
| `first_detected_at` | `datetime` | no | `2026-11-15T21:04:00Z` | Drives `deal_id` |
| `last_seen_at` | `datetime` | no | `2026-11-15T21:04:00Z` | Last confirmation |
| `is_current` | `bool` | no | `true` | Derived at publish, never hand-set |
| `run_id` | `str` | no | `run_20261115T210000Z_3f9c1a2b` | |

Rules: no evidence URL or `captured_at`, no deal. Expired deals move to
`history.json`. Changing a threshold in `config/deal_rules.yml` must change the
output; a rule version bump invalidates golden files on purpose.

## MatchCandidate

`data/matching/review_queue.json`.

| Field | Type | Null | Example | Notes |
|---|---|---|---|---|
| `candidate_id` | `str` | no | `mc_jb_hifi__9f2c1ab34d0e` | Derived from `source_hash[:12]` |
| `seller_id` | `str` | no | `jb_hifi` | |
| `raw_title` | `str` | no | `Apple MacBook Air 13" M3 8/256 Midnight` | Exactly as the source wrote it |
| `price_aud` | `Decimal` | no | `"1697.00"` | The price that failed to match |
| `proposed_product_id` | `str` | yes | `apple-macbook-air-13-m3-2024-8gb-256gb-midnight` | `null` when no candidate was close |
| `method` | `match_method` | no | `fuzzy` | Layer that produced it |
| `confidence` | `Decimal` | no | `"0.62"` | Below 0.85 to be here |
| `normalised_title` | `str` | no | `apple macbook air 13 m3 2024 8gb 256gb midnight` | Normaliser output |
| `config_diff` | `list[ConfigDiff]` | no | see below | Every mismatched field |
| `status` | `candidate_status` | no | `pending` | |
| `evidence_url` | `str` | no | `https://www.jbhifi.com.au/products/...` | |
| `captured_at` | `datetime` | no | `2026-11-15T21:04:00Z` | |
| `source_hash` | `str` | no | 64 hex chars | Identity of the source payload |
| `created_run_id` | `str` | no | `run_20261115T210000Z_3f9c1a2b` | |
| `reviewed_by` | `str` | yes | `apple-director` | `null` until decided |
| `reviewed_at` | `datetime` | yes | `2026-11-16T01:00:00Z` | |
| `review_note` | `str` | yes | `Storage matches; colour confirmed on page` | |

`ConfigDiff`: `{"field": "storage_gb", "expected": 256, "got": 512}`.

## RunManifest

`data/runs/<run_id>.json`, written even when the run fails (`APA-015`).

| Field | Type | Null | Example | Notes |
|---|---|---|---|---|
| `run_id` | `str` | no | `run_20261115T210000Z_3f9c1a2b` | |
| `started_at` | `datetime` | no | `2026-11-15T21:00:00Z` | |
| `finished_at` | `datetime` | yes | `2026-11-15T21:04:12Z` | `null` while running; set on completion or failure |
| `status` | `run_status` | no | `partial` | `partial` = some sources failed |
| `trigger` | `trigger` | no | `schedule` | |
| `git_sha` | `str` | yes | `4f2c1ab` | Absent for local runs outside git |
| `workflow_run_url` | `str` | yes | `https://github.com/.../actions/runs/123` | |
| `config_hash` | `str` | no | 64 hex chars | Hash of config + registry, proves inputs |
| `rule_versions` | `dict[str, str]` | no | `{"deal_rules": "1.0", "freshness": "1.0"}` | |
| `sources` | `list[SourceRunResult]` | no | see below | One row per `seller_id` attempted in this run |
| `counts` | `RunCounts` | no | see below | Aggregates |

`SourceRunResult`: one row per seller attempted. A seller with two collection
sources reports the aggregate for the run; the per-source detail belongs in the
adapter health file, not here. Curated sources outside a scheduled run do not
appear.

| Field | Type | Null | Example |
|---|---|---|---|
| `seller_id` | `str` | no | `jb_hifi` |
| `status` | `source_run_status` | no | `ok` |
| `http_status` | `int` | yes | `200` |
| `fetched` | `int` | no | `42` |
| `parsed` | `int` | no | `40` |
| `matched` | `int` | no | `38` |
| `rejected` | `int` | no | `2` |
| `duration_ms` | `int` | no | `4210` |
| `error` | `str` | yes | `ParseError: price node missing` |

`RunCounts`: `fetched`, `parsed`, `offers_written`, `matched`,
`rejected_low_confidence`, `review_queue_delta`, `sellers_attempted`,
`sellers_blocked`.

## PurchaseRoute

Ranked in `APA-037`; every amount traces to an offer.

| Field | Type | Null | Example | Notes |
|---|---|---|---|---|
| `route_id` | `str` | no | `route_apple-iphone-16-128gb-black__education` | |
| `product_id` | `str` | no | `apple-iphone-16-128gb-black` | |
| `route_type` | `route_type` | no | `education` | |
| `seller_id` | `str` | yes | `apple_education` | `null` for quote-based routes |
| `headline_price_aud` | `Decimal` | no | `"1529.00"` | Before any adjustment |
| `effective_price_aud` | `Decimal` | no | `"1529.00"` | After adjustments, once each |
| `saving_vs_apple_aud` | `Decimal` | no | `"170.00"` | Against Apple AU retail |
| `eligibility_required` | `bool` | no | `true` | |
| `eligibility_notes` | `str` | yes | `Tertiary student or parent; UNiDAYS check` | |
| `evidence_offer_ids` | `list[str]` | no | `["ofr_apple_education__...__education"]` | Audit trail |
| `components` | `list[RouteComponent]` | no | see below | Applied once, on distinct spend |
| `expires_at` | `datetime` | yes | `2026-11-24T13:00:00Z` | |
| `confidence` | `Decimal` | no | `"0.99"` | Weakest link in the chain |
| `rank` | `int` | no | `1` | Derived; ties broken by `route_id` |
| `available` | `bool` | no | `true` | `false` for quote-only until requested |
| `notes` | `str` | yes | `EPP requires an employer code` | |

`RouteComponent`: `{"kind": "gift_card", "offer_id": "gc_...", "amount_aud": "-10.00", "label": "Everyday Rewards points"}`.

Double-counting is a hard failure: a gift-card discount and a cashback rate may
not both apply to the same spend (`APA-031`). `effective_price_aud` is never
below zero; a discount above face value is rejected.

## Freshness config

`config/freshness.yml` (Milestone 1, `APA-006`). Executable policy, not prose.

```yaml
schema_version: "1.0"
default_max_age_hours: 24

seller_type_max_age_hours:
  apple_direct: 24
  authorised_reseller: 24
  specialist_reseller: 36
  marketplace: 12
  carrier: 24
  department_store: 24
  education: 72
  business: 168
  gift_card_program: 72
  cashback_platform: 72
  supermarket: 72

source_class_max_age_hours:
  apple_store: 24
  education: 72
  refurbished: 48
  gift_card: 168
  cashback: 168

overrides:            # seller_id -> max_age_hours
  apple_education:
    max_age_hours: 72
```

| Field | Type | Null | Notes |
|---|---|---|---|
| `schema_version` | `str` | no | `"1.0"` |
| `default_max_age_hours` | `int` | no | Fallback when no rule matches |
| `seller_type_max_age_hours` | `dict[seller_type, int]` | yes | Matched on `sellers.json` |
| `source_class_max_age_hours` | `dict[str, int]` | yes | Source classes are assigned per adapter |
| `overrides` | `dict[seller_id, {max_age_hours}]` | yes | Highest precedence |

Precedence: `overrides` > `source_class` > `seller_type` > `default`.

Effective status is computed **once, at publish**, and written into the output
with `evaluated_at`. Rule:

```
age = evaluated_at - captured_at
stored unverified  -> stays unverified
stored verified and age > max_age_hours -> stale
otherwise -> verified
```

A stale or unverified offer is labelled "indicative" on the site and cannot win
best price (`APA-037`). The site does not call the clock to decide freshness;
it renders the published status.

## Review queue and overrides contract

Two committed files, both derived tables with different lifecycles.

`data/matching/review_queue.json` — regenerated every run:

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-11-15T21:04:00Z",
  "run_id": "run_20261115T210000Z_3f9c1a2b",
  "entries": []
}
```

`entries` is an array of `MatchCandidate` objects as defined above; it is empty
on a clean run.

`data/matching/overrides.json` — append-only human decisions, the only file in
`data/matching/` a human writes:

```json
{
  "schema_version": "1.0",
  "updated_at": "2026-11-16T01:00:00Z",
  "decisions": [
    {
      "candidate_id": "mc_jb_hifi__9f2c1ab34d0e",
      "seller_id": "jb_hifi",
      "product_id": "apple-macbook-air-13-m3-2024-8gb-256gb-midnight",
      "decision": "approve",
      "source_hash": "9f2c1ab34d0e...",
      "reviewer": "apple-director",
      "decided_at": "2026-11-16T01:00:00Z",
      "reason": "Storage and colour match the Apple AU page"
    }
  ]
}
```

Contract:

1. Overrides are applied **before** `latest_offers.json` is written, so an
   approval publishes on the next run.
2. An override is keyed by `candidate_id` and records the `source_hash` it was
   decided against.
3. A rejection is terminal for that payload hash. The same listing cannot
   re-enter the queue unless its `source_hash` changes.
4. An approval sets `match_method: "manual"` and `confidence` at the reviewer's
   stated value; it never invents a confidence of 1.0.
5. Overrides are append-only. Corrections are new decisions, not edits.
6. `review_queue.json` is regenerated; `overrides.json` is never regenerated.

---

## SQLite target DDL

The JSON model maps to a relational backend without shape changes. This DDL is
the **target** for the migration path, not a Phase 1 deliverable.

Mapping rules:

- `price_aud` → `price_cents INTEGER` (`round(Decimal * 100)`).
  `point_value_aud` (`0.005`) → `point_value_micros` because cents cannot
  express a half-cent. `points_value_aud` is derived at read time from
  `points_earned × point_value_micros` and is not stored.
- Timestamps → `TEXT` ISO 8601 UTC; SQLite sorts these correctly.
- Booleans → `INTEGER` with `CHECK (x IN (0,1))`.
- Enums → `TEXT` with an explicit `CHECK` list.
- Arrays that are queried or joined → child tables (below).
- Arrays that are display-only → `*_json TEXT CHECK (json_valid(...))`.
- Derived amounts stay derived. They may be generated columns so the database
  cannot disagree with itself.

Which JSON nesting becomes a child table:

| JSON path | Becomes | Why |
|---|---|---|
| `Product.configuration` | flattened columns on `products` | 1:1 and used by the matcher |
| `Product.tags` | `tags_json` | display only |
| `Product.gtins` | `product_gtins` | matching by barcode |
| `Seller.regions` | `seller_regions` | filtered by state |
| `Seller.categories` | `seller_categories` | filtered by category |
| `Offer.pickup_locations` | `pickup_locations_json` | display only |
| `EducationOffer.eligibility` | `education_offer_eligibility` | eligibility filter |
| `GiftCardOffer.risks` | `gift_card_risks` | shown as warnings, queried by tests |
| `GiftCardOffer.redemption_scope` | `gift_card_redemption_scope` | join to sellers |
| `MatchCandidate.config_diff` | `match_diffs` | one row per mismatched field |
| `RunManifest.sources` | `run_sources` | one row per source per run |
| `RunManifest.rule_versions` | `run_rule_versions` | audit |
| `PurchaseRoute.components` | `route_components` | money must be auditable |
| `PurchaseRoute.evidence_offer_ids` | `route_evidence` | many-to-many |

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE sellers (
    seller_id                 TEXT PRIMARY KEY,
    display_name              TEXT NOT NULL,
    seller_type               TEXT NOT NULL CHECK (seller_type IN (
                                  'apple_direct','authorised_reseller','specialist_reseller',
                                  'marketplace','carrier','department_store','education',
                                  'business','gift_card_program','cashback_platform','supermarket')),
    website                   TEXT NOT NULL,
    search_url                TEXT,
    country                   TEXT NOT NULL CHECK (country = 'AU'),
    collection_method         TEXT NOT NULL CHECK (collection_method IN (
                                  'automated','curated','quote_only','manual')),
    requests_per_minute       INTEGER CHECK (requests_per_minute > 0),
    burst                     INTEGER CHECK (burst > 0),
    crawl_delay_seconds       REAL NOT NULL CHECK (crawl_delay_seconds >= 0),
    policy_status             TEXT NOT NULL CHECK (policy_status IN (
                                  'verified_allowed','verified_denied','unverified',
                                  'quote_only_by_design')),
    policy_evidence_url       TEXT,
    policy_checked_at         TEXT,
    authorised                INTEGER NOT NULL CHECK (authorised IN (0,1)),
    authorisation_evidence_url TEXT,
    warranty_url              TEXT,
    returns_url               TEXT,
    delivery_url              TEXT,
    trust_score               REAL NOT NULL CHECK (trust_score BETWEEN 0 AND 1),
    grey_import_risk          TEXT NOT NULL CHECK (grey_import_risk IN
                                  ('none','possible','known','unknown')),
    warranty_provider         TEXT NOT NULL CHECK (warranty_provider IN
                                  ('apple_au','seller','unknown')),
    adapter_module            TEXT,
    enabled                   INTEGER NOT NULL CHECK (enabled IN (0,1)),
    enabled_reason            TEXT,
    notes                     TEXT,
    CHECK (enabled = 0 OR (policy_evidence_url IS NOT NULL
                           AND policy_checked_at IS NOT NULL
                           AND policy_status IN ('verified_allowed','quote_only_by_design'))),
    CHECK (enabled = 1 OR enabled_reason IS NOT NULL)
);

CREATE TABLE seller_regions (
    seller_id TEXT NOT NULL REFERENCES sellers(seller_id),
    region    TEXT NOT NULL CHECK (region IN
                  ('national','VIC','NSW','QLD','WA','SA','TAS','ACT','NT')),
    PRIMARY KEY (seller_id, region)
);

CREATE TABLE seller_categories (
    seller_id TEXT NOT NULL REFERENCES sellers(seller_id),
    category  TEXT NOT NULL CHECK (category IN
                  ('mac','ipad','iphone','watch','airpods','apple_tv','homepod','display','accessory')),
    PRIMARY KEY (seller_id, category)
);

CREATE TABLE products (
    product_id          TEXT PRIMARY KEY,
    canonical_name      TEXT NOT NULL,
    family              TEXT NOT NULL,
    category            TEXT NOT NULL CHECK (category IN
                            ('mac','ipad','iphone','watch','airpods','apple_tv','homepod','display','accessory')),
    model_year          INTEGER,
    chip                TEXT,
    screen_inches       REAL,
    storage_gb          INTEGER CHECK (storage_gb > 0),
    memory_gb           INTEGER CHECK (memory_gb > 0),
    connectivity        TEXT CHECK (connectivity IS NULL OR connectivity IN
                            ('wifi','cellular','wifi_cellular','gps','gps_cellular','wired','bluetooth')),
    size_mm             INTEGER,
    colour              TEXT,
    apple_sku           TEXT UNIQUE,
    rrp_cents           INTEGER CHECK (rrp_cents >= 0),
    rrp_verification    TEXT NOT NULL CHECK (rrp_verification IN
                            ('verified','unverified','stale')),
    rrp_evidence_url    TEXT,
    rrp_captured_at     TEXT,
    apple_au_url        TEXT NOT NULL,
    education_eligible  INTEGER CHECK (education_eligible IN (0,1)),
    lifecycle_status    TEXT NOT NULL CHECK (lifecycle_status IN
                            ('active','discontinued','superseded')),
    superseded_by       TEXT REFERENCES products(product_id),
    tags_json           TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tags_json)),
    first_seen_at       TEXT NOT NULL,
    last_updated_at     TEXT NOT NULL,
    CHECK ((rrp_cents IS NULL AND rrp_verification = 'unverified')
           OR (rrp_cents IS NOT NULL
               AND rrp_verification IN ('verified','stale')
               AND rrp_evidence_url IS NOT NULL
               AND rrp_captured_at IS NOT NULL)),
    CHECK (superseded_by IS NULL OR lifecycle_status = 'superseded')
);

CREATE TABLE product_gtins (
    product_id TEXT NOT NULL REFERENCES products(product_id),
    gtin       TEXT NOT NULL UNIQUE,
    PRIMARY KEY (product_id, gtin)
);

CREATE TABLE run_manifests (
    run_id             TEXT PRIMARY KEY,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    status             TEXT NOT NULL CHECK (status IN ('success','partial','failed')),
    trigger            TEXT NOT NULL CHECK (trigger IN
                           ('schedule','workflow_dispatch','local','backfill')),
    git_sha            TEXT,
    workflow_run_url   TEXT,
    config_hash        TEXT NOT NULL CHECK (length(config_hash) = 64),
    fetched            INTEGER NOT NULL DEFAULT 0,
    parsed             INTEGER NOT NULL DEFAULT 0,
    offers_written     INTEGER NOT NULL DEFAULT 0,
    matched            INTEGER NOT NULL DEFAULT 0,
    rejected_low_confidence INTEGER NOT NULL DEFAULT 0,
    review_queue_delta INTEGER NOT NULL DEFAULT 0,
    sellers_attempted  INTEGER NOT NULL DEFAULT 0,
    sellers_blocked    INTEGER NOT NULL DEFAULT 0,
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE run_rule_versions (
    run_id       TEXT NOT NULL REFERENCES run_manifests(run_id),
    ruleset      TEXT NOT NULL,
    version      TEXT NOT NULL,
    PRIMARY KEY (run_id, ruleset)
);

CREATE TABLE offers (
    offer_id             TEXT PRIMARY KEY,
    offer_type           TEXT NOT NULL DEFAULT 'retail' CHECK (offer_type = 'retail'),
    product_id           TEXT NOT NULL REFERENCES products(product_id),
    seller_id            TEXT NOT NULL REFERENCES sellers(seller_id),
    price_cents          INTEGER NOT NULL CHECK (price_cents > 0),
    currency             TEXT NOT NULL CHECK (currency = 'AUD'),
    price_basis          TEXT NOT NULL CHECK (price_basis IN
                             ('rrp','retail','sale','clearance',
                              'business_quote','epp','refurbished','bundle')),
    was_price_cents      INTEGER CHECK (was_price_cents > 0),
    stock_status         TEXT NOT NULL CHECK (stock_status IN
                             ('in_stock','low_stock','out_of_stock','preorder','unknown','discontinued')),
    pickup_available     INTEGER CHECK (pickup_available IN (0,1)),
    pickup_locations_json TEXT CHECK (pickup_locations_json IS NULL
                                      OR json_valid(pickup_locations_json)),
    condition            TEXT NOT NULL CHECK (condition IN ('new','refurbished','used')),
    sold_by              TEXT,
    url                  TEXT NOT NULL,
    captured_at          TEXT NOT NULL,
    source_hash          TEXT NOT NULL CHECK (length(source_hash) = 64),
    run_id               TEXT NOT NULL REFERENCES run_manifests(run_id),
    verification_status  TEXT NOT NULL CHECK (verification_status IN
                             ('verified','unverified','stale')),
    confidence           REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    match_method         TEXT NOT NULL CHECK (match_method IN
                             ('apple_sku','gtin','configuration','fuzzy','manual')),
    promotion_type       TEXT NOT NULL CHECK (promotion_type IN
                             ('none','price_drop','sale','clearance','bundle','cashback',
                              'gift_card','education','trade_in','member_price','coupon')),
    promotion_text       TEXT,
    promotion_ends_at    TEXT,
    grey_import          INTEGER NOT NULL DEFAULT 0 CHECK (grey_import IN (0,1)),
    grey_import_risk     TEXT NOT NULL CHECK (grey_import_risk IN
                             ('none','possible','known','unknown')),
    warranty_provider    TEXT NOT NULL CHECK (warranty_provider IN
                             ('apple_au','seller','unknown')),
    delivery_cost_cents  INTEGER CHECK (delivery_cost_cents >= 0),
    notes                TEXT,
    UNIQUE (seller_id, product_id, price_basis, captured_at)
);

CREATE TABLE education_offers (
    offer_id              TEXT PRIMARY KEY,
    offer_type            TEXT NOT NULL DEFAULT 'education' CHECK (offer_type = 'education'),
    product_id            TEXT NOT NULL REFERENCES products(product_id),
    seller_id             TEXT NOT NULL REFERENCES sellers(seller_id),
    standard_price_cents  INTEGER NOT NULL CHECK (standard_price_cents > 0),
    education_price_cents INTEGER NOT NULL CHECK (education_price_cents > 0),
    saving_cents          INTEGER GENERATED ALWAYS AS
                              (standard_price_cents - education_price_cents) STORED,
    saving_pct            REAL GENERATED ALWAYS AS
                              (ROUND((standard_price_cents - education_price_cents) * 100.0
                                     / standard_price_cents, 2)) STORED,
    verification_required INTEGER NOT NULL CHECK (verification_required IN (0,1)),
    verification_vendor   TEXT,
    annual_limit_summary  TEXT,
    limits_source_url     TEXT,
    stock_status          TEXT NOT NULL CHECK (stock_status IN
                              ('in_stock','low_stock','out_of_stock','preorder','unknown','discontinued')),
    url                   TEXT NOT NULL,
    captured_at           TEXT NOT NULL,
    source_hash           TEXT NOT NULL CHECK (length(source_hash) = 64),
    run_id                TEXT NOT NULL REFERENCES run_manifests(run_id),
    verification_status   TEXT NOT NULL CHECK (verification_status IN
                              ('verified','unverified','stale')),
    confidence            REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    match_method          TEXT NOT NULL CHECK (match_method IN
                              ('apple_sku','gtin','configuration','fuzzy','manual')),
    CHECK (education_price_cents <= standard_price_cents)
);

CREATE TABLE education_offer_eligibility (
    offer_id TEXT NOT NULL REFERENCES education_offers(offer_id),
    category TEXT NOT NULL CHECK (category IN
                 ('tertiary_student','parent_guardian','teacher','education_staff','homeschool')),
    PRIMARY KEY (offer_id, category)
);

CREATE TABLE gift_card_offers (
    gift_card_offer_id    TEXT PRIMARY KEY,
    seller_id             TEXT NOT NULL REFERENCES sellers(seller_id),
    card_type             TEXT NOT NULL CHECK (card_type IN
                              ('apple_gift_card','retailer_gift_card','prepaid_visa','loyalty_points')),
    face_value_cents      INTEGER NOT NULL CHECK (face_value_cents > 0),
    discount_pct          REAL NOT NULL CHECK (discount_pct BETWEEN 0 AND 100),
    points_earned         INTEGER CHECK (points_earned >= 0),
    point_value_micros    INTEGER CHECK (point_value_micros >= 0),   -- 1e-6 AUD; 0.005 AUD = 5000
    points_program        TEXT,
    cashback_pct          REAL CHECK (cashback_pct BETWEEN 0 AND 100),
    effective_cost_cents  INTEGER NOT NULL CHECK (effective_cost_cents >= 0),
    effective_discount_pct REAL NOT NULL CHECK (effective_discount_pct BETWEEN 0 AND 100),
    starts_at             TEXT NOT NULL,
    ends_at               TEXT,
    limits                TEXT,
    source_url            TEXT NOT NULL,
    captured_at           TEXT NOT NULL,
    source_hash           TEXT NOT NULL CHECK (length(source_hash) = 64),
    verification_status   TEXT NOT NULL CHECK (verification_status IN
                              ('verified','unverified','stale')),
    run_id                TEXT NOT NULL REFERENCES run_manifests(run_id),
    CHECK (ends_at IS NULL OR ends_at > starts_at),
    CHECK (points_earned IS NULL OR point_value_micros IS NOT NULL)
);

CREATE TABLE gift_card_risks (
    gift_card_offer_id TEXT NOT NULL REFERENCES gift_card_offers(gift_card_offer_id),
    risk               TEXT NOT NULL,
    PRIMARY KEY (gift_card_offer_id, risk)
);

CREATE TABLE gift_card_redemption_scope (
    gift_card_offer_id TEXT NOT NULL REFERENCES gift_card_offers(gift_card_offer_id),
    seller_id          TEXT NOT NULL REFERENCES sellers(seller_id),
    PRIMARY KEY (gift_card_offer_id, seller_id)
);

CREATE TABLE deals (
    deal_id              TEXT PRIMARY KEY,
    deal_type            TEXT NOT NULL CHECK (deal_type IN
                             ('price_drop','rrp_discount','all_time_low','education_saving',
                              'gift_card','cashback','bundle_value','clearance','restock')),
    product_id           TEXT NOT NULL REFERENCES products(product_id),
    seller_id            TEXT NOT NULL REFERENCES sellers(seller_id),
    current_price_cents  INTEGER NOT NULL CHECK (current_price_cents > 0),
    reference_price_cents INTEGER NOT NULL CHECK (reference_price_cents > 0),
    saving_cents         INTEGER GENERATED ALWAYS AS
                             (reference_price_cents - current_price_cents) STORED,
    saving_pct           REAL GENERATED ALWAYS AS
                             (ROUND((reference_price_cents - current_price_cents) * 100.0
                                    / reference_price_cents, 2)) STORED,
    confidence           REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence_url         TEXT NOT NULL,
    captured_at          TEXT NOT NULL,
    effective_price_cents INTEGER CHECK (effective_price_cents >= 0),
    starts_at            TEXT,
    expires_at           TEXT,
    rule_id              TEXT NOT NULL,
    rule_version         TEXT NOT NULL,
    verification_status  TEXT NOT NULL CHECK (verification_status IN
                             ('verified','unverified','stale')),
    first_detected_at    TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    is_current           INTEGER NOT NULL CHECK (is_current IN (0,1)),
    run_id               TEXT NOT NULL REFERENCES run_manifests(run_id),
    CHECK (expires_at IS NULL OR captured_at <= expires_at),
    CHECK (is_current = 0 OR expires_at IS NULL OR expires_at > captured_at)
);

CREATE TABLE match_candidates (
    candidate_id        TEXT PRIMARY KEY,
    seller_id           TEXT NOT NULL REFERENCES sellers(seller_id),
    raw_title           TEXT NOT NULL,
    price_cents         INTEGER NOT NULL CHECK (price_cents > 0),
    proposed_product_id TEXT REFERENCES products(product_id),
    method              TEXT NOT NULL CHECK (method IN
                            ('apple_sku','gtin','configuration','fuzzy','manual')),
    confidence          REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    normalised_title    TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN
                            ('pending','approved','rejected','auto_matched','expired')),
    evidence_url        TEXT NOT NULL,
    captured_at         TEXT NOT NULL,
    source_hash         TEXT NOT NULL CHECK (length(source_hash) = 64),
    created_run_id      TEXT NOT NULL REFERENCES run_manifests(run_id),
    reviewed_by         TEXT,
    reviewed_at         TEXT,
    review_note         TEXT,
    CHECK (status IN ('pending','auto_matched') OR reviewed_by IS NOT NULL)
);

CREATE TABLE match_diffs (
    candidate_id TEXT NOT NULL REFERENCES match_candidates(candidate_id),
    field        TEXT NOT NULL,
    expected     TEXT,
    got          TEXT,
    PRIMARY KEY (candidate_id, field)
);

CREATE TABLE run_sources (
    run_id       TEXT NOT NULL REFERENCES run_manifests(run_id),
    seller_id    TEXT NOT NULL REFERENCES sellers(seller_id),
    status       TEXT NOT NULL CHECK (status IN
                     ('ok','blocked_by_policy','network_error','parse_error',
                      'rate_limited','skipped','disabled')),
    http_status  INTEGER,
    fetched      INTEGER NOT NULL DEFAULT 0,
    parsed       INTEGER NOT NULL DEFAULT 0,
    matched      INTEGER NOT NULL DEFAULT 0,
    rejected     INTEGER NOT NULL DEFAULT 0,
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    PRIMARY KEY (run_id, seller_id)
);

CREATE TABLE purchase_routes (
    route_id              TEXT PRIMARY KEY,
    product_id            TEXT NOT NULL REFERENCES products(product_id),
    route_type            TEXT NOT NULL CHECK (route_type IN
                              ('apple_retail','apple_education','corporate_epp',
                               'business_quote','trade_in','gift_card','cashback',
                               'refurbished','grey_import')),
    seller_id             TEXT REFERENCES sellers(seller_id),
    headline_price_cents  INTEGER NOT NULL CHECK (headline_price_cents >= 0),
    effective_price_cents INTEGER NOT NULL CHECK (effective_price_cents >= 0),
    saving_vs_apple_cents INTEGER NOT NULL,
    eligibility_required  INTEGER NOT NULL CHECK (eligibility_required IN (0,1)),
    eligibility_notes     TEXT,
    expires_at            TEXT,
    confidence            REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    rank                  INTEGER NOT NULL CHECK (rank > 0),
    available             INTEGER NOT NULL CHECK (available IN (0,1)),
    notes                 TEXT,
    UNIQUE (product_id, route_type)
);

CREATE TABLE route_components (
    route_id    TEXT NOT NULL REFERENCES purchase_routes(route_id),
    sequence    INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    offer_id    TEXT,
    amount_cents INTEGER NOT NULL,
    label       TEXT NOT NULL,
    PRIMARY KEY (route_id, sequence)
);

CREATE TABLE route_evidence (
    route_id TEXT NOT NULL REFERENCES purchase_routes(route_id),
    offer_id TEXT NOT NULL,
    PRIMARY KEY (route_id, offer_id)
);
```

Notes on the DDL:

- `offers.run_id` references `run_manifests`, so the manifest must be inserted
  before offers. In JSON this is a plain string; the loader orders writes.
- `offers` has `UNIQUE (seller_id, product_id, price_basis, captured_at)`,
  which permits history but forbids two observations of the same offer at the
  same instant.
- `education_offers` deliberately does **not** inherit from `offers`: separate
  table, separate eligibility, impossible to merge by accident.
- Generated columns make `saving_*` and `effective_*` impossible to hand-edit.
- The `CHECK` on `sellers` encodes the policy gate: enabled sellers have
  evidence and a policy decision.

## Migration path

Phase 1 (M1–M4) has **no database**. JSON files under `data/` are the store of
record, and git is the transaction log. Reasons are in
[ARCHITECTURE.md](ARCHITECTURE.md#why-no-database-in-phase-1).

| Phase | Store | Write path | Read path | Trigger to move |
|---|---|---|---|---|
| 1 (now) | JSON in git | scripts write, CI commits (from M2) | scripts + static site build | works while files stay under ~50 MB and full-file rewrite is fast |
| 2 | SQLite (`data/orchard.sqlite`) as a build artefact | loader rebuilds from JSON, then scripts query it | scripts and site generator query a snapshot DB | catalogue or offer volume makes JSON scans slow, or joins are needed for analytics |
| 3 | DuckDB over Parquet exports of snapshots | nightly export | analysis notebooks, data-quality report | history analysis across months outgrows SQLite |

Rules for any move:

1. The relational schema is the DDL above, versioned in `docs/` first.
2. A migration is proven by a golden test: same inputs → byte-identical
   `latest_offers.json`, `current_deals.json` and `current_offers.json`.
3. Committed JSON stays the public contract even after a database exists, so
   the site and outside readers never depend on the database.
4. `data/snapshots/` remains append-only in every phase. A database is an index
   over evidence, never a replacement for it.
5. The database is rebuilt, not backed up as authoritative. Losing it costs one
   run, not the dataset.

## Change protocol

- Changing a field name, type or enum value is a `schema_version` bump and a
  change to this document **before** the code change.
- Adding an optional field is additive and does not require a bump.
- Removing a field requires a deprecation note here and a migration step for
  committed data.
- No derived value (`saving_*`, `effective_*`, `is_current`, `rank`) is ever
  hand-edited in a JSON file; validation recomputes and fails on mismatch.