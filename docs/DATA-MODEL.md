# Data model

**Status:** current. This document describes the models in `scripts/models/` and
the generated schemas in `data/schema/`. Those two are the source of truth; this
document is a derived explanation. If they disagree, the code wins and this file
is the bug.

Every field below is verifiable in `scripts/models/*.py` and in
`data/schema/*.json`. Nothing here is aspirational: there is no field that only
exists in prose.

## Generated artefacts

`data/schema/*.json` is generated. Do not hand-edit it.

```bash
uv run python -m scripts.models.export_schema          # write the 10 schemas
uv run python -m scripts.models.export_schema --check  # CI: fail on drift
```

| Artefact | Produced by | Rule |
|---|---|---|
| `data/schema/product.schema.json` | `Product` | generated |
| `data/schema/seller.schema.json` | `Seller` | generated |
| `data/schema/offer.schema.json` | `Offer` | generated |
| `data/schema/education_offer.schema.json` | `EducationOffer` | generated |
| `data/schema/gift_card_offer.schema.json` | `GiftCardOffer` | generated |
| `data/schema/deal.schema.json` | `Deal` | generated |
| `data/schema/purchase_route.schema.json` | `PurchaseRoute` | generated |
| `data/schema/match_candidate.schema.json` | `MatchCandidate` | generated |
| `data/schema/run_manifest.schema.json` | `RunManifest` | generated |
| `data/schema/run_error.schema.json` | `RunError` | generated |

The exporter renders validation-mode JSON Schema, sorted and indented, with
`additionalProperties: false`. `--check` writes nothing and exits `1` if a
committed schema is missing or stale. A model change without a committed schema
change fails the check, so the two cannot drift.

## Conventions

`OrchardModel` is the base class for every entity. It sets `extra="forbid"`,
strips surrounding whitespace, validates on assignment and accepts field names
or aliases.

| Topic | Rule |
|---|---|
| Currency | AUD only. A non-AUD currency marker in an amount raises. |
| Money | `Decimal` in Python, **JSON number** in committed files, integer cents in SQLite. Quantised to two decimal places, half-up. |
| Percentages | `Decimal` in Python, JSON number between 0 and 100, at most two decimals. |
| Confidence | `Decimal` in Python, JSON number between 0 and 1. |
| Timestamps | Timezone-aware `datetime`, converted to UTC, serialised ISO 8601 with a trailing `Z`. Naive datetimes are rejected. |
| Source hash | `sha256:` followed by exactly 64 lowercase hex characters (71 characters total). |
| Nullability | `null` means unknown or absent. It never means zero. Defaults are applied before serialisation, so a validated record always carries every field. |
| Unknown fields | A hard error. There is no sidecar data. |
| Arrays | Order is significant only where noted. |

### Why money is a JSON number

The canonical dataset examples use numbers, and the static site formats every
amount with `Intl.NumberFormat` in `en-AU`, so a string in the file would only
be parsed back into a number at render. The exactness lives on the Python side:
`AUDMoney` coerces the input, quantises to cents with `ROUND_HALF_UP` and rejects
more than two decimals. Arithmetic never touches binary floating point.

Two consequences to know:

1. Serialisation goes through `float`, so a whole-dollar amount appears as
   `2099.0` in a dump, not `2099.00`. The value is exact to the cent; the
   textual precision is not. Read it as a number.
2. Percentages and confidence are stored the same way, for the same reason.

### Why the site formats and the data does not

Money in `data/` is machine-readable, not presentation-ready. The site applies
locale, currency symbol, grouping and the "indicative" label. A formatted string
in a data file would bake in one presentation and break every other consumer.

## Identifier conventions

The models constrain identifiers to a non-empty string. They do **not** enforce
a format. The conventions actually used in the repository are:

| Identifier | Observed convention | Example in the repo |
|---|---|---|
| `product_id` | `apple-{family}-{screen}-{chip}-{memory}-{storage}-{colour}`, lowercase, hyphenated, segments omit absent axes | `apple-macbook-air-13-inch-m5-16gb-512gb-midnight` |
| `offer_id` | `{kind}-{seller_id}-{product slug}-{yyyymmdd}` in the test fixtures; no runtime dataset yet | `ofr-apple-au-macbook-air-13-m4-20261115` |
| `deal_id` | `deal-{seller_id}-{product slug}-{yyyymmdd}` in the test fixtures | `deal-jbhifi-macbook-air-13-m4-20261115` |
| `candidate_id` | `cand-{seller_id}-{yyyymmdd}-{sequence}` in the test fixtures | `cand-jbhifi-20261115-001` |
| `run_id` | `run-{yyyymmdd}T{hhmm}` in the test fixtures | `run-20261115T0800` |
| `seller_id` | lowercase snake_case, fixed at registry time | `apple_education_au` |

These are conventions, not schema rules. A change to one is a data migration,
not a model change. The deterministic generator for `offer_id` belongs to the
publishing card and must be documented here when it lands.

## Enum catalogue

Values are persisted strings. They come from `scripts/models/enums.py` and appear
verbatim in the generated schemas.

| Enum | Values |
|---|---|
| `Category` | `mac`, `ipad`, `iphone`, `watch`, `airpods`, `tv`, `homepod`, `accessory`, `applecare` |
| `SellerCategory` | the nine product categories plus `business`, `education`, `gift_card`, `cashback` |
| `SellerType` | `apple_direct`, `national_reseller`, `specialist_reseller`, `online_marketplace`, `telco`, `business_reseller`, `education_reseller`, `gift_card_seller` |
| `CollectionMethod` | `automated_html`, `automated_jsonld`, `automated_api`, `curated`, `manual` |
| `StockStatus` | `in_stock`, `low_stock`, `out_of_stock`, `preorder`, `unknown` |
| `PriceBasis` | `advertised`, `member`, `bundle`, `clearance`, `quote`, `edu`, `epp` |
| `PromotionType` | `sale`, `clearance`, `bundle`, `member`, `gift_card`, `cashback` |
| `VerificationStatus` | `verified`, `indicative`, `curated_unverified`, `unverified` |
| `DealType` | `price_drop`, `rrp_discount`, `all_time_low`, `education`, `gift_card`, `retailer_gift_card`, `cashback`, `bundle`, `clearance`, `restock` |
| `MatchMethod` | `apple_sku`, `gtin`, `config`, `fuzzy`, `manual` |
| `MatchStatus` | `pending`, `approved`, `rejected`, `auto_published` |
| `ProductStatus` | `active`, `discontinued`, `superseded` |
| `Priority` | `high`, `normal` |
| `RrpVerdict` | `verified`, `unverified` |
| `CardType` | `apple_gift_card`, `retailer_gift_card` |
| `GiftCardChannel` | `in_store`, `online`, `both` |
| `PurchaseRouteType` | `apple_retail`, `apple_education`, `epp`, `business_quote`, `gift_card`, `cashback` |

`VerificationStatus.INDICATIVE` is a stored verdict. It is also what the
freshness policy assigns to an expired record, so a page does not need a second
word for "stale". `CURATED_UNVERIFIED` is for a human-entered price that has a
source but no machine capture.

## Dataset files

The validator knows these paths and wrapper keys (`scripts/validation/validate_data.py`).
A dataset that does not exist is treated as empty with a warning.

