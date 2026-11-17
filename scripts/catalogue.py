"""Generate the Apple AU catalogue from Apple's own storefront pages.

The catalogue is not hand-curated: this module discovers the live Apple AU
product families from the storefront sitemaps, selects one entry configuration
per variant (screen size or watch case size, Wi-Fi/GPS baseline), fetches each
product page and reads the same JSON-LD ``Product`` block the collector reads.
Rows are produced by :func:`scripts.normalise.normalise`, so the catalogue and
the collector can never disagree about what a product is.

Selection is deterministic
--------------------------
Family and configuration URLs come from the sitemaps, not from markup order, so
the same Apple range always yields the same targets. Within one variant the
pick is the configuration with the smallest memory, then the smallest storage,
then the lowest chip tier, then the smallest CPU core count, then standard
glass/stand, then the preferred colour (a colour the catalogue already carries
for that family and size, otherwise Apple's registry order), then the URL.
Running the generator twice therefore produces byte-identical
``data/products.json`` while the range is unchanged: timestamps only move when a
row's facts move.

Evidence
--------
Every row has a ``data/evidence/apple_au/<product_id>.json`` file in the same
shape as the original seed evidence, extended with the page's raw availability
token. An unchanged capture is left alone rather than rewritten, so a rerun is a
no-op on disk as well as in the catalogue.

Nothing is invented
-------------------
A page that does not parse, publishes a non-AUD price, or normalises to a
different identity is skipped and recorded in
``data/evidence/apple_au/_expansion_report.json`` with its URL and the reason.
Catalogue rows whose page can no longer be read are preserved, never deleted;
Apple products that have left the range are marked ``superseded`` by a human, not
by this generator.

Usage::

    python -m scripts.catalogue            # fetch, verify and rewrite
    python -m scripts.catalogue --dry-run  # select and report, fetch nothing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from scripts.collectors.apple_au import CapturedPage, ParseError, parse_page
from scripts.collectors.base import SELLERS_REGISTRY, RateLimiter, utc_now
from scripts.collectors.http import HttpClient, HttpError, load_settings
from scripts.collectors.policy import RobotsGate
from scripts.normalise import NormalisationError, normalise, slugify

__all__ = [
    "EVIDENCE_DIR",
    "PRODUCTS_PATH",
    "build_row",
    "discover_targets",
    "main",
    "select_families",
    "write_catalogue",
]

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
PRODUCTS_PATH: Final = REPO_ROOT / "data" / "products.json"
EVIDENCE_DIR: Final = REPO_ROOT / "data" / "evidence" / "apple_au"
REPORT_PATH: Final = EVIDENCE_DIR / "_expansion_report.json"
SUMMARY_PATH: Final = EVIDENCE_DIR / "_summary.json"
CATALOGUE_REGISTRY: Final = Path(__file__).with_name("normalise_data") / "catalogue.json"

SELLER_ID: Final = "apple_au"

#: Apple's AU storefront sitemaps. ``buy`` lists every configurable product page,
#: ``watchpdp`` the Watch case/band bundles and ``product`` the accessory pages.
SITEMAPS: Final[tuple[str, ...]] = (
    "https://www.apple.com/au/shop/sitemaps/buy.xml",
    "https://www.apple.com/au/shop/sitemaps/watchpdp.xml",
    "https://www.apple.com/au/shop/sitemaps/product.xml",
)

#: Apple families this generator carries: the URL family pages whose titles the
#: normaliser can name. Anything else on the storefront is left alone.
FAMILY_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "buy-mac/imac",
        "buy-mac/mac-mini",
        "buy-mac/mac-studio",
        "buy-mac/macbook-air",
        "buy-mac/macbook-neo",
        "buy-mac/macbook-pro",
        "buy-mac/macbook-pro-proled-xdr",
        "buy-ipad/ipad",
        "buy-ipad/ipad-air",
        "buy-ipad/ipad-mini",
        "buy-ipad/ipad-pro",
        "buy-iphone/iphone-16",
        "buy-iphone/iphone-17",
        "buy-iphone/iphone-17e",
        "buy-iphone/iphone-18-pro",
        "buy-iphone/iphone-air",
        "buy-iphone/iphone-duo",
        "buy-watch/apple-watch",
        "buy-watch/apple-watch-se",
        "buy-watch/apple-watch-ultra",
        "buy-watch/apple-watch-hermes",
        "buy-airpods/airpods-5",
        "buy-airpods/airpods-max-2",
        "buy-airpods/airpods-pro-3",
        "buy-tv/apple-tv-4k",
        "buy-homepod/homepod",
        "buy-homepod/homepod-mini",
    }
)

#: Apple accessory product pages worth carrying, selected from ``product.xml``.
#: Each entry is (slug fragment, preferred suffix); the first matching URL wins.
ACCESSORY_PRODUCTS: Final[tuple[tuple[str, str], ...]] = (
    ("/magic-mouse-usb", "white-multi-touch-surface"),
    ("/magic-trackpad-usb", "white-multi-touch-surface"),
    ("/magic-keyboard-for-ipad-air-13", "us-english-white"),
    ("/siri-remote", "/siri-remote"),
)

_SIZE_INCH: Final = re.compile(r"(?P<size>\d+(?:\.\d+)?)-inch")
_SIZE_MM: Final = re.compile(r"(?<!\d)(?P<size>\d{2})-?mm")
_MEMORY: Final = re.compile(r"(\d+)gb-memory")
_STORAGE: Final = re.compile(r"(\d+)(gb|tb)(?!-memory)(?=-|$)")
_CORES: Final = re.compile(r"(\d+)-core-cpu")
_LOC: Final = re.compile(r"<loc>(.*?)</loc>")


class CatalogueError(RuntimeError):
    """The generator cannot proceed safely; nothing is written."""


def _relative(url: str) -> str:
    """Return the path below ``/au/shop/`` for one Apple AU product URL."""
    path = urlsplit(url).path
    marker = "/au/shop/"
    if marker not in path:
        raise CatalogueError(f"{url!r} is not an Apple AU shop URL")
    return path.split(marker, 1)[1]


def _family_of(url: str) -> str:
    parts = _relative(url).split("/")
    return "/".join(parts[:2])


def _tail(url: str) -> str:
    return _relative(url).rsplit("/", 1)[-1]


def _bytes(text: str, pattern: re.Pattern[str]) -> int:
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


def _storage(text: str) -> int:
    match = _STORAGE.search(text)
    if match is None:
        return 0
    size = int(match.group(1))
    return size * 1024 if match.group(2) == "tb" else size


def _chip_rank(tail: str) -> int:
    if "ultra" in tail:
        return 3
    if "max" in tail:
        return 2
    if "pro" in tail:
        return 1
    return 0


def _display_penalty(tail: str) -> int:
    penalty = 0
    if "nano-texture" in tail:
        penalty += 100
    if "vesa" in tail:
        penalty += 50
    return penalty


def _variant(family: str, url: str) -> tuple[str, str]:
    """The axes that make one entry configuration a distinct product."""
    tail = _tail(url)
    if family == "buy-airpods/airpods-5":
        return tail, ""  # with or without the wireless charging case
    size = ""
    match = _SIZE_INCH.search(tail)
    if match is not None:
        size = f"{match.group('size')}-inch"
    else:
        match = _SIZE_MM.search(tail)
        if match is not None:
            size = f"{match.group('size')}mm"
    connectivity = ""
    if "gps-cellular" in tail:
        connectivity = "gps-cellular"
    elif "cellular" in tail:
        connectivity = "cellular"
    elif "gps" in tail:
        connectivity = "gps"
    return size, connectivity


def _colour_aliases() -> tuple[list[str], dict[str, int]]:
    payload = json.loads(CATALOGUE_REGISTRY.read_text(encoding="utf-8"))
    slugs: list[str] = []
    order: dict[str, int] = {}
    for index, entry in enumerate(payload["colours"]):
        for alias in entry["aliases"]:
            slug = slugify(alias)
            if slug and slug not in order:
                slugs.append(slug)
                order[slug] = index
    return sorted(slugs, key=len, reverse=True), order


def _colour_in(tail: str, aliases: Sequence[str]) -> str:
    for alias in aliases:
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", tail):
            return alias
    return ""


def select_families(locs: Iterable[str]) -> dict[str, list[str]]:
    """Group sitemap URLs into (family -> configuration URLs), excluding the rest."""
    families: dict[str, list[str]] = defaultdict(list)
    for url in locs:
        family = _family_of(url)
        if family not in FAMILY_ALLOWLIST:
            continue
        if _relative(url).count("/") < 2:
            continue  # the family landing page, not a configuration
        families[family].append(url)
    return dict(families)


def _existing_colour_prefs(
    products: Iterable[Mapping[str, Any]],
) -> tuple[
    dict[tuple[str, str], set[str]],
    dict[str, set[str]],
    dict[tuple[str, str], set[str]],
    dict[str, set[str]],
]:
    """Colour and Watch-band preferences, by (family, size) and by family."""
    colour: dict[tuple[str, str], set[str]] = defaultdict(set)
    colour_family: dict[str, set[str]] = defaultdict(set)
    band: dict[tuple[str, str], set[str]] = defaultdict(set)
    band_family: dict[str, set[str]] = defaultdict(set)
    for product in products:
        url = product.get("apple_au_url")
        configuration = product.get("configuration")
        if not isinstance(url, str) or not isinstance(configuration, Mapping):
            continue
        family = _family_of(url)
        size = str(configuration.get("screen_size") or configuration.get("size") or "")
        value = configuration.get("colour")
        if isinstance(value, str) and value:
            colour[(family, size)].add(slugify(value))
            colour_family[family].add(slugify(value))
        watch_band = configuration.get("band")
        if isinstance(watch_band, str) and watch_band:
            band[(family, size)].add(slugify(watch_band))
            band_family[family].add(slugify(watch_band))
    return colour, colour_family, band, band_family


def _eligible(family: str, url: str) -> bool:
    tail = _tail(url)
    if family.startswith("buy-ipad") and "cellular" in tail:
        return False
    if family in {"buy-watch/apple-watch", "buy-watch/apple-watch-se"} and "cellular" in tail:
        return False
    if (
        family.startswith(
            ("buy-mac/macbook", "buy-mac/imac", "buy-mac/mac-mini", "buy-mac/mac-studio")
        )
        and _STORAGE.search(tail) is None
    ):
        return False
    if family.startswith("buy-ipad") and _storage(tail) == 0:
        return False
    return not (family.startswith("buy-iphone") and _storage(tail) == 0)


def _within_variant_key(
    url: str,
    *,
    aliases: Sequence[str],
    order: Mapping[str, int],
    preferred_colours: set[str],
    preferred_bands: set[str],
    catalogue_urls: frozenset[str],
) -> tuple[int, int, int, int, int, int, int, str]:
    """Order one variant's configurations from the entry model upwards."""
    tail = _tail(url)
    colour = _colour_in(tail, aliases)
    preferred = 0 if colour in preferred_colours else 1
    band = 0 if any(f"-{slug}-" in f"-{tail}" for slug in preferred_bands) else 1
    known = 0 if url in catalogue_urls else 1
    return (
        _bytes(tail, _MEMORY),
        _storage(tail),
        _chip_rank(tail),
        _bytes(tail, _CORES),
        _display_penalty(tail),
        band * 100000 + preferred * 1000 + order.get(colour, 999),
        known,
        url,
    )


