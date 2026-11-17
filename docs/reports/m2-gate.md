# M2 review gate — Apple AU and Apple Education

**Gate run:** 2026-11-15, re-verified 2026-11-17
**Reviewer:** director · **Cards:** APA-011 … APA-016
**Evidence:** `data/snapshots/2026-11-16/`, `data/runs/`

## Method

The gate is deliberately independent of the collector. A throwaway script
re-fetched a fixed sample of snapshot offers, parsed the JSON-LD itself with its
own regular expression and `json.loads`, and compared the price with the captured
value. A parsing bug in the adapter cannot validate itself.

- Sample: 5 offers drawn with a fixed seed (`random.Random(20261115)`) so the
  gate is reproducible.
- User agent: `OrchardPriceBot/1.0` — the honest identity, not a browser string.
- Crawl delay: 3 seconds between requests, matching `data/sellers.json`.
- Tolerance: a price is correct only if it differs from the capture by less than
  half a cent.
- A fetch failure is recorded as a fetch failure, never as a price mismatch.

## Results

| Product | Live | Captured | Verdict |
|---|---:|---:|---|
| iPhone 18 Pro 6.3-inch 256GB Black | 2,099.00 | 2,099.00 | match |
| AirPods Max 2 — Midnight | 999.00 | 999.00 | match |
| MacBook Pro with PROLED XDR 16-inch M5 Pro | 5,499.00 | 5,499.00 | match |
| Apple Watch Series 12 42mm Space Grey Aluminium GPS | 649.00 | 649.00 | match |
| AirPods 5 with Wireless Charging Case | 249.00 | 249.00 | match |

**5 of 5 match.**

Two products (iPhone Air, Apple Watch Ultra 4) timed out on the first pass: the
Apple product pages are 300–480 KB and the gate script's 30-second read limit was
too short. Both were re-fetched with a 90-second limit and matched their captured
prices exactly — A$1,899 and A$1,349. This is a limitation of the gate script,
not of the data.

## Coverage

- Apple AU: **45 of 45** catalogue products captured, all from JSON-LD, every
  price equal to the catalogue RRP. No drift.
- Stock: 6 products report `in_stock`, 39 report `unknown`. Apple's JSON-LD
  carries no `availability` field for most configurations. Absence of a stock
  statement is not evidence of stock, and the site must render `unknown` rather
  than `in stock` until a stock source exists.
- Apple Education: **18 offers**, savings from A$30 (iPad 11-inch, 4.01%) to
  A$350 (Mac Studio, 8.14%). UNiDAYS and the annual purchase limits are quoted
  from Apple's own sales-policy page, with the source URL recorded.

## The education finding, verified rather than assumed

The plan flagged as an open assumption that education prices are publicly
visible. They are: `/au-edu/shop/...` returns 200 and the price sits in the
Product JSON-LD with no sign-in. UNiDAYS gates the *purchase*, not the display.
Independently confirmed on the live iMac page (A$2,319.00).

A correction to the project brief: its education example showed a $160 saving on
a $2,099 product as **7.63%**. That is 7.62%. The implementation computes it
with half-up rounding and a test asserts the value.

## Defects found and fixed during this milestone

1. **A partial run could become the day's evidence.** The `--limit` guard only
   refused to *overwrite* an existing day file, so on a fresh date a two-product
   smoke test wrote `apple_education_au.json` for that day and a consumer would
   have read 2 of 18 offers as the complete education set. A limited run now
   never writes under the day's name. Test:
   `test_limited_cli_run_never_creates_a_days_evidence`.
2. **`EducationOffer.in_stock` was a plain boolean** and the collector set it to
   `False` for "not observed". That would have rendered as "Out of stock" on all
   18 education products. Now tri-state, with `None` meaning not observed.
3. **The data gate could not see run manifests**, because it looked for a single
   `runs.json` while manifests are written one file per run. The loader now reads
   the directory and names a bad manifest by its own filename.
4. **Six tests hard-coded the catalogue size.** They failed with a bare
   `FileNotFoundError` naming a fixture. They now derive expectations from the
   data and name the missing products.

## Verdict

**PASS.** Apple AU and the Apple Education Store are collectable under their own
robots.txt with an honest user agent; captured prices match the live site; the
education prices are public. M2 is closed. Publishing these offers to the site
arrives with APA-028 in M3.