| Dataset | Accepted paths | Accepted list keys |
|---|---|---|
| products | `products.json`, `catalogue/products.json` | `products`, `items` |
| sellers | `sellers.json` | `sellers`, `items` |
| offers | `latest_offers.json`, `offers/latest.json` | `offers`, `latest`, `items` |
| education | `education_offers.json`, `education.json`, `education/offers.json` | `education_offers`, `education`, `offers`, `items` |
| gift cards | `gift_cards/offers.json`, `gift_cards/latest.json`, `gift_cards.json` | `gift_cards`, `gift_card_offers`, `offers`, `items` |
| deals | `deals/latest.json`, `deals.json`, `deals/offers.json` | `deals`, `items` |
| candidates | `matching/review_queue.json` | `candidates`, `review_queue`, `pending`, `queue`, `items` |
| runs | `runs.json`, `health/runs.json` | `runs`, `manifests`, `items` |

A file may also be a bare JSON array. Product and seller files currently carry
extra top-level keys (`generated_at`, `source_note`) beside the list; those are
file metadata, not model fields.

---

## Product

One configuration of an Apple product sold in Australia. `data/products.json`
holds 20 seed products.

| Field | Python type | JSON type | Null | Constraints and validators | Example |
|---|---|---|---|---|---|
| `product_id` | `str` | string | no | non-empty | `apple-macbook-air-13-inch-m5-16gb-512gb-midnight` |
| `apple_sku` | `str \| None` | string | yes | none | `MDHE4X/A` |
| `gtin` | `str \| None` | string | yes | `^(\d{8}\|\d{12,14})$` | `0194253949880` |
| `category` | `Category` | string | no | enum | `mac` |
| `family` | `str` | string | no | non-empty; `macbook_air`, `iphone`, `ipad_pro`… | `macbook_air` |
| `name` | `str` | string | no | non-empty; the display name | `MacBook Air 13-inch M5 16GB 512GB Midnight` |
| `brand` | `Literal["Apple"]` | string | no | fixed `Apple` | `Apple` |
| `model` | `str` | string | no | non-empty; marketing line | `MacBook Air` |
| `configuration` | `dict[str, str]` | object | no | default `{}`; free-form string map, conventional keys below | `{"storage": "512GB"}` |
| `apple_au_url` | `HttpUrl` | string (URI) | no | absolute URL | `https://www.apple.com/au/shop/buy-mac/macbook-air` |
| `apple_rrp_aud` | `Decimal \| None` | number | yes | 2dp, `>= 0` | `2099.00` |
| `education_eligible` | `bool` | boolean | no | default `false` | `true` |
| `status` | `ProductStatus` | string | no | default `active` | `active` |
| `priority` | `Priority` | string | no | default `normal` | `high` |
| `specs_source_url` | `HttpUrl \| None` | string (URI) | yes | evidence for the RRP and specs | `https://www.apple.com/au/shop/buy-mac/macbook-air` |
| `rrp_verification` | `RrpVerdict` | string | no | default `unverified` | `verified` |
| `created_at` | `datetime` | string (date-time) | no | UTC | `2026-11-15T00:00:00Z` |
| `updated_at` | `datetime` | string (date-time) | no | UTC; `>= created_at` | `2026-11-15T00:00:00Z` |

Validators (`Product._check_rrp_evidence`):

- `rrp_verification: verified` requires a non-null `apple_rrp_aud` **and** a
  `specs_source_url`. A verified price without evidence is rejected.
- `updated_at` may not be earlier than `created_at`.

### `configuration`

`configuration` is a `dict[str, str]`: keys are free-form and values are always
strings, never numbers. The conventional keys, defined in
`KNOWN_CONFIGURATION_KEYS`, are:

| Key | Meaning | Example |
|---|---|---|
| `screen_size` | nominal diagonal | `13-inch` |
| `chip` | processor generation | `Apple M5 (10-core CPU, 8-core GPU)` |
| `memory` | unified memory or RAM | `16GB` |
| `storage` | storage capacity | `512GB` |
| `colour` | Apple's colour name | `Midnight` |
| `connectivity` | wireless variant | `Wi-Fi` |
| `cellular` | cellular variant flag | `Cellular` |
| `generation` | product generation | `2nd generation` |
| `size` | case or accessory size | `42mm` |

Extra keys are permitted so a new axis does not need a model change. Values stay
strings so `16GB` and `16 GB` can be normalised by the matcher rather than being
coerced differently in two places.

## Seller

One row per source or procurement route. `data/sellers.json` holds 29 sellers:
10 enabled, 19 disabled, 8 quote-only.

| Field | Python type | JSON type | Null | Constraints and validators | Example |
|---|---|---|---|---|---|
| `seller_id` | `str` | string | no | non-empty | `jbhifi` |
| `display_name` | `str` | string | no | non-empty | `JB Hi-Fi` |
| `seller_type` | `SellerType` | string | no | enum | `national_reseller` |
| `website` | `HttpUrl \| None` | string (URI) | yes | required when enabled | `https://www.jbhifi.com.au/` |
| `product_search_url` | `HttpUrl \| None` | string (URI) | yes | required when enabled | `https://www.jbhifi.com.au/search?query=apple` |
| `country` | `str` | string | no | default `AU`; 2 letters, upper-cased | `AU` |
| `supported_regions` | `list[str]` | array of string | no | default `["AU"]`, at least one | `["AU"]` |
| `categories` | `list[SellerCategory]` | array of string | no | at least one; enum values | `["mac", "ipad", "gift_card"]` |
| `collection_method` | `CollectionMethod` | string | no | enum | `curated` |
| `rate_limit_rps` | `float` | number | no | 0–20; `0` for curated-only | `0.0` |
| `crawl_delay_seconds` | `float` | number | no | 0–120 | `5` |
| `authorised_reseller` | `bool \| None` | boolean | yes | `true` requires evidence URL | `null` |
| `authorised_evidence_url` | `HttpUrl \| None` | string (URI) | yes | manufacturer page | `https://www.apple.com/au/` |
| `warranty_policy_url` | `HttpUrl \| None` | string (URI) | yes | never guessed | `https://www.apple.com/au/legal/warranty/` |
| `returns_policy_url` | `HttpUrl \| None` | string (URI) | yes | never guessed | `https://www.apple.com/au/shop/help/returns_refund` |
| `delivery_policy_url` | `HttpUrl \| None` | string (URI) | yes | never guessed | `https://www.apple.com/au/shop/help/shipping_delivery` |
| `trust_score_inputs` | `dict[str, str \| int \| float \| bool]` | object | no | default `{}`; heterogeneous scalar map | see below |
| `adapter_module` | `str` | string | no | default `""`; dotted path; required for automated methods | `scripts.collectors.apple_au` |
| `enabled` | `bool` | boolean | no | default `true` | `true` |
| `enabled_reason` | `str \| None` | string | yes | required when `enabled` is false | `Phase 2. Would be enabled after …` |
| `quote_only` | `bool` | boolean | no | default `false`; no price can be captured | `false` |
| `last_success_at` | `datetime \| None` | string (date-time) | yes | UTC | `null` |
| `last_failure_at` | `datetime \| None` | string (date-time) | yes | UTC | `null` |
| `notes` | `str` | string | no | default `""`; evidence and policy prose | see `data/sellers.json` |

Validators (`Seller._check_registry_consistency`):