def discover_targets(
    families: Mapping[str, list[str]],
    products: Iterable[Mapping[str, Any]],
    *,
    accessory_urls: Iterable[str] = (),
    catalogue_urls: frozenset[str] = frozenset(),
) -> list[str]:
    """Pick the entry configuration for every variant, plus the accessories."""
    aliases, order = _colour_aliases()
    colour_prefs, colour_by_family, band_prefs, band_by_family = _existing_colour_prefs(products)
    targets: list[str] = []
    for family, urls in sorted(families.items()):
        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
        for url in sorted(set(urls)):
            if _eligible(family, url):
                groups[_variant(family, url)].append(url)
        for key, group in sorted(groups.items()):
            size = key[0]
            preferred_colours = colour_prefs.get((family, size)) or colour_by_family.get(
                family, set()
            )
            preferred_bands = band_prefs.get((family, size)) or band_by_family.get(family, set())
            targets.append(
                min(
                    group,
                    key=lambda candidate: _within_variant_key(
                        candidate,
                        aliases=aliases,
                        order=order,
                        preferred_colours=preferred_colours,
                        preferred_bands=preferred_bands,
                        catalogue_urls=catalogue_urls,
                    ),
                )
            )
    targets.extend(sorted(set(accessory_urls)))
    return targets


