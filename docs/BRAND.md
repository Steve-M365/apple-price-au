# Brand

**Status:** provisional working brand. Chosen to unblock design, copy and the
site scaffold. It is not a registered trademark, no trademark search has been
run, the domain is not secured, and it may change before launch. A rename is
designed to be one pass — see the checklist at the end.

## The brand

| Element | Value |
|---|---|
| Name | **Orchard** |
| Tagline | **Apple prices in Australia, actually checked.** |
| Short description | An Australia-only Apple price tracker with the evidence attached. |
| Voice | Plain, specific, unexcited. Numbers first, timestamp second, caveat third. |
| Audience | Australians buying Apple hardware who want to know what it actually costs today, and whether a "deal" is one. |
| Personality | A careful friend who reads the terms. Not a deals blog. Not a shop. |

The name is deliberately not an Apple reference. An orchard is where things are
grown and picked, which fits a tracker that gathers prices; it is short,
pronounceable, and has no product or retailer meaning in this market.

## Trademark position

This is a descriptive, nominative use of third-party marks, not a claim to them.

1. **The brand avoids the Apple mark.** "Orchard" contains no Apple word mark,
   no Apple imagery, no Apple logo, no Apple typeface and no Apple store
   styling. The intended domain is an Orchard domain, not an Apple-adjacent one.
2. **Apple marks are used descriptively only.** "Apple", "MacBook", "iPhone",
   "iPad", "Apple Watch", "AirPods", "Apple TV" and "HomePod" appear only to
   identify the products being priced. Product names are written as the
   manufacturer writes them, not as Orchard brands.
3. **No logo or trade dress.** Orchard does not use the Apple logo, the
   "Think different" line, Apple product photography as its own identity, or a
   look-alike store layout. Product imagery, where used, is attributed and never
   presented as Orchard's own.
4. **Non-affiliation is stated plainly and repeatedly.** The site footer, the
   About page and the README will state: *"Orchard is not affiliated with,
   endorsed by or sponsored by Apple Inc. Apple and the product names are
   trademarks of Apple Inc., used only to describe the products being tracked."*
5. **No implied endorsement.** No copy may suggest partnership, authorised
   reseller status, warranty provision or approval by Apple or a retailer.
6. **Reseller status is a fact about the retailer, not about Orchard.** Saying
   "JB Hi-Fi is an authorised reseller" is a sourced claim about JB Hi-Fi. It is
   never phrased as "Orchard is authorised".
7. **The repository slug is descriptive, and is the first rename candidate.**
   `apple-price-au` describes the subject matter; it is not the brand. If a
   concern is raised, the slug and Python package change without touching the
   product name.

This is a design position, not legal advice. Before launch, the disclaimers are
reviewed against Australian comparison-site expectations (`APA-053`).

## Naming rules for any future brand

- No Apple word mark, no "i" prefix, no "Mac" or "Pod" construction, no Apple
  product silhouette in a logo.
- Nothing that implies official status: "official", "authorised", "Apple store",
  "genuine", "approved".
- Nothing that promises an outcome: "cheapest", "guaranteed", "always lowest".
- Must be registrable and available as `.com.au` or `.au`, with the social handle
  free, before it is adopted.
- Must be pronounceable in one pass and spellable after hearing it once.

## Copy rules

These are enforceable in review, not aspirational.

| Rule | Example |
|---|---|
| State the observation time, not "now" | "Verified 15 Nov 2026, 9:04 am Melbourne" — not "today" |
| Stale prices say so | "Indicative — last checked 3 days ago" |
| Points are points | "2,000 Everyday Rewards points (worth about $10 at $0.005/point)" — never "$10 cash" |
| Education is separate | "Education price, eligibility required" — never folded into "best price" |
| No invented urgency | No countdowns, no "ends tonight" unless the source states an end |
| No marketing inflation | No "massive", "unbeatable", "huge savings" |
| Australian English | colour, catalogue, normalise, behaviour, organisation, licence (noun) |
| Show the source | Every price links to the page it came from |

## Rename checklist

A brand change is one pass. Work top to bottom; each row names the artefact and
the thing to change.

| # | Artefact | Location | Action |
|---|---|---|---|
| 1 | Repository slug | GitHub repo, local path, `.git/config` remote | Rename repo; update local path and remote |
| 2 | Python distribution name | `pyproject.toml` → `[project].name`, `description` | Change `apple-price-au` and the description |
| 3 | Python package path | `pyproject.toml` → `[tool.hatch.build.targets.wheel].packages` | Only if the import package is renamed; otherwise keep `scripts` |
| 4 | Lockfile | `uv.lock` | Regenerate with `uv lock` after step 2 |
| 5 | Package metadata | `.venv/.../apple_price_au-*.dist-info` | Rebuild with `uv sync`; never edit in place |
| 6 | Brand name and tagline | `README.md`, `AGENTS.md` | Replace name, tagline, description |
| 7 | Document set | `docs/*.md` | Replace brand name, tagline, non-affiliation wording |
| 8 | Board metadata | `.kanban/board.json` → `project`, card text | Update `project` string and any brand mentions; regenerate `board.md` |
| 9 | Site copy | `site/src/**` (layouts, pages, components) | Header, footer, titles, meta descriptions |
| 10 | Site config | `site/astro.config.mjs`, `site/package.json` | `site` URL, `name`, canonical base |
| 11 | Design tokens | `site/src/styles/` | Only if the palette carries the old name |
| 12 | JSON-LD publisher | Structured data in `site/src/` | `publisher.name`, `publisher.url`, `@id` |
| 13 | Canonical and sitemap | `site/astro.config.mjs`, `PUBLIC_SITE_URL` | New absolute URL |
| 14 | Domain | Registrar, DNS, GitHub Pages custom domain | Register, set `PUBLIC_SITE_URL`, update CNAME |
| 15 | User agent | `.env.example` → `HTTP_USER_AGENT` | New project URL and contact address |
| 16 | Alert identity | Discord webhook name/avatar, message footer | New name and site link |
| 17 | Social handle | Chosen platform(s) | Claim the same handle as the domain |
| 18 | Email | Contact address in docs and user agent | New alias |
| 19 | CI and workflow names | `.github/workflows/*.yml` | Cosmetic only; check badges and links |
| 20 | Licence and attribution text | `README.md`, site footer | Keep the MIT and trademark wording |

Verification after the pass:

```bash
grep -rn "apple-price-au\|Orchard" --exclude-dir=.git --exclude-dir=.venv \
  --exclude-dir=node_modules --exclude-dir=.ruff_cache --exclude-dir=.pytest_cache .
cd site && npm run build
uv run pytest -q
```

Then confirm the live site, the sitemap, the RSS feed and the JSON-LD all point
at the new domain, and that no page still carries the old tagline.

## Things that must not change in a rename

- The data model, identifiers and `seller_id` values.
- The non-affiliation statement's substance, even if the wording is refreshed.
- The trust labels: verified, indicative, points-as-points.
- Historical commit messages and snapshots. A rename does not rewrite evidence.