- `authorised_reseller: true` requires `authorised_evidence_url`.
- `enabled: false` requires a non-empty `enabled_reason`.
- `enabled: true` requires `website` and `product_search_url`.
- An automated `collection_method` requires a non-empty `adapter_module` and
  `rate_limit_rps > 0`.

### `trust_score_inputs`

This is a map, not a scalar. Each seller carries the inputs to its trust score,
with keys chosen per seller class. The keys used in `data/sellers.json` are:

| Key | Scale | Meaning |
|---|---|---|
| `apple_authorised` | 0.0–1.0 | manufacturer authorisation |
| `marketplace_third_party_risk` | 0.0–1.0 | **1.0 means no third-party exposure**, 0.0 means an open marketplace |
| `returns_clarity` | 0.0–1.0 | how clearly returns are stated |
| `stock_reporting` | 0.0–1.0 | how reliable stock signals are |
| `price_transparency` | 0.0–1.0 | whether the displayed price is the payable price |

Nothing aggregates these into a scalar. A trust score is a later derivation, and
the inputs are published so the derivation can be audited.

**Field-name note:** the field is `rate_limit_rps` in both the model and
`data/sellers.json`. `rate_limit_rps_float` is accepted as a legacy alias only;
do not write it in new data.

## Offer

A retail price observation. Education pricing is never an `Offer`.

| Field | Python type | JSON type | Null | Constraints and validators | Example |
|---|---|---|---|---|---|
| `offer_id` | `str` | string | no | non-empty, unique across all offer datasets | `ofr-apple-au-macbook-air-13-m5-20261115` |
| `product_id` | `str` | string | no | must exist in products | `apple-macbook-air-13-inch-m5-16gb-512gb-midnight` |
| `seller_id` | `str` | string | no | must exist in sellers | `apple_au` |
| `url` | `HttpUrl` | string (URI) | no | the captured page | `https://www.apple.com/au/shop/buy-mac/macbook-air` |
| `price_aud` | `Decimal` | number | no | 2dp, `>= 0` | `1999.00` |
| `was_price_aud` | `Decimal \| None` | number | yes | 2dp; `>= price_aud` | `2099.00` |
| `rrp_aud` | `Decimal \| None` | number | yes | 2dp; Apple RRP at capture | `2099.00` |
| `price_basis` | `PriceBasis` | string | no | enum | `advertised` |
| `in_stock` | `bool` | boolean | no | must agree with `stock_status` | `true` |
| `stock_status` | `StockStatus` | string | no | default `unknown`; enum | `in_stock` |
| `delivery_cost_aud` | `Decimal` | number | no | default `0.00`, 2dp | `0.00` |
| `delivery_cost_verified` | `bool` | boolean | no | default `false` | `true` |
| `delivery_estimate` | `str` | string | no | default `""` | `Free delivery` |
| `pickup_available` | `bool` | boolean | no | default `false` | `true` |
| `pickup_locations` | `list[str]` | array of string | no | default `[]` | `["Melbourne CBD"]` |
| `promotion` | `str \| None` | string | yes | required when `promotion_type` is set | `Save $200` |
| `promotion_type` | `PromotionType \| None` | string | yes | enum | `sale` |
| `bundle` | `bool` | boolean | no | default `false` | `false` |
| `bundle_contents` | `list[str]` | array of string | no | default `[]`; required when `bundle` is true | `["AirPods 4", "Case"]` |
| `confidence` | `Decimal` | number | no | 0–1 | `1.0` |
| `match_method` | `MatchMethod` | string | no | enum | `apple_sku` |
| `match_status` | `MatchStatus` | string | no | default `pending`; enum | `auto_published` |
| `verification_status` | `VerificationStatus` | string | no | enum | `verified` |
| `freshness_max_age_hours` | `int` | integer | no | default `24`; 1–720 | `24` |
| `captured_at` | `datetime` | string (date-time) | no | UTC | `2026-11-15T08:00:00Z` |
| `source_hash` | `str` | string | no | `^sha256:[0-9a-f]{64}$` | `sha256:…` |
| `run_id` | `str` | string | no | non-empty; links to a `RunManifest` | `run-20261115T0800` |
| `grey_import` | `bool` | boolean | no | default `false`; can never rank as authorised stock | `false` |
| `sold_by` | `str \| None` | string | yes | marketplace seller of record | `Apple Pty Ltd` |

Validators (`Offer._check_offer_consistency`):

- `was_price_aud >= price_aud`. It may be equal; a "was" price is not required to
  be strictly higher.
- `stock_status` in `in_stock`, `low_stock` or `preorder` requires
  `in_stock: true`; `out_of_stock` requires `in_stock: false`.
- `promotion_type` requires non-empty `promotion`.
- `promotion_type: bundle` requires `bundle: true`, and `bundle` requires at
  least one `bundle_contents` entry.
- `match_status: auto_published` requires `confidence >= 0.85`. Below that the
  match must stay `pending`.

There is no `condition` field. Used and refurbished stock is excluded by source
policy and matching, not by a price field. There is no `warranty_provider`
field; the seller's policy URLs carry that information.

## EducationOffer

A captured Apple Education Store price. Separate entity, separate eligibility,
never merged into `Offer`.

| Field | Python type | JSON type | Null | Constraints and validators | Example |
|---|---|---|---|---|---|
| `offer_id` | `str` | string | no | non-empty, unique across offer datasets | `edu-apple-au-macbook-air-13-m5-20261115` |
| `product_id` | `str` | string | no | must exist in products | `apple-macbook-air-13-inch-m5-16gb-512gb-midnight` |
| `seller_id` | `str` | string | no | must exist in sellers | `apple_education_au` |
| `url` | `HttpUrl` | string (URI) | no | education store page | `https://www.apple.com/au-edu/shop/buy-mac/macbook-air` |
| `price_aud` | `Decimal` | number | no | 2dp; the education price | `1889.10` |
| `standard_price_aud` | `Decimal` | number | no | 2dp; `>= price_aud` | `2099.00` |
| `saving_aud` | `Decimal` | number | no | must equal `standard_price_aud - price_aud` within $0.01 | `209.90` |
| `saving_pct` | `Decimal` | number | no | must equal `saving_aud / standard_price_aud * 100` within 0.01 | `10.00` |
| `eligibility` | `list[str]` | array of string | no | at least one | `["University students", "Teachers"]` |
| `verification` | `str` | string | no | how eligibility is verified, as stated | `Apple AU Education Store price page` |
| `annual_limit_summary` | `str` | string | no | non-empty; sourced, not paraphrased | `One Mac per eligible buyer per year` |
| `annual_limit_source_url` | `HttpUrl` | string (URI) | no | the policy page | `https://www.apple.com/au/shop/browse/open/salespolicies/edu` |
| `in_stock` | `bool` | boolean | no | — | `true` |
| `price_basis` | `PriceBasis` | string | no | default `edu`; must stay `edu` | `edu` |
| `verification_status` | `VerificationStatus` | string | no | enum | `verified` |
| `freshness_max_age_hours` | `int` | integer | no | default `24`; 1–720 | `24` |
| `captured_at` | `datetime` | string (date-time) | no | UTC | `2026-11-15T08:00:00Z` |
| `source_hash` | `str` | string | no | `^sha256:[0-9a-f]{64}$` | `sha256:…` |
| `run_id` | `str` | string | no | links to a `RunManifest` | `run-20261115T0800` |