def _accessory_urls(locs: Iterable[str]) -> list[str]:
    selected: list[str] = []
    for fragment, suffix in ACCESSORY_PRODUCTS:
        matches = sorted(url for url in locs if fragment in url and url.endswith(suffix))
        if matches:
            selected.append(matches[0])
    return selected


def _price(evidence_price: Decimal) -> float:
    return float(evidence_price.quantize(Decimal("0.01")))


def _rendered(price: Decimal) -> str:
    return f"A${price.quantize(Decimal('0.01')):,.2f}"


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _education_eligible(category: str, family: str) -> bool:
    return category in {"mac", "ipad"} or family == "applecare"


def build_row(
    evidence_title: str,
    *,
    url: str,
    price: Decimal,
    sku: str | None,
    known: Mapping[str, str],
    created_at: str,
    priority: str = "normal",
) -> dict[str, Any]:
    """Normalise one live page read into a catalogue row."""
    identity = normalise(evidence_title, catalogue=known)
    return {
        "product_id": identity.product_id,
        "apple_sku": sku,
        "gtin": None,
        "category": identity.category,
        "family": identity.family,
        "name": identity.name,
        "brand": "Apple",
        "model": identity.model,
        "configuration": dict(identity.configuration),
        "apple_au_url": url,
        "apple_rrp_aud": _price(price),
        "rrp_verification": "verified",
        "education_eligible": _education_eligible(identity.category, identity.family),
        "status": "active",
        "priority": priority,
        "specs_source_url": url,
        "created_at": created_at,
        "updated_at": created_at,
    }


