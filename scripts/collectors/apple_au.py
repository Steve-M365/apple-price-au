"""Apple AU collector: JSON-LD first, server-rendered markup second.

Apple's Australian storefront publishes a schema.org ``Product`` block in a
``<script type="application/ld+json">`` tag on every product page. That block is
the primary read: it carries the name, the AUD price, the part number and — on
some pages — the availability. A price is only ever taken from that block or,
when it is absent, from server-rendered markup that sits in the buy block
(``current_price`` / ``rf-pdp-currentprice`` elements, or the ``currentPrice``
entry in the page's own price configuration). Nothing else is trusted, and no
JavaScript is executed.

The rules this module enforces, in the order they bite:

1. **Policy first.** :meth:`AppleAuAdapter.collect` inherits the robots gate
   from :mod:`scripts.collectors.base`; no page is fetched until every catalogue
   URL has been cleared for this user agent. The recorded finding for
   ``apple.com`` is evidence for a past decision, not a permanent permission.
2. **Identity is confirmed, never assumed.** The captured title is put through
   :func:`scripts.normalise.normalise` and its ``product_id`` must equal the
   catalogue row's. A title that now resolves elsewhere is a
   :class:`TitleMismatchError` (the product was probably superseded), recorded as
   a ``CollectionError`` for that product. It is never silently re-mapped.
3. **No guessing.** If the buy block publishes several different prices and
   JSON-LD is missing, the page is refused rather than resolved by picking the
   first number. A currency other than AUD is refused the same way.
4. **Confidence is published only when earned.** A part number that matches the
   catalogue earns 1.0; a configuration match earns 0.95; a page whose part
   number contradicts the catalogue earns 0.80 and is returned as an error,
   because anything below 0.85 must not be published.
5. **Only what the page states.** ``was_price_aud`` is left unset unless the page
   publishes a previous price. The delivery cost is recorded (and marked
   verified) only when the page states a shipping rate; otherwise the model's
   own default is left in place, which means "not stated" rather than "free".

Failures are typed and per-product: a page that does not parse, no longer
matches, or quotes the wrong currency appends a ``CollectionError`` and the run
continues. A policy refusal is different — the base class turns it into a
``BlockedSource`` with no offers and no errors, because a refusal is a decision,
not a fault.

Usage::

    python -m scripts.collectors.apple_au --limit 3          # smoke run, no snapshot
    python -m scripts.collectors.apple_au --dry-run          # fetch nothing
    python -m scripts.collectors.apple_au --save-fixtures    # full run + fixtures
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar, Final, Literal, cast

from scripts.models import Offer, Product
from scripts.models.enums import (
    MatchMethod,
    MatchStatus,
    PriceBasis,
    ProductStatus,
    StockStatus,
    VerificationStatus,
)
from scripts.normalise import NormalisationError, normalise

from .base import (
    SELLERS_REGISTRY,
    BaseAdapter,
    Capabilities,
    CollectionError,
    CollectionResult,
    ConcurrencyGate,
    RateLimiter,
    utc_now,
)
from .http import HttpClient, HttpError, load_settings
from .policy import RobotsGate
from .snapshot import SnapshotConflictError, snapshot_path, write_snapshot

__all__ = [
    "CONFIDENCE_CONFIG",
    "CONFIDENCE_SKU",
    "PARSE_PATHS",
    "PUBLISH_FLOOR",
    "AppleAuAdapter",
    "AppleAuError",
    "CapturedPage",
    "LowConfidenceError",
    "ParseError",
    "PriceEvidence",
    "TitleMismatchError",
    "build_offer",
    "fixture_payload",
    "load_products",
    "main",
    "map_availability",
    "new_run_id",
    "offer_from_capture",
    "parse_page",
    "save_fixture",
    "write_snapshot_for",
]

SELLER_ID: Final = "apple_au"

#: Repository-relative defaults, resolved from this file so a run never depends
#: on the working directory.
REPO_ROOT: Final = Path(__file__).resolve().parents[2]
PRODUCTS_PATH: Final = REPO_ROOT / "data" / "products.json"
FIXTURES_DIR: Final = REPO_ROOT / "fixtures" / "html" / "apple_au"
SNAPSHOTS_DIR: Final = REPO_ROOT / "data" / "snapshots"

#: Which read produced the price. Recorded per product so a partial fallback
#: (JSON-LD gone, markup still fine) is visible in the run report.
ParsePath = Literal["jsonld", "html"]
PARSE_PATHS: Final[tuple[ParsePath, ...]] = ("jsonld", "html")

#: Confidence for a part-number match and for a configuration-only match.
CONFIDENCE_SKU: Final = Decimal("1.0")
CONFIDENCE_CONFIG: Final = Decimal("0.95")

#: A contradicting part number scores below every publishable threshold on
#: purpose: the catalogue and the page disagree, so a human must look.
CONFIDENCE_SKU_CONFLICT: Final = Decimal("0.80")

#: Below this an offer is returned as an error instead of being published.
PUBLISH_FLOOR: Final = Decimal("0.85")

#: Ceiling on the trimmed capture saved as a fixture, so every fixture stays a
#: reviewable fragment rather than a copy of the page.
FIXTURE_MAX_FRAGMENTS: Final = 40

_WHITESPACE: Final = re.compile(r"\s+")
_TITLE: Final = re.compile(r"<title[^>]*>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)
_TITLE_SUFFIX: Final = re.compile(r"\s*[-\u2013\u2014]\s*Apple\s*\(AU\)\s*$", re.IGNORECASE)
_MARKUP_PRICE: Final = re.compile(
    r'<(?:span|div)[^>]*class="[^"]*(?:current_price|currentprice)[^"]*"[^>]*>\s*'
    r"A\$\s?(?P<amount>[\d,]+\.\d{2})",
    re.IGNORECASE,
)
_CONFIG_PRICE: Final = re.compile(
    r'"currentPrice"\s*:\s*\{\s*"amount"\s*:\s*"A\$\s?(?P<amount>[\d,]+\.\d{2})"'
)


class AppleAuError(RuntimeError):
    """A typed, per-product failure that is recorded, never raised out of a run.

    ``error_type`` is the token written into the run's ``CollectionError`` so a
    health report can group failures without parsing messages.
    """

    error_type: ClassVar[str] = "apple_au_error"

    def __init__(
        self, reason: str, *, url: str | None = None, product_id: str | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.url = url
        self.product_id = product_id


class ParseError(AppleAuError):
    """The page could not be read as a price at all."""

    error_type: ClassVar[str] = "parse_error"


class TitleMismatchError(ParseError):
    """The page title no longer maps to the catalogue row.

    The product has most likely been superseded. Re-mapping on the fly would
    quietly move a price onto a different product, so this stops instead.
    """

    error_type: ClassVar[str] = "title_mismatch"

    def __init__(
        self,
        reason: str,
        *,
        published_title: str,
        catalogue_product_id: str,
        resolved_product_id: str | None,
        url: str | None = None,
        product_id: str | None = None,
    ) -> None:
        super().__init__(reason, url=url, product_id=product_id)
        self.published_title = published_title
        self.catalogue_product_id = catalogue_product_id
        self.resolved_product_id = resolved_product_id


class LowConfidenceError(ParseError):
    """The match scored below :data:`PUBLISH_FLOOR`, so it must not be published."""

    error_type: ClassVar[str] = "low_confidence"

    def __init__(self, reason: str, *, confidence: Decimal) -> None:
        super().__init__(reason)
        self.confidence = confidence


@dataclass(frozen=True, slots=True)
class PriceEvidence:
    """Everything one page says about one price, before it is judged.

    ``source_fragment`` is the exact slice of the capture that carries the
    price — the JSON-LD ``Product`` block, or the buy-block price element. Its
    SHA-256 becomes the offer's ``source_hash``, so the hash covers the evidence
    rather than the whole page.
    """

    title: str
    price_aud: Decimal
    currency: str
    availability: str | None
    sku: str | None
    url: str | None
    delivery_cost_aud: Decimal | None
    parse_path: ParsePath
    source_fragment: str

    @property
    def source_hash(self) -> str:
        """``sha256:<hex>`` of the captured price fragment."""
        digest = hashlib.sha256(self.source_fragment.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"


@dataclass(frozen=True, slots=True)
class CapturedPage:
    """One fetched product page, or the trimmed capture saved as a fixture."""

    url: str
    html: str
    fetched_at: datetime
    http_status: int = 200

    def __post_init__(self) -> None:
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(f"{self.url!r} is not an absolute http(s) URL")
        if self.fetched_at.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")

    @classmethod
    def from_fixture(cls, payload: Mapping[str, object]) -> CapturedPage:
        """Rebuild a capture from a saved fixture document."""
        url = payload.get("url")
        html = payload.get("html")
        captured_at = payload.get("captured_at")
        status = payload.get("http_status", 200)
        if not isinstance(url, str) or not isinstance(html, str):
            raise ValueError("a fixture needs string 'url' and 'html' fields")
        if not isinstance(captured_at, str):
            raise ValueError("a fixture needs a 'captured_at' timestamp")
        if not isinstance(status, int) or isinstance(status, bool):
            raise ValueError("a fixture 'http_status' must be an integer")
        parsed_at = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        return cls(url=url, html=html, fetched_at=parsed_at, http_status=status)


# --------------------------------------------------------------------------- #
# Reading the capture
# --------------------------------------------------------------------------- #


class _JsonLdScripts(HTMLParser):
    """Collect the text of every ``application/ld+json`` script in a document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.blocks: list[str] = []
        self._collecting = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        declared = dict(attrs).get("type") or ""
        if declared.strip().lower() == "application/ld+json":
            self._collecting = True
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._collecting:
            self.blocks.append("".join(self._buffer))
            self._buffer = []
            self._collecting = False

    def handle_data(self, data: str) -> None:
        if self._collecting:
            self._buffer.append(data)