Validators (`EducationOffer._check_education_pricing`): `price_basis` must be
`edu`; `standard_price_aud >= price_aud`; `saving_aud` and `saving_pct` are
recomputed and must match within $0.01. A discount that does not add up is a
hard failure.

`eligibility` is a list of free-form strings because the store's wording changes;
`verification` records the vendor or mechanism in Apple's own terms. It is not a
closed enum, and the site must render it verbatim.

## GiftCardOffer

A purchased-discount gift card or loyalty-points promotion.

| Field | Python type | JSON type | Null | Constraints and validators | Example |
|---|---|---|---|---|---|
| `offer_id` | `str` | string | no | non-empty, unique | `gift-woolworths-apple-500-20261115` |
| `card_type` | `CardType` | string | no | enum | `retailer_gift_card` |
| `seller_id` | `str` | string | no | must exist in sellers | `woolworths` |
| `loyalty_program` | `str` | string | no | named programme, never "cash" | `Everyday Rewards` |
| `channel` | `GiftCardChannel` | string | no | enum | `both` |
| `face_value_aud` | `Decimal` | number | no | 2dp; `> 0` | `500.00` |
| `purchase_price_aud` | `Decimal` | number | no | 2dp; `<= face_value_aud` | `475.00` |
| `bonus_points_per_dollar` | `Decimal` | number | no | default `0`; `>= 0` | `10` |
| `points_earned` | `Decimal` | number | no | default `0`; `>= 0` | `4750` |
| `assumed_point_value_aud` | `Decimal` | number | no | default `0.005`; up to 4dp; `>= 0` | `0.005` |
| `effective_value_aud` | `Decimal` | number | no | 2dp; `<= face_value_aud` | `451.25` |
| `effective_discount_pct` | `Decimal` | number | no | 0–100; must match `(face - effective) / face` within 0.01 | `9.75` |
| `daily_limit_cards` | `int \| None` | integer | yes | `>= 0` | `5` |
| `starts_at` | `datetime` | string (date-time) | no | UTC | `2026-11-13T00:00:00Z` |
| `ends_at` | `datetime` | string (date-time) | no | must be later than `starts_at` | `2026-11-26T23:59:59Z` |
| `source_url` | `HttpUrl` | string (URI) | no | promotion source | `https://www.woolworths.com.au/…` |
| `verification_status` | `VerificationStatus` | string | no | enum | `verified` |
| `evidence_snapshot` | `str \| None` | string | yes | required when verified | `data/snapshots/woolworths-20261115T0800.html` |
| `terms_url` | `HttpUrl \| None` | string (URI) | yes | terms page | `https://www.woolworths.com.au/terms` |
| `risks` | `list[str]` | array of string | no | at least one | `["Points value may change"]` |

Validators (`GiftCardOffer._check_gift_card_value`): face value positive;
purchase price and effective value may not exceed face; the discount percentage
is recomputed; `ends_at > starts_at`; a `verified` record requires an
`evidence_snapshot`.

Note the absence of `captured_at`, `source_hash` and `run_id` on this model. Its
evidence is the `evidence_snapshot` path plus the promotion window; the snapshot
file carries the capture timestamp. Any change to that is a model change and a
schema regeneration.

### Points valuation

