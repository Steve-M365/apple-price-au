# M2 review gate — Apple AU and Apple Education

**Date:** 2026-11-15 (gate run) · **Reviewer:** director
**Card:** APA-016 · **Snapshots:** `data/snapshots/2026-11-15/`

## Method

The gate is deliberately independent of the collector. A small throwaway script
fetched five snapshot offers again from Apple AU, parsed the JSON-LD itself with
its own regular expression and `json.loads`, and compared the price against the
captured value. If the adapter had a parsing bug, it could not validate itself.

- Sample: 5 offers drawn with a fixed seed (`random.Random(20261115)`) so the
  gate is reproducible.
- User agent: `OrchardPriceBot/1.0` — the honest identity, not a browser string.
- Crawl delay: 3 seconds between requests, matching `data/sellers.json`.
- Tolerance: a price is correct only if it differs from the capture by less than
  half a cent.

## Results

| Product | Live | Captured | Status |
|---|---:|---:|---|
| iPhone 18 Pro 6.3-inch 256GB Black | 2,099.00 | 2,099.00 | match |
| AirPods 5 with Wireless Charging Case | 249.00 | 249.00 | match |
| Magic Keyboard for iPad Air 11-inch (M4) — US English — White | 449.00 | 449.00 | match |
| iPad 11-inch A16 128GB Blue | 749.00 | 749.00 | match |
| Mac mini M6 16GB 256GB | 1,449.00 | 1,449.00 | match |

**5 of 5 match. Zero fetch failures.**

## What the gate also confirmed

- The collector's full run produced **20 of 20** offers with `price_aud` equal to
  the catalogue RRP — no drift between the seed catalogue and the live pages.
- The iPad is the interesting case: its JSON-LD name is simply
  `iPad Wi-Fi 128GB — Blue`, with no screen size and no chip. That is why the
  normaliser carries catalogue-anchored defaults for seven ids. The gate shows
  the default is doing real work rather than papering over a bad match.

## Known limitations carried forward

1. **Stock is largely unknown.** 18 of 20 Apple AU offers report
   `stock_status: unknown` because Apple's JSON-LD carries no
   `availability` field for most configurations. Two report `in_stock`. This is
   accurate — absence of a stock statement is not evidence of stock — but it
   means the site must render "unknown" rather than "in stock" until a stock
   source exists.
2. **Education stock is not observed** either. `EducationOffer.in_stock` is
   `None` for all 8 offers; the education page carries no per-configuration
   stock statement.
3. **Education prices use a 168-hour freshness window** (7 days). Education
   pricing changes with product generations, not sales cycles, but a month-old
   price (the original 720-hour value) was too long to call current.
4. **Nothing is published yet.** The snapshots are evidence; the site reads
   published feeds, which arrive with APA-028 in M3.

## Verdict

**PASS.** Apple AU and the Apple Education Store are collectable under their own
robots.txt with an honest user agent, the captured prices match the live site,
and the education prices are publicly visible without authentication. M2 may
close once the catalogue expansion lands.