def jsonld_blocks(html: str) -> tuple[str, ...]:
    """Return every JSON-LD script body in document order."""
    parser = _JsonLdScripts()
    parser.feed(html)
    parser.close()
    return tuple(parser.blocks)


def _decode(block: str) -> object | None:
    try:
        parsed: object = json.loads(block)
    except json.JSONDecodeError:
        return None
    return parsed


def _iter_products(payload: object) -> Iterator[Mapping[str, object]]:
    """Yield every schema.org ``Product`` object, including ``@graph`` members."""
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_products(item)
        return
    if not isinstance(payload, Mapping):
        return
    record = cast(Mapping[str, object], payload)
    declared = record.get("@type")
    kinds = (
        [declared]
        if isinstance(declared, str)
        else list(declared)
        if isinstance(declared, list)
        else []
    )
    if any(isinstance(kind, str) and kind.lower() == "product" for kind in kinds):
        yield record
    if "@graph" in record:
        yield from _iter_products(record["@graph"])


def _iter_mappings(value: object) -> Iterator[Mapping[str, object]]:
    if isinstance(value, Mapping):
        yield cast(Mapping[str, object], value)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, Mapping):
                yield cast(Mapping[str, object], item)


def _amount(value: object) -> Decimal | None:
    """Parse a price that may be a number or a formatted ``A$1,449.00`` string."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = value.replace("A$", "").replace("$", "").replace(",", "").strip()
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
    return None


def _currency(offer: Mapping[str, object]) -> str:
    raw = offer.get("priceCurrency")
    return raw.strip().upper() if isinstance(raw, str) else ""


def _shipping_cost(offer: Mapping[str, object]) -> Decimal | None:
    """Return the stated shipping rate, but only when the page quotes it in AUD."""
    for detail in _iter_mappings(offer.get("shippingDetails")):
        rate = detail.get("shippingRate")
        if not isinstance(rate, Mapping):
            continue
        rate_record = cast(Mapping[str, object], rate)
        quoted = rate_record.get("currency")
        if isinstance(quoted, str) and quoted.strip().upper() not in {"", "AUD"}:
            continue
        value = _amount(rate_record.get("value"))
        if value is not None:
            return value
    return None


def parse_jsonld_price(blocks: Sequence[str], *, url: str) -> PriceEvidence | None:
    """Read the first priced ``Product`` offer from the page's JSON-LD.

    Returns ``None`` when no block carries a price, which lets the caller fall
    back to the markup. A block that quotes a non-AUD currency is a refusal, not
    a ``None``: converting a foreign price would be inventing one.
    """
    for block in blocks:
        for product in _iter_products(_decode(block)):
            name = product.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            for offer in _iter_mappings(product.get("offers")):
                price = _amount(offer.get("price"))
                if price is None or price <= 0:
                    continue
                currency = _currency(offer)
                if currency and currency != "AUD":
                    raise ParseError(
                        f"page quotes {currency} for {name!r}; only AUD prices are supported",
                        url=url,
                    )
                sku = offer.get("sku")
                availability = offer.get("availability")
                declared_url = offer.get("url") or product.get("url")
                return PriceEvidence(
                    title=name,
                    price_aud=price,
                    currency=currency or "AUD",
                    availability=availability if isinstance(availability, str) else None,
                    sku=sku.strip() if isinstance(sku, str) and sku.strip() else None,
                    url=declared_url if isinstance(declared_url, str) else None,
                    delivery_cost_aud=_shipping_cost(offer),
                    parse_path="jsonld",
                    source_fragment=block,
                )
    return None


def _distinct_prices(html: str, pattern: re.Pattern[str]) -> dict[str, str]:
    """Map each distinct amount to the first fragment that published it."""
    found: dict[str, str] = {}
    for match in pattern.finditer(html):
        found.setdefault(match.group("amount"), match.group(0))
    return found


def parse_html_price(html: str, *, url: str) -> PriceEvidence | None:
    """Read the price from server-rendered buy-block markup.

    The buy block is preferred over the page's price configuration. Either way
    every distinct amount is collected first: if more than one is on offer the
    page is refused, because choosing one would be inventing a price for a
    variant we cannot identify from markup alone.
    """
    for pattern, label in (
        (_MARKUP_PRICE, "buy-block markup"),
        (_CONFIG_PRICE, "price configuration"),
    ):
        candidates = _distinct_prices(html, pattern)
        if not candidates:
            continue
        if len(candidates) > 1:
            quoted = ", ".join(f"A${amount}" for amount in sorted(candidates))
            raise ParseError(
                f"{label} publishes {len(candidates)} different prices ({quoted}); "
                "refusing to guess which variant this page prices",
                url=url,
            )
        amount, fragment = next(iter(candidates.items()))
        return PriceEvidence(
            title="",
            price_aud=Decimal(amount.replace(",", "")),
            currency="AUD",
            availability=None,
            sku=None,
            url=None,
            delivery_cost_aud=None,
            parse_path="html",
            source_fragment=fragment,
        )
    return None


def page_title(html: str) -> str:
    """Return the product title a page publishes, without Apple's chrome.

    Apple writes ``Buy <product> - Apple (AU)``; the prefix and suffix are site
    furniture, and stripping them leaves the same string JSON-LD publishes.
    """
    match = _TITLE.search(html)
    if match is None:
        return ""
    text = unescape(re.sub(r"<[^>]+>", "", match.group("title")))
    text = _WHITESPACE.sub(" ", text).strip()
    if text.lower().startswith("buy "):
        text = text[4:]
    return _TITLE_SUFFIX.sub("", text).strip()


def parse_page(page: CapturedPage) -> PriceEvidence:
    """Read one capture, JSON-LD first and markup second.

    Raises :class:`ParseError` when neither path yields an AUD price, or when
    the markup path cannot disambiguate. The title is taken from JSON-LD when it
    is available, because that is the string the catalogue was built from.
    """
    blocks = jsonld_blocks(page.html)
    evidence = parse_jsonld_price(blocks, url=page.url)
    if evidence is None:
        evidence = parse_html_price(page.html, url=page.url)
        if evidence is not None:
            title = page_title(page.html)
            if not title:
                raise ParseError(
                    "no JSON-LD price and no page title; identity cannot be confirmed",
                    url=page.url,
                )
            evidence = PriceEvidence(
                title=title,
                price_aud=evidence.price_aud,
                currency=evidence.currency,
                availability=evidence.availability,
                sku=evidence.sku,
                url=evidence.url,
                delivery_cost_aud=evidence.delivery_cost_aud,
                parse_path=evidence.parse_path,
                source_fragment=evidence.source_fragment,
            )
    if evidence is None:
        raise ParseError(
            "no JSON-LD Product price and no A$ price in the buy block",
            url=page.url,
        )
    return evidence


# --------------------------------------------------------------------------- #
# Turning evidence into a publishable offer
# --------------------------------------------------------------------------- #


def map_availability(value: str | None) -> StockStatus:
    """Map a schema.org availability value onto Orchard's vocabulary.

    An absent or unrecognised token is ``unknown``. A page that says nothing
    about stock has not told us the item is in stock, and guessing ``in_stock``
    from a priced page is exactly the invention the project forbids.
    """
    if not value:
        return StockStatus.UNKNOWN
    token = value.strip().rstrip("/").rsplit("/", 1)[-1]
    token = re.sub(r"[^a-z]", "", token.lower())
    if token in {"instock", "onlineonly", "limitedavailability"}:
        return StockStatus.IN_STOCK
    if token in {"outofstock", "soldout", "discontinued"}:
        return StockStatus.OUT_OF_STOCK
    if token in {"preorder", "presale"}:
        return StockStatus.PREORDER
    return StockStatus.UNKNOWN


def _published_part_numbers(sku: str | None) -> list[str]:
    """Split a published part number into its components.

    Apple encodes bundles as parts joined by ``+`` — a Watch page publishes
    ``Z0YQ+MJFN4X/A+MKTC4FE/A`` for case plus band — so the catalogue's
    ``MJFN4X/A`` is a match when it is one of the components.
    """
    if not sku:
        return []
    return [part.strip() for part in sku.split("+") if part.strip()]


def match_confidence(product: Product, evidence: PriceEvidence) -> tuple[Decimal, MatchMethod]:
    """Score the page-to-catalogue match and say how it was reached.

    Raises :class:`LowConfidenceError` when the page publishes a part number
    that contradicts the catalogue: the title agrees but the SKU does not, which
    means the page is pricing a different bundle than the row describes.
    """
    published = _published_part_numbers(evidence.sku)
    catalogue_sku = (product.apple_sku or "").strip()
    if published and catalogue_sku:
        if catalogue_sku in published:
            return CONFIDENCE_SKU, MatchMethod.APPLE_SKU
        raise LowConfidenceError(
            f"page publishes part number {evidence.sku!r} which does not contain the "
            f"catalogue part number {catalogue_sku!r}; the page appears to price a "
            "different configuration, so no offer is published",
            confidence=CONFIDENCE_SKU_CONFLICT,
        )
    return CONFIDENCE_CONFIG, MatchMethod.CONFIG


def _confirm_title(product: Product, evidence: PriceEvidence, *, url: str) -> None:
    """Prove the captured title still describes this catalogue product."""
    try:
        resolved = normalise(evidence.title)
    except NormalisationError as exc:
        raise TitleMismatchError(
            f"page title {evidence.title!r} cannot be normalised ({exc}); the product "
            f"may have been superseded — refusing to publish against "
            f"{product.product_id!r}",
            published_title=evidence.title,
            catalogue_product_id=product.product_id,
            resolved_product_id=None,
            url=url,
            product_id=product.product_id,
        ) from exc
    if resolved.product_id != product.product_id:
        raise TitleMismatchError(
            f"page title {evidence.title!r} now resolves to {resolved.product_id!r} but "
            f"the catalogue row is {product.product_id!r}; the product has probably been "
            "superseded — refusing to re-map the price",
            published_title=evidence.title,
            catalogue_product_id=product.product_id,
            resolved_product_id=resolved.product_id,
            url=url,
            product_id=product.product_id,
        )


def offer_id_for(product_id: str, captured_at: datetime) -> str:
    """Stable-for-the-day identifier: ``apple-au-<product_id>-<YYYYMMDD>``."""
    return f"apple-au-{product_id}-{captured_at.astimezone(UTC).strftime('%Y%m%d')}"


def build_offer(
    product: Product,
    evidence: PriceEvidence,
    *,
    run_id: str,
    captured_at: datetime,
) -> Offer:
    """Build the validated :class:`Offer` for one confirmed page read.

    ``captured_at`` is the fetch instant, never the parse instant: the price is
    what the page said when we read it.
    """
    _confirm_title(product, evidence, url=str(product.apple_au_url))
    confidence, match_method = match_confidence(product, evidence)
    if confidence < PUBLISH_FLOOR:
        raise LowConfidenceError(
            f"match confidence {confidence} is below the {PUBLISH_FLOOR} publish floor",
            confidence=confidence,
        )
    stock_status = map_availability(evidence.availability)
    # ``in_stock`` mirrors an observed state, so an unknown availability stays
    # False: a priced page is not proof of stock, and claiming stock we did not
    # read is exactly the invention the project forbids.
    in_stock = stock_status in {StockStatus.IN_STOCK, StockStatus.LOW_STOCK, StockStatus.PREORDER}
    offer = Offer(
        offer_id=offer_id_for(product.product_id, captured_at),
        product_id=product.product_id,
        seller_id=SELLER_ID,
        url=product.apple_au_url,
        price_aud=evidence.price_aud,
        price_basis=PriceBasis.ADVERTISED,
        in_stock=in_stock,
        stock_status=stock_status,
        confidence=confidence,
        match_method=match_method,
        match_status=MatchStatus.AUTO_PUBLISHED,
        verification_status=VerificationStatus.VERIFIED,
        captured_at=captured_at,
        source_hash=evidence.source_hash,
        run_id=run_id,
    )
    if evidence.delivery_cost_aud is not None:
        # The page stated a shipping rate in AUD, so it is verified evidence.
        # When it states nothing the model default (0.00, unverified) stays,
        # which means "not stated" — never an invented free delivery.
        offer = offer.model_copy(
            update={
                "delivery_cost_aud": evidence.delivery_cost_aud,
                "delivery_cost_verified": True,
            }
        )
    return offer


def _offer_and_evidence(
    product: Product, page: CapturedPage, *, run_id: str
) -> tuple[Offer, PriceEvidence]:
    evidence = parse_page(page)
    return (
        build_offer(product, evidence, run_id=run_id, captured_at=page.fetched_at),
        evidence,
    )


def offer_from_capture(product: Product, page: CapturedPage, *, run_id: str) -> Offer:
    """Parse one capture and return the offer it supports, or raise a typed error."""
    offer, _ = _offer_and_evidence(product, page, run_id=run_id)
    return offer


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def fixture_payload(
    product: Product, page: CapturedPage, *, parse_path: ParsePath
) -> dict[str, object]:
    """Trim a capture to the evidence worth committing: title, JSON-LD, prices.

    JSON-LD and the buy-block price fragments are the only parts the parser
    reads, so a fixture keeps exactly those plus the page title. That keeps each
    file small enough to review and stops the repository filling with pages.
    """
    scripts = [block for block in jsonld_blocks(page.html) if any(_iter_products(_decode(block)))]
    fragments = _price_fragments(page.html)
    parts = ["<!doctype html>", "<html>", "<head>"]
    title = page_title(page.html)
    if title:
        parts.append(f"<title>Buy {title} - Apple (AU)</title>")
    parts.extend(f'<script type="application/ld+json">{block}</script>' for block in scripts)
    parts.extend(["</head>", "<body>"])
    parts.extend(fragments)
    parts.extend(["</body>", "</html>"])
    return {
        "product_id": product.product_id,
        "url": page.url,
        "captured_at": page.fetched_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "http_status": page.http_status,
        "parse_path": parse_path,
        "synthetic": False,
        "html": "\n".join(parts) + "\n",
    }


def _price_fragments(html: str, *, limit: int = FIXTURE_MAX_FRAGMENTS) -> list[str]:
    """Return distinct buy-block price snippets, in document order, capped."""
    fragments: list[str] = []
    for pattern in (_MARKUP_PRICE, _CONFIG_PRICE):
        for match in pattern.finditer(html):
            snippet = match.group(0)
            if snippet not in fragments:
                fragments.append(snippet)
            if len(fragments) >= limit:
                return fragments
    return fragments


def save_fixture(directory: Path, payload: Mapping[str, object]) -> Path | None:
    """Write a fixture, leaving an unchanged capture in place.

    Returns the path when a file was written, ``None`` when the saved capture
    was already identical. Fixtures are test data, not evidence, so a changed
    capture replaces the old one — the snapshot keeps the immutable record.
    """
    product_id = payload.get("product_id")
    html = payload.get("html")
    if not isinstance(product_id, str) or not isinstance(html, str):
        raise ValueError("a fixture payload needs string 'product_id' and 'html'")
    path = directory / f"{product_id}.json"
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = None
        if isinstance(existing, dict) and existing.get("html") == html:
            return None
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


# --------------------------------------------------------------------------- #
# The adapter
# --------------------------------------------------------------------------- #


class AppleAuAdapter(BaseAdapter):
    """Apple AU: one product page per catalogue row, JSON-LD with a markup fallback."""

    seller_id: ClassVar[str] = SELLER_ID
    capabilities: ClassVar[Capabilities] = Capabilities(product_page=True, listing=True, stock=True)

    def __init__(
        self,
        *,
        http: HttpClient,
        gate: RobotsGate,
        limiter: RateLimiter,
        concurrency: ConcurrencyGate | None = None,
        clock: Callable[[], datetime] = utc_now,
        catalogue: Path = PRODUCTS_PATH,
        fixtures_dir: Path | None = None,
    ) -> None:
        super().__init__(
            http=http, gate=gate, limiter=limiter, concurrency=concurrency, clock=clock
        )
        self._catalogue = catalogue
        self._fixtures_dir = fixtures_dir
        self._run_id = new_run_id(clock())
        #: Which read produced each offer in the most recent run, for reporting.
        self.parse_paths: dict[str, ParsePath] = {}

    @property
    def run_id(self) -> str:
        """The identifier for the current run, generated once per collection."""
        return self._run_id

    def policy_urls(self) -> Sequence[str]:
        """Every catalogue product URL, so the gate clears exactly what we fetch."""
        return tuple(str(product.apple_au_url) for product in load_products(self._catalogue))

    def _collect(self, targets: list[Product]) -> CollectionResult:
        started = self._clock()
        self._run_id = new_run_id(started)
        self.parse_paths = {}
        offers: list[Offer] = []
        errors: list[CollectionError] = []
        for product in targets:
            url = str(product.apple_au_url)
            try:
                response = self.fetch(url)
                if response.status != 200:
                    raise ParseError(
                        f"page answered HTTP {response.status}",
                        url=response.url,
                        product_id=product.product_id,
                    )
                page = CapturedPage(
                    url=response.url,
                    html=response.text,
                    fetched_at=response.fetched_at,
                    http_status=response.status,
                )
                offer, evidence = _offer_and_evidence(product, page, run_id=self._run_id)
            except (HttpError, AppleAuError) as exc:
                errors.append(_collection_error(product, exc, url))
                continue
            offers.append(offer)
            self.parse_paths[product.product_id] = evidence.parse_path
            if self._fixtures_dir is not None:
                save_fixture(
                    self._fixtures_dir,
                    fixture_payload(product, page, parse_path=evidence.parse_path),
                )
        return CollectionResult(offers=offers, errors=errors, fetched_at=started)


def _collection_error(product: Product, exc: Exception, url: str) -> CollectionError:
    if isinstance(exc, AppleAuError):
        return CollectionError(
            message=exc.reason,
            error_type=exc.error_type,
            product_id=product.product_id,
            url=exc.url or url,
        )
    return CollectionError(
        message=str(exc),
        error_type="http_error",
        product_id=product.product_id,
        url=url,
    )


def new_run_id(now: datetime) -> str:
    """One identifier per run, sortable and unique to the second."""
    return f"run-{now.astimezone(UTC).strftime('%Y%m%dT%H%M%S')}"


def load_products(path: Path = PRODUCTS_PATH) -> list[Product]:
    """Load the catalogue rows this adapter is allowed to collect."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain an object")
    records = payload.get("products")
    if not isinstance(records, list):
        raise ValueError(f"{path} must contain a 'products' list")
    products = [Product.model_validate(record) for record in records]
    return [product for product in products if product.status is ProductStatus.ACTIVE]