`assumed_point_value_aud` defaults to **$0.005 per point**. The worked example
in the project brief (2,000 points at `0.05` on a $100 card, called a "10%
discount") is arithmetically wrong: 2,000 × 0.05 = $100, which is 100% of the
card. The implemented default gives 2,000 × 0.005 = $10, a 10.00% discount.

Rules: the assumed value is a field on every record, not a hidden constant;
points are labelled as points in copy and never as cash; the effective value is
the card cost after the assumed value of the points, and the arithmetic is
recomputed by the validator.

## Deal

A derived claim of a saving. Recomputed every run; never hand-edited.

| Field | Python type | JSON type | Null | Constraints and validators | Example |
|---|---|---|---|---|---|
| `deal_id` | `str` | string | no | non-empty, unique | `deal-apple-au-macbook-air-13-m5-20261115` |
| `deal_type` | `DealType` | string | no | enum | `price_drop` |
| `product_id` | `str` | string | no | must exist in products | `apple-macbook-air-13-inch-m5-16gb-512gb-midnight` |
| `seller_id` | `str` | string | no | must exist in sellers | `apple_au` |
| `offer_id` | `str \| None` | string | yes | if set, must exist in an offer dataset | `ofr-apple-au-macbook-air-13-m5-20261115` |
| `current_price_aud` | `Decimal` | number | no | 2dp | `1999.00` |
| `reference_price_aud` | `Decimal` | number | no | 2dp; `>= current_price_aud` | `2099.00` |
| `reference_type` | `str` | string | no | free text: `rrp`, `previous_price`, `all_time_low`, `market_median` | `rrp` |
| `saving_aud` | `Decimal` | number | no | must equal `reference - current` within $0.01 | `100.00` |
| `saving_pct` | `Decimal` | number | no | must match recomputation within 0.01 | `4.76` |
| `effective_price_aud` | `Decimal \| None` | number | yes | `<= current_price_aud` | `1799.00` |
| `confidence` | `Decimal` | number | no | 0–1 | `1.0` |
| `evidence_url` | `HttpUrl` | string (URI) | no | source page | `https://www.apple.com/au/shop/buy-mac/macbook-air` |
| `captured_at` | `datetime` | string (date-time) | no | UTC | `2026-11-15T08:00:00Z` |
| `expires_at` | `datetime \| None` | string (date-time) | yes | must be later than `captured_at` | `2026-11-30T13:59:00Z` |
| `verification_status` | `VerificationStatus` | string | no | enum | `verified` |
| `rule_version` | `str` | string | no | non-empty; the rule set that fired | `2026-11-01` |

Validators (`Deal._check_saving`): the reference price may not be below the
current price (a rise is not a deal); `saving_aud` and `saving_pct` are
recomputed; `effective_price_aud` may not exceed the current price; an expiry
must be after the capture.

`reference_type` is deliberately a free string, not an enum: the deal engine
chooses which baseline it compares against, and the value is displayed beside
the saving.

## MatchCandidate

A proposed link between a raw seller listing and a catalogue product. Lives in
`data/matching/review_queue.json`.

| Field | Python type | JSON type | Null | Constraints and validators | Example |
|---|---|---|---|---|---|
| `candidate_id` | `str` | string | no | non-empty, unique | `cand-jbhifi-20261115-001` |
| `seller_id` | `str` | string | no | must exist in sellers | `jbhifi` |
| `raw_title` | `str` | string | no | exactly as the source wrote it | `MacBook Air 13 inch M5 512GB Midnight` |
| `raw_price_aud` | `Decimal` | number | no | 2dp | `1899.00` |
| `proposed_product_id` | `str \| None` | string | yes | required for an approved candidate | `apple-macbook-air-13-inch-m4-16gb-512gb-midnight` |
| `method` | `MatchMethod` | string | no | enum | `fuzzy` |
| `confidence` | `Decimal` | number | no | 0–1 | `0.72` |
| `config_diff` | `list[str]` | array of string | no | default `[]`; human-readable differences | `["storage: 512GB == 512GB"]` |
| `status` | `MatchStatus` | string | no | default `pending`; enum | `pending` |
| `source_hash` | `str` | string | no | `^sha256:[0-9a-f]{64}$` | `sha256:…` |
| `captured_at` | `datetime` | string (date-time) | no | UTC | `2026-11-15T08:00:00Z` |
| `decided_by` | `str \| None` | string | yes | required for a decided status | `apple-director` |
| `decided_at` | `datetime \| None` | string (date-time) | yes | `>= captured_at` | `2026-11-16T01:00:00Z` |

Validators (`MatchCandidate._check_decision`):

- `approved`, `rejected` and `auto_published` require both `decided_by` and
  `decided_at`.
- `pending` must carry neither.
- An approved or auto-published candidate must name `proposed_product_id`.
- `auto_published` requires `confidence >= 0.85` **and** `method != fuzzy`. A
  fuzzy match always needs a human.
- `decided_at` may not precede `captured_at`.

`config_diff` is a list of strings, not structured objects. The reviewer reads
it; no code branches on its shape.

### Review queue and decisions

There is one file: `data/matching/review_queue.json`. A decision is stored on
the candidate (`status`, `decided_by`, `decided_at`). There is no separate
`overrides.json`, and the validator does not re-apply decisions. A candidate is
rejected or approved in place, and the record keeps the `source_hash` it was
decided against.

## RunManifest and RunError

One manifest per adapter run, written even when the run partly failed.

| Field | Python type | JSON type | Null | Constraints and validators | Example |
|---|---|---|---|---|---|
| `run_id` | `str` | string | no | non-empty, unique | `run-20261115T0800` |
| `adapter` | `str` | string | no | module or source name | `scripts.collectors.apple_au` |
| `started_at` | `datetime` | string (date-time) | no | UTC | `2026-11-15T08:00:00Z` |
| `finished_at` | `datetime` | string (date-time) | no | `>= started_at` | `2026-11-15T08:04:12Z` |
| `fetched` | `int` | integer | no | `>= 0` | `42` |
| `parsed` | `int` | integer | no | `>= 0`; `<= fetched` | `40` |
| `matched` | `int` | integer | no | `>= 0`; `<= parsed` | `38` |
| `rejected` | `int` | integer | no | `>= 0` | `2` |
| `blocked_by_policy` | `int` | integer | no | `>= 0` | `0` |
| `errors` | `list[RunError]` | array of object | no | default `[]` | `[]` |

`RunError`:

| Field | Python type | JSON type | Null | Constraints and validators | Example |
|---|---|---|---|---|---|
| `product_id` | `str \| None` | string | yes | — | `apple-iphone-16-128gb-black` |
| `url` | `HttpUrl \| None` | string (URI) | yes | page that failed | `https://www.apple.com/au/shop/buy-iphone/iphone-16` |
| `message` | `str` | string | no | non-empty | `Price node missing` |
| `error_type` | `str` | string | no | non-empty; e.g. `ParseError` | `ParseError` |

## PurchaseRoute

One way of buying a product, ranked. Routes are embedded in a product context;
the model carries no `product_id` of its own.

| Field | Python type | JSON type | Null | Constraints and validators | Example |
|---|---|---|---|---|---|
| `route_type` | `PurchaseRouteType` | string | no | enum | `apple_education` |
| `label` | `str` | string | no | non-empty, display copy | `Apple Education Store` |
| `effective_price_aud` | `Decimal` | number | no | 2dp, `>= 0` | `1889.10` |
| `saving_vs_rrp_aud` | `Decimal` | number | no | 2dp; **signed**, may be negative | `209.90` |
| `requires` | `list[str]` | array of string | no | default `[]`; eligibility requirements | `["Tertiary student or teacher"]` |
| `warnings` | `list[str]` | array of string | no | default `[]` | `["Verification required at checkout"]` |
| `confidence` | `Decimal` | number | no | 0–1 | `0.99` |
| `source_urls` | `list[HttpUrl]` | array of string (URI) | no | at least one | `["https://www.apple.com/au-edu/shop"]` |
| `rank` | `int \| None` | integer | yes | `>= 1` when set | `1` |

`saving_vs_rrp_aud` is `SignedAUD`: it allows a negative value because a route
can cost more than the RRP (for example a bundle or a financed purchase). The
validator does not force it positive.

---

## Freshness policy

The policy is executable in `scripts/validation/freshness.py` and configured in
`config/freshness.yml`. It is applied by the validation CLI; the site renders the
result.

Config shape:

```yaml
default_max_age_hours: 36
by_seller_type:
  apple_direct: 48
  national_reseller: 36
  specialist_reseller: 48
  online_marketplace: 24
  gift_card_seller: 24
  business_reseller: 0
  education_reseller: 0
by_category:
  accessory: 72
  applecare: 72
review_queue_max_age_hours: 24
```

| Key | Type | Meaning |
|---|---|---|
| `default_max_age_hours` | int | fallback budget |
| `by_seller_type` | map | budget per `SellerType` |
| `by_category` | map | budget per product `Category`; highest precedence |
| `review_queue_max_age_hours` | int | age at which a pending candidate raises a warning |

Threshold precedence, highest first: `by_category`, `by_seller_type`,
`default_max_age_hours`, then the record's own `freshness_max_age_hours`.

Verdict rules (`effective_verification`):

1. A record stored as `unverified` stays `unverified` at any age.
2. A `captured_at` in the future is clamped to age zero and flagged as clock
   skew, so a bad clock cannot make a price look stale.
3. A threshold of `0` means no expiry; the stored verdict is returned unchanged.
4. Within the budget, the stored verdict is returned unchanged.
5. Beyond the budget, the verdict becomes `indicative` and `stale` is true.

`curated_unverified` and `unverified` are stored verdicts; age does not promote
them. The site's "indicative" label renders both the `indicative` verdict and the
`unverified` one.

---

## SQLite target DDL

The JSON maps to a relational backend without shape changes. This DDL is the
target for the migration path, not a Phase 1 deliverable. It is generated from
the documented fields, not from the brief, and is executed in tests of this
document.

Mapping rules:

- `*_aud` → `*_cents INTEGER` (`round(Decimal * 100)`).
- `assumed_point_value_aud` → `assumed_point_value_micros INTEGER`, because cents
  cannot express $0.005.
- Percentages → `REAL` (derived values, two decimal places, never used to
  compute money).
- Booleans → `INTEGER` with `CHECK (x IN (0,1))`.
- Enums → `TEXT` with the exact `CHECK` list.
- Timestamps → `TEXT` ISO 8601 UTC; lexical order is chronological.
- Derived amounts (`saving_aud`, `saving_pct`) → generated columns, so the
  database cannot disagree with itself.

Which nesting becomes a child table:

| JSON path | Becomes | Why |
|---|---|---|
| `Product.configuration` | `product_configuration` | free-form map, queried by the matcher |
| `Seller.supported_regions` | `seller_regions` | filtered by region |
| `Seller.categories` | `seller_categories` | filtered by category |
| `Seller.trust_score_inputs` | `trust_score_inputs_json` | heterogeneous scalar map; displayed, not joined |
| `Offer.pickup_locations` | `pickup_locations_json` | display only |
| `Offer.bundle_contents` | `bundle_contents_json` | display only |
| `EducationOffer.eligibility` | `eligibility_json` | free-form strings |
| `GiftCardOffer.risks` | `risks_json` | free-form warnings |
| `MatchCandidate.config_diff` | `config_diff_json` | free-form strings |
| `RunManifest.errors` | `run_errors` | one row per failure, joined by health |
| `PurchaseRoute.requires` / `warnings` / `source_urls` | `*_json` | display and audit only |

Rule: a list of free-form strings stays JSON; a list of controlled vocabulary
that gets filtered or joined becomes a child table.

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE sellers (
    seller_id             TEXT PRIMARY KEY,
    display_name          TEXT NOT NULL,
    seller_type           TEXT NOT NULL CHECK (seller_type IN (
                              'apple_direct','national_reseller','specialist_reseller',
                              'online_marketplace','telco','business_reseller',
                              'education_reseller','gift_card_seller')),
    website               TEXT,
    product_search_url    TEXT,
    country               TEXT NOT NULL CHECK (length(country) = 2),
    collection_method     TEXT NOT NULL CHECK (collection_method IN (
                              'automated_html','automated_jsonld','automated_api',
                              'curated','manual')),
    rate_limit_rps_float  REAL NOT NULL CHECK (rate_limit_rps_float BETWEEN 0 AND 20),
    crawl_delay_seconds   REAL NOT NULL CHECK (crawl_delay_seconds BETWEEN 0 AND 120),
    authorised_reseller   INTEGER CHECK (authorised_reseller IN (0,1)),
    authorised_evidence_url TEXT,
    warranty_policy_url   TEXT,
    returns_policy_url    TEXT,
    delivery_policy_url   TEXT,
    trust_score_inputs_json TEXT NOT NULL DEFAULT '{}'
                              CHECK (json_valid(trust_score_inputs_json)),
    adapter_module        TEXT NOT NULL DEFAULT '',
    enabled               INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    enabled_reason        TEXT,
    quote_only            INTEGER NOT NULL DEFAULT 0 CHECK (quote_only IN (0,1)),
    last_success_at       TEXT,
    last_failure_at       TEXT,
    notes                 TEXT NOT NULL DEFAULT '',
    CHECK (authorised_reseller IS NOT 1 OR authorised_evidence_url IS NOT NULL),
    CHECK (enabled = 1 OR length(coalesce(enabled_reason, '')) > 0),
    CHECK (enabled = 0 OR (website IS NOT NULL AND product_search_url IS NOT NULL)),
    CHECK (collection_method NOT IN ('automated_html','automated_jsonld','automated_api')
           OR (length(adapter_module) > 0 AND rate_limit_rps_float > 0))
);

CREATE TABLE seller_regions (
    seller_id TEXT NOT NULL REFERENCES sellers(seller_id),
    region    TEXT NOT NULL,
    PRIMARY KEY (seller_id, region)
);

CREATE TABLE seller_categories (
    seller_id TEXT NOT NULL REFERENCES sellers(seller_id),
    category  TEXT NOT NULL CHECK (category IN (
                  'mac','ipad','iphone','watch','airpods','tv','homepod',
                  'accessory','applecare','business','education','gift_card','cashback')),
    PRIMARY KEY (seller_id, category)
);

CREATE TABLE products (
    product_id          TEXT PRIMARY KEY,
    apple_sku           TEXT,
    gtin                TEXT CHECK (gtin IS NULL
                            OR (gtin NOT GLOB '*[^0-9]*'
                                AND (length(gtin) = 8 OR length(gtin) BETWEEN 12 AND 14))),
    category            TEXT NOT NULL CHECK (category IN (
                            'mac','ipad','iphone','watch','airpods','tv','homepod',
                            'accessory','applecare')),
    family              TEXT NOT NULL,
    name                TEXT NOT NULL,
    brand               TEXT NOT NULL DEFAULT 'Apple' CHECK (brand = 'Apple'),
    model               TEXT NOT NULL,
    apple_au_url        TEXT NOT NULL,
    apple_rrp_cents     INTEGER CHECK (apple_rrp_cents >= 0),
    education_eligible  INTEGER NOT NULL DEFAULT 0 CHECK (education_eligible IN (0,1)),
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN
                            ('active','discontinued','superseded')),
    priority            TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('high','normal')),
    specs_source_url    TEXT,
    rrp_verification    TEXT NOT NULL DEFAULT 'unverified' CHECK (rrp_verification IN
                            ('verified','unverified')),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    CHECK (rrp_verification <> 'verified'
           OR (apple_rrp_cents IS NOT NULL AND specs_source_url IS NOT NULL)),
    CHECK (updated_at >= created_at)
);