def _evidence_payload(
    product_id: str,
    *,
    url: str,
    final_url: str,
    fetched_at: str,
    user_agent: str,
    evidence: Any,
) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "url": url,
        "final_url": final_url,
        "http_status": 200,
        "fetched_at": fetched_at,
        "user_agent": user_agent,
        "extraction_method": evidence.parse_path,
        "price_rendered": _rendered(evidence.price_aud),
        "price_numeric": _price(evidence.price_aud),
        "currency": evidence.currency,
        "page_sku": evidence.sku,
        "raw_fragment": evidence.source_fragment,
        "availability": evidence.availability,
        "note": (
            "price taken from the Product JSON-LD Offer.price on the product page; "
            f"availability as published: {evidence.availability or 'not stated'}"
        ),
    }


#: Fields whose change means the evidence file must be rewritten. A new capture
#: timestamp alone is not a change of fact.
_EVIDENCE_FACTS: Final = (
    "url",
    "final_url",
    "http_status",
    "extraction_method",
    "price_numeric",
    "currency",
    "page_sku",
    "raw_fragment",
    "availability",
)


def _write_if_changed(path: Path, payload: Mapping[str, Any]) -> bool:
    text = json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def _evidence_changed(path: Path, payload: Mapping[str, Any]) -> bool:
    if not path.is_file():
        return True
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    if not isinstance(existing, dict):
        return True
    return any(existing.get(field) != payload.get(field) for field in _EVIDENCE_FACTS)