def write_snapshot_for(
    adapter: AppleAuAdapter,
    result: CollectionResult,
    *,
    root: Path = SNAPSHOTS_DIR,
    path: Path | None = None,
) -> Path:
    """Persist a run's offers as the day's snapshot for this seller."""
    target = path or snapshot_path(
        root,
        date=result.fetched_at.astimezone(UTC).date().isoformat(),
        seller_id=SELLER_ID,
    )
    return write_snapshot(target, SELLER_ID, adapter.run_id, result.offers, result.fetched_at)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.collectors.apple_au",
        description=(
            "Collect Apple AU product pages through the policy gate. A partial run "
            "(--limit) writes no snapshot: only a full pass over the catalogue is "
            "evidence of the catalogue's state."
        ),
    )
    parser.add_argument("--limit", type=int, default=None, help="collect at most N products")
    parser.add_argument("--dry-run", action="store_true", help="list targets and fetch nothing")
    parser.add_argument("--products", type=Path, default=PRODUCTS_PATH, help="catalogue to collect")
    parser.add_argument(
        "--fixtures-dir", type=Path, default=FIXTURES_DIR, help="directory for trimmed captures"
    )
    parser.add_argument(
        "--save-fixtures", action="store_true", help="also write trimmed captures for each page"
    )
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS_DIR, help="snapshot root")
    parser.add_argument(
        "--snapshot", type=Path, default=None, help="explicit snapshot file (overrides --snapshots)"
    )
    parser.add_argument(
        "--no-snapshot", action="store_true", help="collect without writing a snapshot"
    )
    return parser.parse_args(argv)