CREATE TABLE product_configuration (
    product_id TEXT NOT NULL REFERENCES products(product_id),
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    PRIMARY KEY (product_id, key)
);

CREATE TABLE offers (
    offer_id              TEXT PRIMARY KEY,
    product_id            TEXT NOT NULL REFERENCES products(product_id),
    seller_id             TEXT NOT NULL REFERENCES sellers(seller_id),
    url                   TEXT NOT NULL,
    price_cents           INTEGER NOT NULL CHECK (price_cents >= 0),
    was_price_cents       INTEGER CHECK (was_price_cents >= 0),
    rrp_cents             INTEGER CHECK (rrp_cents >= 0),
    price_basis           TEXT NOT NULL CHECK (price_basis IN
                              ('advertised','member','bundle','clearance','quote','edu','epp')),
    in_stock              INTEGER NOT NULL CHECK (in_stock IN (0,1)),
    stock_status          TEXT NOT NULL DEFAULT 'unknown' CHECK (stock_status IN
                              ('in_stock','low_stock','out_of_stock','preorder','unknown')),
    delivery_cost_cents   INTEGER NOT NULL DEFAULT 0 CHECK (delivery_cost_cents >= 0),
    delivery_cost_verified INTEGER NOT NULL DEFAULT 0 CHECK (delivery_cost_verified IN (0,1)),
    delivery_estimate     TEXT NOT NULL DEFAULT '',
    pickup_available      INTEGER NOT NULL DEFAULT 0 CHECK (pickup_available IN (0,1)),
    pickup_locations_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(pickup_locations_json)),
    promotion             TEXT,
    promotion_type        TEXT CHECK (promotion_type IS NULL OR promotion_type IN
                              ('sale','clearance','bundle','member','gift_card','cashback')),
    bundle                INTEGER NOT NULL DEFAULT 0 CHECK (bundle IN (0,1)),
    bundle_contents_json  TEXT NOT NULL DEFAULT '[]'
                              CHECK (json_valid(bundle_contents_json)),
    confidence            REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    match_method          TEXT NOT NULL CHECK (match_method IN
                              ('apple_sku','gtin','config','fuzzy','manual')),
    match_status          TEXT NOT NULL DEFAULT 'pending' CHECK (match_status IN
                              ('pending','approved','rejected','auto_published')),
    verification_status   TEXT NOT NULL CHECK (verification_status IN
                              ('verified','indicative','curated_unverified','unverified')),
    freshness_max_age_hours INTEGER NOT NULL DEFAULT 24
                              CHECK (freshness_max_age_hours BETWEEN 1 AND 720),
    captured_at           TEXT NOT NULL,
    source_hash           TEXT NOT NULL
                              CHECK (length(source_hash) = 71 AND source_hash GLOB 'sha256:*'),
    run_id                TEXT NOT NULL,
    grey_import           INTEGER NOT NULL DEFAULT 0 CHECK (grey_import IN (0,1)),
    sold_by               TEXT,
    CHECK (was_price_cents IS NULL OR was_price_cents >= price_cents),
    CHECK (stock_status NOT IN ('in_stock','low_stock','preorder') OR in_stock = 1),
    CHECK (stock_status <> 'out_of_stock' OR in_stock = 0),
    CHECK (promotion_type IS NULL OR length(coalesce(promotion, '')) > 0),
    CHECK (promotion_type IS NULL OR promotion_type <> 'bundle' OR bundle = 1),
    CHECK (bundle = 0 OR json_array_length(bundle_contents_json) > 0),
    CHECK (match_status <> 'auto_published' OR confidence >= 0.85)
);