def write_catalogue(products: Sequence[Mapping[str, Any]], *, generated_at: str) -> None:
    """Write ``data/products.json`` sorted by category then product id."""
    ordered = sorted(products, key=lambda row: (str(row["category"]), str(row["product_id"])))
    payload = {
        "products": list(ordered),
        "generated_at": generated_at,
        "source_note": (
            "Apple AU RRPs (AUD, inc GST) captured from the individual product page "
            "JSON-LD Product offer on apple.com/au. Every verified price has a matching "
            "data/evidence/apple_au/<product_id>.json record with the source URL, capture "
            "timestamp, HTTP status and the JSON-LD fragment it came from. Zeros/null "
            "prices are never inferred."
        ),
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if PRODUCTS_PATH.is_file() and PRODUCTS_PATH.read_text(encoding="utf-8") == text:
        return
    PRODUCTS_PATH.write_text(text, encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _collect(
    targets: Sequence[str],
    existing: Sequence[Mapping[str, Any]],
    *,
    dry_run: bool,
) -> int:
    settings = load_settings()
    limiter = RateLimiter.from_registry(SELLERS_REGISTRY)
    gate = RobotsGate(user_agent=settings.user_agent)
    existing_urls = {
        str(product.get("apple_au_url")): product
        for product in existing
        if isinstance(product.get("apple_au_url"), str)
    }
    unique = list(dict.fromkeys([*targets, *existing_urls]))
    if dry_run:
        print(f"{SELLER_ID}: {len(unique)} page(s) would be fetched, nothing fetched")
        for url in unique:
            print(f"  {url}")
        return 0

    denied = [url for url in unique if not gate.is_allowed(url).allowed]
    if denied:
        raise CatalogueError(
            "robots.txt denies " + ", ".join(denied) + "; refusing to fetch any product page"
        )

    rows: dict[str, dict[str, Any]] = {
        str(product["product_id"]): dict(product) for product in existing
    }
    known: dict[str, str] = {product_id: str(row["name"]) for product_id, row in rows.items()}
    skipped: list[dict[str, str]] = []
    price_changes: list[dict[str, Any]] = []
    fetched_at_values: list[str] = []
    fetched = 0

    with HttpClient(settings) as http:
        for url in unique:
            limiter.acquire(SELLER_ID)
            try:
                response = http.get(url)
            except HttpError as exc:
                skipped.append({"url": url, "reason": f"{type(exc).__name__}: {exc}"})
                continue
            fetched += 1
            fetched_at = _stamp(response.fetched_at)
            fetched_at_values.append(fetched_at)
            if response.status != 200:
                skipped.append({"url": url, "reason": f"HTTP {response.status}"})
                continue
            page = CapturedPage(
                url=response.url,
                html=response.text,
                fetched_at=response.fetched_at,
                http_status=response.status,
            )
            try:
                evidence = parse_page(page)
            except ParseError as exc:
                skipped.append({"url": url, "reason": f"parse_error: {exc.reason}"})
                continue

            # A bundle identifier (``+``-joined) is not one part number; the
            # catalogue records the page's bundle string in evidence only.
            sku = evidence.sku if evidence.sku and "+" not in evidence.sku else None

            try:
                row = build_row(
                    evidence.title,
                    url=url,
                    price=evidence.price_aud,
                    sku=sku,
                    known=known,
                    created_at=fetched_at,
                )
            except NormalisationError as exc:
                skipped.append({"url": url, "reason": f"normalise: {exc}"})
                continue

            product_id = row["product_id"]
            current = rows.get(product_id)
            if current is not None and str(current.get("apple_au_url")) != url:
                # Two different Apple URLs normalising to one product_id means
                # the id scheme cannot tell the configurations apart (for
                # example an 8-core and a 10-core GPU Air). The catalogue keeps
                # the page it already tracks rather than letting the two
                # overwrite each other's price.
                skipped.append(
                    {
                        "url": url,
                        "reason": (
                            f"duplicate identity: {product_id} is already tracked at "
                            f"{current.get('apple_au_url')}"
                        ),
                    }
                )
                continue
            if current is not None:
                facts = (
                    "name",
                    "configuration",
                    "apple_sku",
                    "category",
                    "family",
                    "model",
                    "education_eligible",
                    "apple_rrp_aud",
                )
                # Unchanged: keep the row, its evidence and its timestamps.
                if not all(current.get(field) == row.get(field) for field in facts):
                    if current.get("apple_rrp_aud") != row["apple_rrp_aud"]:
                        price_changes.append(
                            {
                                "product_id": product_id,
                                "from": current.get("apple_rrp_aud"),
                                "to": row["apple_rrp_aud"],
                                "url": url,
                            }
                        )
                    # The live page's facts win: name, configuration and part
                    # number may have moved with the price. Only the row's
                    # origin timestamps and priority are the catalogue's own.
                    refreshed = dict(row)
                    refreshed["created_at"] = current.get("created_at", fetched_at)
                    refreshed["updated_at"] = fetched_at
                    refreshed["priority"] = current.get("priority", "normal")
                    refreshed["status"] = current.get("status", "active")
                    current.clear()
                    current.update(refreshed)
                    known[product_id] = str(current["name"])
            else:
                rows[product_id] = row
                known[product_id] = row["name"]

            # Evidence is reconciled from the row's own page every time; the
            # writer is a no-op when the facts (not the clock) are unchanged.
            evidence_path = EVIDENCE_DIR / f"{product_id}.json"
            payload = _evidence_payload(
                product_id,
                url=url,
                final_url=response.url,
                fetched_at=fetched_at,
                user_agent=settings.user_agent,
                evidence=evidence,
            )
            if _evidence_changed(evidence_path, payload):
                _write_if_changed(evidence_path, payload)

    stamp = max(fetched_at_values) if fetched_at_values else _stamp(utc_now())
    previous = _load_json(PRODUCTS_PATH)
    previous_rows = previous.get("products")
    ordered_rows = sorted(
        rows.values(), key=lambda row: (str(row["category"]), str(row["product_id"]))
    )
    unchanged = previous.get("generated_at") and previous_rows == ordered_rows
    generated_at = str(previous["generated_at"]) if unchanged else stamp
    write_catalogue(list(rows.values()), generated_at=generated_at)

    counts = Counter(str(row["category"]) for row in rows.values())
    report = {
        "generated_at": generated_at,
        "seller_id": SELLER_ID,
        "pages_fetched": fetched,
        "catalogue_size": len(rows),
        "categories": dict(sorted(counts.items())),
        "skipped": skipped,
        "price_changes": price_changes,
    }
    _write_if_changed(REPORT_PATH, report)

    verified = sum(
        1
        for row in rows.values()
        if row.get("rrp_verification") == "verified" and row.get("apple_au_url")
    )
    summary = {
        "source": "apple.com/au product pages",
        "fetched_at": generated_at,
        "fetched": len(unique),
        "verified": verified,
        "unverified": len(rows) - verified,
        "urls_attempted": [str(row["apple_au_url"]) for row in rows.values()],
        "blockers": [entry["reason"] for entry in skipped],
        "method": (
            "Each product page was fetched with the project's identifying user agent at "
            "the apple_au crawl delay. The price is read from the server-rendered JSON-LD "
            "Product offers[].price (priceCurrency AUD). Only the JSON-LD offer fragment "
            "is stored, never the page."
        ),
    }
    _write_if_changed(SUMMARY_PATH, summary)

    print(f"{SELLER_ID}: {len(rows)} products, {fetched} page(s) fetched")
    print("  categories: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    print(f"  skipped: {len(skipped)}, price changes: {len(price_changes)}")
    for entry in skipped:
        print(f"  SKIP {entry['url']}: {entry['reason']}")
    for change in price_changes:
        print(f"  PRICE {change['product_id']}: {change['from']} -> {change['to']}")
    return 0


_MIN_SITEMAP_LOCS: Final = 500
_SITEMAP_CACHE: Final = REPO_ROOT / ".cache" / "http" / "sitemaps"


def _read_sitemap(
    http: HttpClient, limiter: RateLimiter, url: str, *, required: bool = True
) -> str:
    """Read one sitemap, falling back to the last complete copy taken.

    Apple's edge occasionally answers a sitemap with a handful of URLs—or an
    empty ``204``—instead of the full set, especially after a burst of requests.
    Accepting that would silently shrink the catalogue, so a short body is
    retried uncached; if every live read is short the last complete copy is used
    and the fallback is reported. A run with no complete copy stops rather than
    guessing.
    """
    name = url.rsplit("/", 1)[-1]
    cached = _SITEMAP_CACHE / name
    best = ""
    best_count = 0
    for attempt in range(2):
        limiter.acquire(SELLER_ID)
        try:
            body = http.get(url, use_cache=attempt == 0).text
        except HttpError as exc:
            print(f"  sitemap {name}: {type(exc).__name__}", file=sys.stderr)
            continue
        count = len(_LOC.findall(body))
        if count > best_count:
            best, best_count = body, count
        if best_count >= _MIN_SITEMAP_LOCS:
            _SITEMAP_CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_text(body, encoding="utf-8")
            return best

    if cached.is_file():
        previous = cached.read_text(encoding="utf-8")
        if len(_LOC.findall(previous)) >= _MIN_SITEMAP_LOCS:
            print(
                f"  sitemap {name}: live read returned {best_count} URL(s); using the "
                "last complete copy",
                file=sys.stderr,
            )
            return previous
    if not required:
        print(f"  sitemap {name}: unavailable; accessory extras skipped", file=sys.stderr)
        return ""
    raise CatalogueError(
        f"{url} returned only {best_count} URL(s) and no complete copy is cached; "
        "refusing to generate a catalogue from a truncated sitemap"
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.catalogue",
        description="Generate data/products.json and Apple AU evidence from live pages.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="select and list targets without fetching or writing anything",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    catalogue = _load_json(PRODUCTS_PATH)
    existing = catalogue.get("products")
    if not isinstance(existing, list):
        print(f"{PRODUCTS_PATH} has no products list", file=sys.stderr)
        return 1

    settings = load_settings()
    limiter = RateLimiter.from_registry(SELLERS_REGISTRY)
    try:
        with HttpClient(settings) as http:
            bodies = [
                _read_sitemap(http, limiter, sitemap, required=not sitemap.endswith("product.xml"))
                for sitemap in SITEMAPS
            ]

        buy_locs: list[str] = []
        watch_locs: list[str] = []
        product_locs: list[str] = []
        for body, bucket in zip(bodies, (buy_locs, watch_locs, product_locs), strict=True):
            bucket.extend(_LOC.findall(body))

        families = select_families([*buy_locs, *watch_locs])
        if not families:
            raise CatalogueError("no product families were discovered from the sitemaps")
        targets = discover_targets(
            families,
            existing,
            accessory_urls=_accessory_urls(product_locs),
            catalogue_urls=frozenset(
                str(product["apple_au_url"])
                for product in existing
                if isinstance(product.get("apple_au_url"), str)
            ),
        )
        return _collect(targets, existing, dry_run=args.dry_run)
    except (CatalogueError, HttpError) as exc:
        print(f"catalogue: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