def _print_targets(products: Sequence[Product]) -> None:
    for product in products:
        print(f"  {product.product_id}  {product.apple_au_url}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one collection. Exit 0 when offers were produced, 2 when blocked."""
    args = _parse_args(argv)
    products = load_products(args.products)
    if args.limit is not None:
        if args.limit < 1:
            print("--limit must be at least 1", file=sys.stderr)
            return 1
        products = products[: args.limit]

    if args.dry_run:
        print(f"{SELLER_ID}: dry run, {len(products)} product(s) targeted, nothing fetched")
        _print_targets(products)
        return 0

    settings = load_settings()
    limiter = RateLimiter.from_registry(SELLERS_REGISTRY)
    with HttpClient(settings) as http:
        adapter = AppleAuAdapter(
            http=http,
            gate=RobotsGate(user_agent=settings.user_agent),
            limiter=limiter,
            catalogue=args.products,
            fixtures_dir=args.fixtures_dir if args.save_fixtures else None,
        )
        print(f"{SELLER_ID}: run {adapter.run_id}, {len(products)} product(s) targeted")
        result = adapter.collect(products)

    if result.blocked:
        for blocked in result.blocked:
            print(f"BLOCKED {blocked.seller_id}: {blocked.reason}", file=sys.stderr)
        print(f"snapshot: not written ({len(result.offers)} offers, policy refusal)")
        return 2

    counts: dict[str, int] = {}
    for offer in result.offers:
        counts[offer.stock_status.value] = counts.get(offer.stock_status.value, 0) + 1
    paths: dict[str, int] = {}
    for path in adapter.parse_paths.values():
        paths[path] = paths.get(path, 0) + 1

    print(f"offers: {len(result.offers)} verified, {len(result.errors)} error(s)")
    print(
        "  parse path: "
        + (", ".join(f"{key}={value}" for key, value in sorted(paths.items())) or "none")
    )
    print(
        "  stock:      "
        + (", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none")
    )
    for error in result.errors:
        print(f"  ERROR {error.product_id or '-'}: [{error.error_type}] {error.message}")

    if args.no_snapshot:
        print("snapshot: not written (--no-snapshot)")
    elif args.snapshot is None and args.limit is not None:
        print(
            "snapshot: not written (partial run of "
            f"{len(products)} product(s); pass --snapshot to keep it)"
        )
    else:
        try:
            written = write_snapshot_for(adapter, result, root=args.snapshots, path=args.snapshot)
        except SnapshotConflictError as exc:
            print(f"snapshot: REFUSED — {exc}", file=sys.stderr)
            return 1
        size_kb = written.stat().st_size / 1024
        print(f"snapshot: {written} ({size_kb:.1f} KB, {len(result.offers)} offers)")

    return 0 if result.offers else 1


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