CREATE TABLE education_offers (
    offer_id              TEXT PRIMARY KEY,
    product_id            TEXT NOT NULL REFERENCES products(product_id),
    seller_id             TEXT NOT NULL REFERENCES sellers(seller_id),
    url                   TEXT NOT NULL,
    price_cents           INTEGER NOT NULL CHECK (price_cents >= 0),
    standard_price_cents  INTEGER NOT NULL CHECK (standard_price_cents >= 0),
    saving_cents          INTEGER GENERATED ALWAYS AS
                              (standard_price_cents - price_cents) STORED,
    saving_pct            REAL GENERATED ALWAYS AS
                              (ROUND((standard_price_cents - price_cents) * 100.0
                                     / standard_price_cents, 2)) STORED,
    eligibility_json      TEXT NOT NULL CHECK (json_valid(eligibility_json)),
    verification          TEXT NOT NULL,
    annual_limit_summary  TEXT NOT NULL,
    annual_limit_source_url TEXT NOT NULL,
    in_stock              INTEGER NOT NULL CHECK (in_stock IN (0,1)),
    price_basis           TEXT NOT NULL DEFAULT 'edu' CHECK (price_basis = 'edu'),
    verification_status   TEXT NOT NULL CHECK (verification_status IN
                              ('verified','indicative','curated_unverified','unverified')),
    freshness_max_age_hours INTEGER NOT NULL DEFAULT 24
                              CHECK (freshness_max_age_hours BETWEEN 1 AND 720),
    captured_at           TEXT NOT NULL,
    source_hash           TEXT NOT NULL
                              CHECK (length(source_hash) = 71 AND source_hash GLOB 'sha256:*'),
    run_id                TEXT NOT NULL,
    CHECK (standard_price_cents >= price_cents),
    CHECK (json_array_length(eligibility_json) > 0)
);

CREATE TABLE gift_card_offers (
    offer_id              TEXT PRIMARY KEY,
    card_type             TEXT NOT NULL CHECK (card_type IN
                              ('apple_gift_card','retailer_gift_card')),
    seller_id             TEXT NOT NULL REFERENCES sellers(seller_id),
    loyalty_program       TEXT NOT NULL,
    channel               TEXT NOT NULL CHECK (channel IN ('in_store','online','both')),
    face_value_cents      INTEGER NOT NULL CHECK (face_value_cents > 0),
    purchase_price_cents  INTEGER NOT NULL CHECK (purchase_price_cents >= 0),
    bonus_points_per_dollar REAL NOT NULL DEFAULT 0 CHECK (bonus_points_per_dollar >= 0),
    points_earned         REAL NOT NULL DEFAULT 0 CHECK (points_earned >= 0),
    assumed_point_value_micros INTEGER NOT NULL DEFAULT 5000
                              CHECK (assumed_point_value_micros >= 0),
    effective_value_cents INTEGER NOT NULL CHECK (effective_value_cents >= 0),
    effective_discount_pct REAL GENERATED ALWAYS AS
                              (ROUND((face_value_cents - effective_value_cents) * 100.0
                                     / face_value_cents, 2)) STORED,
    daily_limit_cards     INTEGER CHECK (daily_limit_cards >= 0),
    starts_at             TEXT NOT NULL,
    ends_at               TEXT NOT NULL,
    source_url            TEXT NOT NULL,
    verification_status   TEXT NOT NULL CHECK (verification_status IN
                              ('verified','indicative','curated_unverified','unverified')),
    evidence_snapshot     TEXT,
    terms_url             TEXT,
    risks_json            TEXT NOT NULL CHECK (json_valid(risks_json)),
    CHECK (purchase_price_cents <= face_value_cents),
    CHECK (effective_value_cents <= face_value_cents),
    CHECK (ends_at > starts_at),
    CHECK (verification_status <> 'verified' OR evidence_snapshot IS NOT NULL),
    CHECK (json_array_length(risks_json) > 0)
);

CREATE TABLE deals (
    deal_id               TEXT PRIMARY KEY,
    deal_type             TEXT NOT NULL CHECK (deal_type IN
                              ('price_drop','rrp_discount','all_time_low','education',
                               'gift_card','retailer_gift_card','cashback','bundle',
                               'clearance','restock')),
    product_id            TEXT NOT NULL REFERENCES products(product_id),
    seller_id             TEXT NOT NULL REFERENCES sellers(seller_id),
    offer_id              TEXT,
    current_price_cents   INTEGER NOT NULL CHECK (current_price_cents >= 0),
    reference_price_cents INTEGER NOT NULL CHECK (reference_price_cents >= 0),
    reference_type        TEXT NOT NULL,
    saving_cents          INTEGER GENERATED ALWAYS AS
                              (reference_price_cents - current_price_cents) STORED,
    saving_pct            REAL GENERATED ALWAYS AS
                              (ROUND((reference_price_cents - current_price_cents) * 100.0
                                     / reference_price_cents, 2)) STORED,
    effective_price_cents INTEGER CHECK (effective_price_cents >= 0),
    confidence            REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence_url          TEXT NOT NULL,
    captured_at           TEXT NOT NULL,
    expires_at            TEXT,
    verification_status   TEXT NOT NULL CHECK (verification_status IN
                              ('verified','indicative','curated_unverified','unverified')),
    rule_version          TEXT NOT NULL,
    CHECK (reference_price_cents >= current_price_cents),
    CHECK (effective_price_cents IS NULL OR effective_price_cents <= current_price_cents),
    CHECK (expires_at IS NULL OR expires_at > captured_at)
);

CREATE TABLE match_candidates (
    candidate_id       TEXT PRIMARY KEY,
    seller_id          TEXT NOT NULL REFERENCES sellers(seller_id),
    raw_title          TEXT NOT NULL,
    raw_price_cents    INTEGER NOT NULL CHECK (raw_price_cents >= 0),
    proposed_product_id TEXT REFERENCES products(product_id),
    method             TEXT NOT NULL CHECK (method IN
                           ('apple_sku','gtin','config','fuzzy','manual')),
    confidence         REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    config_diff_json   TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(config_diff_json)),
    status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                           ('pending','approved','rejected','auto_published')),
    source_hash        TEXT NOT NULL
                           CHECK (length(source_hash) = 71 AND source_hash GLOB 'sha256:*'),
    captured_at        TEXT NOT NULL,
    decided_by         TEXT,
    decided_at         TEXT,
    CHECK (status = 'pending'
           OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)),
    CHECK (status <> 'pending' OR (decided_by IS NULL AND decided_at IS NULL)),
    CHECK (decided_at IS NULL OR decided_at >= captured_at),
    CHECK (status NOT IN ('approved','auto_published') OR proposed_product_id IS NOT NULL),
    CHECK (status <> 'auto_published' OR (confidence >= 0.85 AND method <> 'fuzzy'))
);

CREATE TABLE run_manifests (
    run_id            TEXT PRIMARY KEY,
    adapter           TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT NOT NULL,
    fetched           INTEGER NOT NULL CHECK (fetched >= 0),
    parsed            INTEGER NOT NULL CHECK (parsed >= 0),
    matched           INTEGER NOT NULL CHECK (matched >= 0),
    rejected          INTEGER NOT NULL CHECK (rejected >= 0),
    blocked_by_policy INTEGER NOT NULL CHECK (blocked_by_policy >= 0),
    CHECK (finished_at >= started_at),
    CHECK (parsed <= fetched),
    CHECK (matched <= parsed)
);

CREATE TABLE run_errors (
    run_id     TEXT NOT NULL REFERENCES run_manifests(run_id),
    sequence   INTEGER NOT NULL,
    product_id TEXT,
    url        TEXT,
    message    TEXT NOT NULL,
    error_type TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);

CREATE TABLE purchase_routes (
    product_id            TEXT NOT NULL REFERENCES products(product_id),
    route_type            TEXT NOT NULL CHECK (route_type IN
                              ('apple_retail','apple_education','epp','business_quote',
                               'gift_card','cashback')),
    label                 TEXT NOT NULL,
    effective_price_cents INTEGER NOT NULL CHECK (effective_price_cents >= 0),
    saving_vs_rrp_cents   INTEGER NOT NULL,
    requires_json         TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(requires_json)),
    warnings_json         TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(warnings_json)),
    confidence            REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    source_urls_json      TEXT NOT NULL CHECK (json_valid(source_urls_json)),
    rank                  INTEGER CHECK (rank >= 1),
    PRIMARY KEY (product_id, route_type),
    CHECK (json_array_length(source_urls_json) > 0)
);
```

DDL notes:

- `offers.run_id` and `run_manifests.run_id` are joined by value; the FK is not
  declared because the manifests dataset is loaded independently and may arrive
  after offers in a rebuild. The validator enforces the link.
- `deals.offer_id` may point at a retail, education or gift-card offer, so no
  single-table FK is possible. The validator checks membership across all three.
- `purchase_routes` adds `product_id` to own the rows; the JSON model omits it
  because routes are embedded in a product document.
- Education and gift-card averages (`saving_*`, `effective_discount_pct`) are
  generated columns, so a hand-edited percentage cannot survive.

## Deviations from the brief

Where the implementation and the original brief disagree, this table records the
implementation and why. There is no third version.

| Brief said | Implemented | Why |
|---|---|---|
| Money as two-decimal JSON strings (`"1899.00"`) | `Decimal` internally, **JSON number** in files | canonical dataset examples and `Intl` formatting; Decimal prevents float error in arithmetic |
| `price_basis`: `rrp`, `retail`, `sale`, `clearance`, `education`, `business_quote`, `epp`, `refurbished`, `bundle` | `advertised`, `member`, `bundle`, `clearance`, `quote`, `edu`, `epp` | `advertised` covers ordinary and sale prices; `quote` and `edu` are distinct commercial routes; `refurbished` is a seller/source property |
| `verification_status`: `verified`, `unverified`, `stale` | `verified`, `indicative`, `curated_unverified`, `unverified` | `stale` is computed by freshness into `indicative`; `curated_unverified` distinguishes a human-entered price from a machine capture |
| Freshness only in `config/freshness.yml`, keyed by seller type | per-record `freshness_max_age_hours` (default 24) **and** config, with category → seller type → default → record precedence | an adapter can state its own budget; accessories legitimately outlive phone prices |
| `rrp_verification` may be `stale`; evidence fields `rrp_evidence_url`/`rrp_captured_at` | `RrpVerdict` is `verified`/`unverified`; evidence is `specs_source_url` | a stale RRP is a display decision, and the capture time is the product `updated_at` |
| `collection_method: quote_only` | seller boolean `quote_only`; methods are `automated_*`, `curated`, `manual` | quote-only is orthogonal to how a public page would be read |
| `grey_import_risk` enum on sellers | `Offer.grey_import` boolean, plus `trust_score_inputs` | the risk attaches to a listing, not to every product from a seller |
| `sold_by` as a seller reference | free string on `Offer` | marketplace sellers are not registry entities and must not be treated as such |
| `run_id` as an embedded manifest reference | string on `Offer`/`EducationOffer`, manifests in `runs.json` | avoids duplicating the manifest in every offer; the validator joins them |
| `Deal` owns the offer and has `first_detected_at`, `last_seen_at`, `is_current`, `rule_id` | `Deal.offer_id` is an optional link; lifecycle fields absent; `rule_version` only | a deal is recomputed each run and a stale claim is filtered by `expires_at` and `verification_status` |
| `condition` (`new`/`refurbished`/`used`) on offers | absent | exclusion is a source and matching policy, not a price field; carrying it invites publishing used stock as new |
| `MatchCandidate.config_diff` structured objects; separate `overrides.json` | `list[str]`; decisions stored on the candidate | the reviewer reads the diff; the decision and its `source_hash` live with the record |
| `GiftCardOffer` carries `captured_at`, `source_hash`, `run_id` | `evidence_snapshot` path plus `starts_at`/`ends_at` | the snapshot file holds the capture evidence; the promotion window is the meaningful period |
| Category `apple_tv`, `display` | `tv`, no `display`; `applecare` added | matches Apple AU navigation and the catalogue's actual range |
| Seller types `authorised_reseller`, `marketplace`, `department_store`, `education`, `business`, `gift_card_program`, `cashback_platform`, `supermarket`, `carrier` | `national_reseller`, `specialist_reseller`, `online_marketplace`, `telco`, `business_reseller`, `education_reseller`, `gift_card_seller`, `apple_direct` | describes the commercial shape in the Australian market without a type per retailer |
| `stock_status` alone | `in_stock` boolean plus `stock_status` | the boolean is the cheap filter; the enum is the reason |
| `was_price_aud` strictly greater than the current price | `>=` | a "was" price that equals the current price is a source artefact, not a lie to correct |
| `PurchaseRoute` has `product_id` and `route_id` | no id; `product_id` is the owning document | routes are embedded per product and identified by `(product_id, route_type)` |
| Seller rate limit named `rate_limit_rps` | model/schema `rate_limit_rps_float`, alias `rate_limit_rps` | the float suffix records the unit; the alias keeps committed data readable |

Two further implementation notes that are not deviations but surprise readers:

- The JSON Schema property for the seller rate limit is
  `rate_limit_rps_float`; `data/sellers.json` writes `rate_limit_rps`. Both
  validate through the alias.
- Money serialises through `float`, so whole dollars appear as `2099.0`. The
  value is exact to the cent.

## Change protocol

1. Change the model in `scripts/models/`, not the schema.
2. Run `uv run python -m scripts.models.export_schema` and commit the schema
   diff.
3. Update this document in the same change. If a field is renamed or an enum
   value is removed, that is a data migration and a `schema_version` note.
4. Run `uv run python -m scripts.validation.validate_data` — a hard failure
   blocks the change.
5. Run `uv run pytest -q`; model and freshness tests cover the invariants above.