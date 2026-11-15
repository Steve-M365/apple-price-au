"""Apple Education Store (``apple.com/au-edu``) price collector.

Education pricing is a *separate offer type*. It is never merged into a retail
:class:`~scripts.models.offer.Offer`, and an education price is never promoted to
``rrp_aud`` (AGENTS.md rule 5). This module emits
:class:`~scripts.models.education.EducationOffer` only.

What the store actually does (captured 2026-11-15, quoted verbatim in the
snapshot this collector writes):

* **Education prices are public.** ``https://www.apple.com/au-edu/shop/...``
  answers a plain, unauthenticated GET with HTTP 200 and the education price is
  in the server-rendered markup: the Product JSON-LD carries
  ``offers[0].price`` (AUD) and the page renders ``"From A$1,939.00"`` with an
  ``"Education Savings"`` badge. No cookie, no sign-in, no browser emulation.
* **UNiDAYS gates check-out, not browsing.** ``/au-edu/shop/buy-mac`` offers
  ``"Verify with UNiDAYS now"`` next to ``"Continue shopping and verify later"``
  and its transcript says: *"Just shop the Education Store on apple.com and
  verify your eligibility during checkout with a UNiDAYS account."* The product
  page FAQ says eligibility *"may"* be verified *"during check-out"*.
* **Eligibility and purchase limits are published** on the Education sales
  policy page. Both are recorded in Apple's own words (the quantity list is
  joined with semicolons), with the URL they came from, rather than paraphrased
  from memory.

Stock is *not* observed by this collector: the Education Store product page
carries no per-configuration stock statement. ``in_stock`` is therefore
``None`` (not observed) rather than ``False``, which would read as a confirmed
"out of stock". A stock lookup is a separate card.

The adapter reads prices from the Product JSON-LD only. A price that is not
below the catalogue's Apple AU retail price is treated as a mis-read (a retail
price leaking in) and recorded as a :class:`CollectionError`, never as an offer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import ClassVar, Final, cast
from uuid import uuid4

from bs4 import BeautifulSoup
from pydantic import HttpUrl

from scripts.models import EducationOffer, PriceBasis, Product, VerificationStatus
from scripts.models.common import format_utc, round_percent

from .base import (
    BaseAdapter,
    BlockedSource,
    Capabilities,
    CollectionError,
    CollectionResult,
    ConcurrencyGate,
    PolicyResult,
    RateLimiter,
    utc_now,
)
from .http import HttpClient, HttpError, load_settings
from .policy import RobotsGate
from .snapshot import SNAPSHOT_SCHEMA_VERSION, SnapshotConflictError, snapshot_path

__all__ = [
    "ANNUAL_LIMIT_SOURCE_URL",
    "ANNUAL_LIMIT_SUMMARY",
    "EDUCATION_STORE_URL",
    "EDUCATION_TERMS_URL",
    "EDUCATION_VERIFICATION_URL",
    "ELIGIBILITY_CATEGORIES",
    "FRESHNESS_MAX_AGE_HOURS",
    "PRODUCTS_PATH",
    "SELLER_ID",
    "SNAPSHOT_ROOT",
    "VERIFICATION_GATE",
    "VERIFICATION_VENDOR",
    "AppleEducationAdapter",
    "EducationCollectionResult",
    "FragmentError",
    "ProductPriceFragment",
    "build_snapshot",
    "education_url_for",
    "extract_product_fragment",
    "load_education_products",
    "main",
    "product_jsonld_objects",
    "read_price_fragment",
    "snapshot_path_for",
    "write_snapshot",
]

#: Repository root, resolved from this file so a run cannot depend on the cwd.
_REPO_ROOT: Final = Path(__file__).resolve().parents[2]

#: The committed catalogue. The collector reads it only for the retail price and
#: eligibility flag; it never writes to it.
PRODUCTS_PATH: Final = _REPO_ROOT / "data" / "products.json"

#: Append-only evidence root. Snapshots live under a capture-date directory.
SNAPSHOT_ROOT: Final = _REPO_ROOT / "data" / "snapshots"

SELLER_ID: Final = "apple_education_au"

EDUCATION_STORE_URL: Final = "https://www.apple.com/au-edu/store"

#: Where Apple names the verification vendor and says when it applies.
EDUCATION_VERIFICATION_URL: Final = "https://www.apple.com/au-edu/shop/buy-mac"

#: ``/au-hed/...`` 301s here; this is the URL a capture should record.
EDUCATION_TERMS_URL: Final = "https://www.apple.com/au-edu/shop/browse/open/salespolicies/edu"

VERIFICATION_VENDOR: Final = "UNiDAYS"
VERIFICATION_GATE: Final = "checkout"

#: Education pricing is annual and eligibility-bound, not a sale cycle. The model
#: caps freshness at 720 hours, and a month is the widest budget it permits.
FRESHNESS_MAX_AGE_HOURS: Final = 168

#: The categories Apple publishes under "Eligibility" on the Education sales
#: policy page, quoted without the leading "a".
ELIGIBILITY_CATEGORIES: Final[tuple[str, ...]] = (
    "A full-time or part-time student aged 18 or over with a current student union "
    "card or student identification card and studying at or accepted into an "
    "accredited Australian university or other Apple-approved institution",
    "A lecturer or staff member at an accredited Australian university",
    "A teacher or lecturer working at an Apple-approved and registered education "
    "establishment within Australia",
    "A parent purchasing on behalf of their child, who is a student currently "
    "attending or accepted into an accredited Australian university or other "
    "Apple-approved institution",
)

ANNUAL_LIMIT_SOURCE_URL: Final = EDUCATION_TERMS_URL

#: Apple's "Purchase Quantity" section, verbatim and in page order. Nothing here
#: is paraphrased; the strings are checked against the live page at run time.
ANNUAL_LIMIT_SUMMARY: Final = (
    # Apple's wording exactly. The page renders the quantity rows as a list, so the
    # entries are joined here with "; " and no word is altered or added.
    "Those eligible to purchase from the Apple Store for Education will be allowed "
    "to purchase the following quantities of products per year. Not all products "
    "have special Education Pricing. Desktop: One (1) may be purchased per year; "
    "Mac mini: One (1) may be purchased per year; MacBook: One (1) may be purchased "
    "per year; Displays: One (1) may be purchased per year; iPad: Two (2) may be "
    "purchased per year; Apple Watch: One (1) Apple Watch with Education Pricing may "
    "be purchased per year; Accessories: Two (2) accessories with Education Pricing "
    "may be purchased per year. The above purchases can be made either online or in "
    "an Apple Retail Store. Total product quantities remain the same regardless of "
    "where your purchase is made."
)

#: Phrases that must still be present on the live pages for the metadata above to
#: count as verified in this run. A change means the claim is stale, not wrong.
_ELIGIBILITY_MARKERS: Final[tuple[str, ...]] = (
    "Those eligible to purchase from the Apple Store for Education are as follows",
    "must be for your own personal, education, or research use",
    "parent purchasing on behalf of their child",
)
_LIMIT_MARKERS: Final[tuple[str, ...]] = (
    "Not all products have special Education Pricing",
    "MacBook: One (1) may be purchased per year",
    "iPad: Two (2) may be purchased per year",
    "Accessories: Two (2) accessories with Education Pricing may be purchased per year",
)
_VERIFICATION_MARKERS: Final[tuple[str, ...]] = (
    "UNiDAYS",
    "verify your eligibility during checkout",
    "Continue shopping and verify later",
)

_JSONLD_SCRIPT: Final = re.compile(
    r'<script[^>]*type\s*=\s*"application/ld\+json"[^>]*>(?P<body>.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_HTML_TAG: Final = re.compile(r"<[^>]+>")
_WHITESPACE: Final = re.compile(r"\s+")


def _normspace(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


# --------------------------------------------------------------------------- #
# Catalogue selection
# --------------------------------------------------------------------------- #


def education_url_for(product: Product) -> str:
    """Return the Education Store URL for a product's Apple AU retail page.

    Apple publishes the education storefront on the same path shape under
    ``/au-edu/``; the retailer path is rewritten, never guessed from a title.
    """
    retail = str(product.apple_au_url)
    marker = "/au/shop/"
    if marker not in retail:
        raise ValueError(f"{product.product_id}: cannot derive an education URL from {retail!r}")
    return retail.replace(marker, "/au-edu/shop/", 1)


def load_education_products(products_path: Path | str = PRODUCTS_PATH) -> list[Product]:
    """Load the catalogue records Apple sells at education pricing.

    Only ``education_eligible: true`` products are returned. Eligibility is a
    catalogue decision; the collector never infers it from a category.
    """
    payload = json.loads(Path(products_path).read_text(encoding="utf-8"))
    records = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"{products_path}: expected an object with a 'products' list")
    products: list[Product] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"{products_path}: every product record must be an object")
        if cast(dict[str, object], record).get("education_eligible") is not True:
            continue
        products.append(Product.model_validate(record))
    return products


# --------------------------------------------------------------------------- #
# JSON-LD parsing
# --------------------------------------------------------------------------- #


class FragmentError(ValueError):
    """A captured Product fragment does not carry a usable AUD price."""


@dataclass(frozen=True, slots=True)
class ProductPriceFragment:
    """One price-bearing Product JSON-LD object, exactly as the page emitted it."""

    name: str
    url: str
    sku: str | None
    currency: str
    price_aud: Decimal
    payload: dict[str, object]

    @property
    def source_hash(self) -> str:
        """Deterministic ``sha256:`` hash of the fragment, for offer provenance."""
        return source_hash(self.payload)


def source_hash(payload: object) -> str:
    """Hash a parsed fragment with sorted keys so the digest is reproducible."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_mapping(value: object) -> Mapping[str, object] | None:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _as_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _iter_jsonld(value: object) -> Iterator[Mapping[str, object]]:
    if isinstance(value, list):
        for item in cast(list[object], value):
            yield from _iter_jsonld(item)
        return
    mapping = _as_mapping(value)
    if mapping is None:
        return
    graph = mapping.get("@graph")
    if graph is not None:
        yield from _iter_jsonld(graph)
    yield mapping


def product_jsonld_objects(html: str) -> Iterator[Mapping[str, object]]:
    """Yield every JSON-LD object on a page, skipping unparseable blocks."""
    for match in _JSONLD_SCRIPT.finditer(html):
        try:
            parsed: object = json.loads(match.group("body"))
        except json.JSONDecodeError:
            continue
        yield from _iter_jsonld(parsed)


def extract_product_fragment(html: str) -> dict[str, object] | None:
    """Return the first ``@type: Product`` JSON-LD object, or ``None``."""
    for obj in product_jsonld_objects(html):
        if obj.get("@type") == "Product":
            return dict(obj)
    return None


def _first_offer(payload: Mapping[str, object]) -> Mapping[str, object]:
    offers = payload.get("offers")
    if isinstance(offers, list):
        entries = cast(list[object], offers)
        if not entries:
            raise FragmentError("the Product fragment carries an empty offers list")
        offer = _as_mapping(entries[0])
    else:
        offer = _as_mapping(offers)
    if offer is None:
        raise FragmentError("the Product fragment carries no offers object")
    return offer


def read_price_fragment(payload: Mapping[str, object]) -> ProductPriceFragment:
    """Read the AUD price from a Product fragment, or raise :class:`FragmentError`."""
    if payload.get("@type") != "Product":
        raise FragmentError(f"expected @type 'Product', got {payload.get('@type')!r}")
    offer = _first_offer(payload)
    currency = _as_text(offer.get("priceCurrency"))
    if currency is None or currency.strip().upper() != "AUD":
        raise FragmentError(f"expected priceCurrency 'AUD', got {currency!r}")
    raw_price = offer.get("price")
    if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float, str, Decimal)):
        raise FragmentError(f"expected a numeric price, got {raw_price!r}")
    try:
        price = Decimal(str(raw_price))
    except InvalidOperation as exc:
        raise FragmentError(f"price {raw_price!r} is not a decimal amount") from exc
    if price <= 0:
        raise FragmentError(f"price must be greater than zero, got {price}")
    return ProductPriceFragment(
        name=_as_text(payload.get("name")) or "",
        url=_as_text(payload.get("url")) or "",
        sku=_as_text(offer.get("sku")),
        currency=currency.strip().upper(),
        price_aud=price,
        payload=dict(payload),
    )


# --------------------------------------------------------------------------- #
# Run metadata captured alongside the prices
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TermsCapture:
    """Whether the published eligibility and limits survived this run's re-read."""

    source_url: str
    captured_at: datetime | None
    source_hash: str | None
    eligibility_verified: bool
    limits_verified: bool
    note: str


@dataclass(frozen=True, slots=True)
class VerificationCapture:
    """Whether Apple still names its verification vendor and where it applies."""

    source_url: str
    captured_at: datetime | None
    source_hash: str | None
    vendor: str
    gate: str
    verified: bool
    note: str


@dataclass(frozen=True, slots=True)
class EducationCollectionResult:
    """One education run: the offers plus the policy and metadata behind them."""

    offers: list[EducationOffer]
    blocked: list[BlockedSource]
    errors: list[CollectionError]
    fetched_at: datetime
    policy: PolicyResult | None
    terms: TermsCapture
    verification: VerificationCapture

    @property
    def blocked_by_policy(self) -> bool:
        return bool(self.blocked)


def _text_of(html: str) -> str:
    """Visible text of a page, with scripts and styles removed."""
    soup = BeautifulSoup(html, "lxml")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return _normspace(soup.get_text(" "))


# --------------------------------------------------------------------------- #
# The adapter
# --------------------------------------------------------------------------- #


class AppleEducationAdapter(BaseAdapter):
    """Collect Apple Education Store prices for education-eligible products.

    ``BaseAdapter.collect`` returns a :class:`CollectionResult` whose ``offers``
    list is typed for retail :class:`~scripts.models.offer.Offer`. This adapter
    never emits a retail price, so that list is always empty and the education
    offers are returned by :meth:`collect_education`, which is the entry point
    callers should use.
    """

    seller_id: ClassVar[str] = SELLER_ID
    capabilities: ClassVar[Capabilities] = Capabilities(product_page=True)

    def __init__(
        self,
        *,
        http: HttpClient,
        gate: RobotsGate,
        limiter: RateLimiter,
        concurrency: ConcurrencyGate | None = None,
        clock: Callable[[], datetime] = utc_now,
        run_id: str | None = None,
        products_path: Path | str = PRODUCTS_PATH,
    ) -> None:
        super().__init__(
            http=http,
            gate=gate,
            limiter=limiter,
            concurrency=concurrency,
            clock=clock,
        )
        self._run_id = run_id or f"{utc_now():%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
        self._products_path = Path(products_path)
        self._education_offers: list[EducationOffer] = []
        self._policy: PolicyResult | None = None
        self._terms = TermsCapture(
            source_url=EDUCATION_TERMS_URL,
            captured_at=None,
            source_hash=None,
            eligibility_verified=False,
            limits_verified=False,
            note="not captured in this run",
        )
        self._verification = VerificationCapture(
            source_url=EDUCATION_VERIFICATION_URL,
            captured_at=None,
            source_hash=None,
            vendor=VERIFICATION_VENDOR,
            gate=VERIFICATION_GATE,
            verified=False,
            note="not captured in this run",
        )

    # -- provenance ------------------------------------------------------ #

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def terms(self) -> TermsCapture:
        return self._terms

    @property
    def verification(self) -> VerificationCapture:
        return self._verification

    @property
    def policy(self) -> PolicyResult | None:
        return self._policy

    @property
    def education_offers(self) -> list[EducationOffer]:
        """Offers from the last :meth:`collect_education` call."""
        return list(self._education_offers)

    # -- policy ---------------------------------------------------------- #

    def policy_urls(self) -> Sequence[str]:
        """Every URL this adapter may fetch in a full run.

        The gate caches per origin, so naming all of them costs one robots.txt
        fetch. Each URL is checked again, individually, immediately before it is
        used, so a ``--limit`` run cannot rely on a URL it did not declare.
        """
        urls: list[str] = [EDUCATION_VERIFICATION_URL, EDUCATION_TERMS_URL]
        urls.extend(education_url_for(product) for product in self._catalogue())
        return tuple(dict.fromkeys(urls))

    def check_policy(self) -> PolicyResult:
        result = super().check_policy()
        self._policy = result
        return result

    def _catalogue(self) -> list[Product]:
        return load_education_products(self._products_path)

    # -- entry point ----------------------------------------------------- #

    def collect_education(self, targets: list[Product]) -> EducationCollectionResult:
        """Run the gate, then capture education prices for ``targets``."""
        self._education_offers = []
        result = self.collect(targets)
        return EducationCollectionResult(
            offers=list(self._education_offers),
            blocked=list(result.blocked),
            errors=list(result.errors),
            fetched_at=result.fetched_at,
            policy=self._policy,
            terms=self._terms,
            verification=self._verification,
        )

    # -- collection ------------------------------------------------------ #

    def _guard(self, url: str) -> BlockedSource | None:
        """Re-check one exact URL. A denial is final and stops the run."""
        decision = self._gate.is_allowed(url)
        if decision.allowed:
            return None
        return BlockedSource(
            seller_id=self.seller_id,
            reason=decision.reason,
            evidence_url=decision.robots_url,
            checked_at=decision.fetched_at,
        )

    def _capture_metadata(self, errors: list[CollectionError]) -> None:
        """Re-read the limits and verification pages, recording what they say."""
        self._terms = self._capture_terms(errors)
        self._verification = self._capture_verification(errors)

    def _capture_terms(self, errors: list[CollectionError]) -> TermsCapture:
        blocked = self._guard(EDUCATION_TERMS_URL)
        if blocked is not None:
            errors.append(
                CollectionError(
                    message=f"education sales policy denied by policy: {blocked.reason}",
                    error_type="PolicyDenied",
                    url=EDUCATION_TERMS_URL,
                )
            )
            return TermsCapture(
                source_url=EDUCATION_TERMS_URL,
                captured_at=None,
                source_hash=None,
                eligibility_verified=False,
                limits_verified=False,
                note=f"denied by policy: {blocked.reason}",
            )
        try:
            response = self.fetch(EDUCATION_TERMS_URL)
        except HttpError as exc:
            errors.append(
                CollectionError(
                    message=f"could not re-read the education sales policy: {exc}",
                    error_type=type(exc).__name__,
                    url=EDUCATION_TERMS_URL,
                )
            )
            return TermsCapture(
                source_url=EDUCATION_TERMS_URL,
                captured_at=None,
                source_hash=None,
                eligibility_verified=False,
                limits_verified=False,
                note=f"fetch failed: {exc}",
            )
        if response.status != 200:
            errors.append(
                CollectionError(
                    message=f"education sales policy answered HTTP {response.status}",
                    error_type="HttpStatus",
                    url=EDUCATION_TERMS_URL,
                )
            )
            return TermsCapture(
                source_url=EDUCATION_TERMS_URL,
                captured_at=response.fetched_at,
                source_hash=None,
                eligibility_verified=False,
                limits_verified=False,
                note=f"HTTP {response.status}",
            )
        text = _text_of(response.text)
        eligibility_verified = all(marker in text for marker in _ELIGIBILITY_MARKERS)
        limits_verified = all(marker in text for marker in _LIMIT_MARKERS)
        return TermsCapture(
            source_url=EDUCATION_TERMS_URL,
            captured_at=response.fetched_at,
            source_hash=source_hash({"text": text}),
            eligibility_verified=eligibility_verified,
            limits_verified=limits_verified,
            note=(
                "eligibility and purchase-quantity wording found on the live page"
                if eligibility_verified and limits_verified
                else "the live page no longer carries the recorded wording; "
                "the committed claim is retained with its source URL and flagged stale"
            ),
        )

    def _capture_verification(self, errors: list[CollectionError]) -> VerificationCapture:
        blocked = self._guard(EDUCATION_VERIFICATION_URL)
        if blocked is not None:
            errors.append(
                CollectionError(
                    message=f"education store page denied by policy: {blocked.reason}",
                    error_type="PolicyDenied",
                    url=EDUCATION_VERIFICATION_URL,
                )
            )
            return VerificationCapture(
                source_url=EDUCATION_VERIFICATION_URL,
                captured_at=None,
                source_hash=None,
                vendor=VERIFICATION_VENDOR,
                gate=VERIFICATION_GATE,
                verified=False,
                note=f"denied by policy: {blocked.reason}",
            )
        try:
            response = self.fetch(EDUCATION_VERIFICATION_URL)
        except HttpError as exc:
            errors.append(
                CollectionError(
                    message=f"could not re-read the education store page: {exc}",
                    error_type=type(exc).__name__,
                    url=EDUCATION_VERIFICATION_URL,
                )
            )
            return VerificationCapture(
                source_url=EDUCATION_VERIFICATION_URL,
                captured_at=None,
                source_hash=None,
                vendor=VERIFICATION_VENDOR,
                gate=VERIFICATION_GATE,
                verified=False,
                note=f"fetch failed: {exc}",
            )
        if response.status != 200:
            errors.append(
                CollectionError(
                    message=f"education store page answered HTTP {response.status}",
                    error_type="HttpStatus",
                    url=EDUCATION_VERIFICATION_URL,
                )
            )
            return VerificationCapture(
                source_url=EDUCATION_VERIFICATION_URL,
                captured_at=response.fetched_at,
                source_hash=None,
                vendor=VERIFICATION_VENDOR,
                gate=VERIFICATION_GATE,
                verified=False,
                note=f"HTTP {response.status}",
            )
        # The vendor wording lives inside the page's script payload as well as in
        # the rendered button, so the raw HTML is the honest place to look.
        verified = all(marker in response.text for marker in _VERIFICATION_MARKERS)
        return VerificationCapture(
            source_url=EDUCATION_VERIFICATION_URL,
            captured_at=response.fetched_at,
            source_hash=source_hash({"html": response.text}),
            vendor=VERIFICATION_VENDOR,
            gate=VERIFICATION_GATE,
            verified=verified,
            note=(
                "Apple names UNiDAYS and says verification happens during checkout"
                if verified
                else "the live page no longer names the recorded verification vendor and gate"
            ),
        )

    def _collect(self, targets: list[Product]) -> CollectionResult:
        errors: list[CollectionError] = []
        self._education_offers = []
        self._capture_metadata(errors)

        eligible = [product for product in targets if product.education_eligible]
        for product in eligible:
            url = education_url_for(product)
            blocked = self._guard(url)
            if blocked is not None:
                # A denial is final for the seller: nothing captured so far may be
                # published as if the run had completed.
                self._education_offers = []
                return CollectionResult(
                    offers=[],
                    blocked=[blocked],
                    errors=[],
                    fetched_at=self._clock(),
                )

            try:
                response = self.fetch(url)
            except HttpError as exc:
                errors.append(
                    CollectionError(
                        message=str(exc),
                        error_type=type(exc).__name__,
                        product_id=product.product_id,
                        url=url,
                    )
                )
                continue

            if response.status != 200:
                errors.append(
                    CollectionError(
                        message=f"expected HTTP 200, got {response.status}",
                        error_type="HttpStatus",
                        product_id=product.product_id,
                        url=url,
                    )
                )
                continue

            payload = extract_product_fragment(response.text)
            if payload is None:
                errors.append(
                    CollectionError(
                        message="no Product JSON-LD on the education page; "
                        "no price was guessed from the rendered markup",
                        error_type="MissingJsonLd",
                        product_id=product.product_id,
                        url=url,
                    )
                )
                continue

            try:
                fragment = read_price_fragment(payload)
            except FragmentError as exc:
                errors.append(
                    CollectionError(
                        message=str(exc),
                        error_type="FragmentError",
                        product_id=product.product_id,
                        url=url,
                    )
                )
                continue

            try:
                offer = self._build_offer(product, fragment, url, response.fetched_at)
            except ValueError as exc:
                errors.append(
                    CollectionError(
                        message=str(exc),
                        error_type=type(exc).__name__,
                        product_id=product.product_id,
                        url=url,
                    )
                )
                continue
            self._education_offers.append(offer)

        return CollectionResult(offers=[], blocked=[], errors=errors, fetched_at=self._clock())

    def _build_offer(
        self,
        product: Product,
        fragment: ProductPriceFragment,
        url: str,
        captured_at: datetime,
    ) -> EducationOffer:
        standard = product.apple_rrp_aud
        if standard is None:
            raise ValueError(
                "the catalogue has no apple_rrp_aud for this product, so a saving "
                "cannot be computed and an education price cannot be asserted alone"
            )
        if product.apple_sku and fragment.sku and fragment.sku != product.apple_sku:
            raise ValueError(
                f"page SKU {fragment.sku!r} does not match catalogue SKU "
                f"{product.apple_sku!r}; refusing to attach the price to the wrong product"
            )
        if fragment.price_aud >= standard:
            raise ValueError(
                f"education price {fragment.price_aud} is not below the catalogue Apple "
                f"AU retail price {standard}; this looks like a retail price and is "
                "never recorded as an education price"
            )
        saving = standard - fragment.price_aud
        saving_pct = round_percent(saving / standard * 100)
        return EducationOffer(
            offer_id=f"apple-edu-{product.product_id}-{captured_at:%Y%m%d}",
            product_id=product.product_id,
            seller_id=self.seller_id,
            url=HttpUrl(url),
            price_aud=fragment.price_aud,
            standard_price_aud=standard,
            saving_aud=saving,
            saving_pct=saving_pct,
            eligibility=list(ELIGIBILITY_CATEGORIES),
            verification=VERIFICATION_VENDOR,
            annual_limit_summary=ANNUAL_LIMIT_SUMMARY,
            annual_limit_source_url=HttpUrl(ANNUAL_LIMIT_SOURCE_URL),
            in_stock=None,
            price_basis=PriceBasis.EDU,
            verification_status=VerificationStatus.VERIFIED,
            freshness_max_age_hours=FRESHNESS_MAX_AGE_HOURS,
            captured_at=captured_at,
            source_hash=fragment.source_hash,
            run_id=self._run_id,
        )


# --------------------------------------------------------------------------- #
# Snapshot
# --------------------------------------------------------------------------- #


def snapshot_path_for(
    captured_at: datetime,
    root: Path | str = SNAPSHOT_ROOT,
    *,
    run_id: str | None = None,
) -> Path:
    """``data/snapshots/<UTC capture date>/apple_education_au.json``.

    Delegates to :func:`scripts.collectors.snapshot.snapshot_path` so every
    adapter uses one path convention, and takes the date from the UTC capture
    instant rather than the local clock.
    """
    return snapshot_path(
        Path(root),
        date=captured_at.astimezone(UTC).date().isoformat(),
        seller_id=SELLER_ID,
        run_id=run_id,
    )


def build_snapshot(
    result: EducationCollectionResult,
    *,
    run_id: str,
    user_agent: str,
    targets: Sequence[Product],
) -> dict[str, object]:
    """Assemble the evidence document written for one education run."""
    captured = result.fetched_at
    observations: list[dict[str, object]] = [
        {
            "question": "Are education prices displayed publicly without signing in?",
            "answer": "yes",
            "url": "https://www.apple.com/au-edu/shop/buy-mac/macbook-air/13-inch-midnight-m5-chip-10-core-cpu-8-core-gpu-16gb-memory-512gb-storage",
            "evidence": "A plain unauthenticated GET returned HTTP 200 and the page renders "
            '"From A$1,939.00" with an "Education Savings" badge; the Product JSON-LD '
            "carries offers[0].price 1939.00 AUD. No cookie, no sign-in, no verification.",
        },
        {
            "question": "Where does UNiDAYS verification gate the purchase?",
            "answer": VERIFICATION_GATE,
            "url": EDUCATION_VERIFICATION_URL,
            "evidence": '"Continue shopping and verify later" is offered next to '
            '"Verify with UNiDAYS now", and the page transcript reads: "Just shop the '
            "Education Store on apple.com and verify your eligibility during checkout "
            'with a UNiDAYS account." The product page FAQ reads: "You may need to '
            "verify your eligibility with an active school email or other documentation "
            'during check-out. Verification varies by country."',
        },
        {
            "question": "What does Apple state about eligibility and purchase limits?",
            "answer": "published on the education sales policy page, recorded verbatim below",
            "url": EDUCATION_TERMS_URL,
            "evidence": ANNUAL_LIMIT_SUMMARY,
        },
    ]
    ordered = sorted(result.offers, key=lambda offer: offer.offer_id)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot": {
            "seller_id": SELLER_ID,
            "source": "Apple Education Store, apple.com/au-edu/shop",
            "store_url": EDUCATION_STORE_URL,
            "price_basis": "edu",
            "captured_at": format_utc(captured),
            "run_id": run_id,
            "user_agent": user_agent,
            "method": (
                "One plain unauthenticated GET per product page with the OrchardPriceBot "
                "user agent. The price is read from the server-rendered Product JSON-LD "
                "offers[].price (priceCurrency AUD) and cross-checked against the "
                "catalogue SKU. No browser emulation, no cookies, no sign-in."
            ),
            "store_observation": {
                "education_prices_public": True,
                "sign_in_required_to_view_price": False,
                "verification_vendor": result.verification.vendor,
                "verification_gate": result.verification.gate,
                "verification_source_url": result.verification.source_url,
                "verification_verified_in_run": result.verification.verified,
                "verification_note": result.verification.note,
                "observed_quotes": observations,
            },
            "eligibility": list(ELIGIBILITY_CATEGORIES),
            "annual_limit_summary": ANNUAL_LIMIT_SUMMARY,
            "annual_limit_source_url": ANNUAL_LIMIT_SOURCE_URL,
            "annual_limit_verified_in_run": result.terms.limits_verified,
            "eligibility_verified_in_run": result.terms.eligibility_verified,
            "terms_capture": {
                "source_url": result.terms.source_url,
                "captured_at": (
                    format_utc(result.terms.captured_at)
                    if result.terms.captured_at is not None
                    else None
                ),
                "source_hash": result.terms.source_hash,
                "note": result.terms.note,
            },
            "policy": result.policy.to_json() if result.policy is not None else None,
            "targets": len(targets),
            "offers_recorded": len(result.offers),
            "stock_basis": (
                "NOT OBSERVED. The Education Store product page carries no "
                "per-configuration stock statement, so in_stock=false on every offer "
                "means 'not asserted', never 'unavailable'. A stock lookup is a "
                "separate capture (fulfillment-messages) and out of scope for this card."
            ),
            "errors": len(result.errors),
        },
        "offers": [offer.model_dump(mode="json") for offer in ordered],
        "errors": [
            {
                "message": error.message,
                "error_type": error.error_type,
                "product_id": error.product_id,
                "url": error.url,
            }
            for error in result.errors
        ],
        "blocked": [
            {
                "seller_id": blocked.seller_id,
                "reason": blocked.reason,
                "evidence_url": blocked.evidence_url,
                "checked_at": format_utc(blocked.checked_at),
            }
            for blocked in result.blocked
        ],
    }


def write_snapshot(payload: Mapping[str, object], path: Path) -> Path:
    """Write one education snapshot under the shared append-only contract.

    Follows :mod:`scripts.collectors.snapshot`: a byte-identical rewrite is a
    no-op, different content at the same path raises
    :class:`~scripts.collectors.snapshot.SnapshotConflictError` instead of
    replacing evidence, the write is atomic, and a capture may only land in its
    own UTC date directory.
    """
    captured = payload.get("snapshot")
    if not isinstance(captured, dict):
        raise ValueError("snapshot payload must carry a 'snapshot' object")
    captured_at = cast(dict[str, object], captured).get("captured_at")
    if not isinstance(captured_at, str):
        raise ValueError("snapshot payload must carry snapshot.captured_at")
    capture_date = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).date()
    if path.parent.name != capture_date.strftime("%Y-%m-%d"):
        raise ValueError(
            f"refusing to write a {capture_date} capture into {path.parent.name}; "
            "data/snapshots is append-only evidence"
        )
    data = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if path.exists():
        existing = path.read_bytes()
        if existing == data:
            return path
        raise SnapshotConflictError(path, existing_bytes=len(existing), new_bytes=len(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:  # pragma: no cover - defensive cleanup
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_adapter(products_path: Path | str) -> AppleEducationAdapter:
    settings = load_settings()
    return AppleEducationAdapter(
        http=HttpClient(settings=settings),
        gate=RobotsGate(user_agent=settings.user_agent),
        limiter=RateLimiter.from_registry(),
        products_path=products_path,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.collectors.apple_education",
        description="Collect Apple Education Store prices for education-eligible products.",
    )
    parser.add_argument("--limit", type=int, default=None, help="collect at most N products")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and print, but do not write the snapshot",
    )
    parser.add_argument("--products", type=Path, default=PRODUCTS_PATH, help="catalogue path")
    parser.add_argument("--snapshot", type=Path, default=None, help="snapshot path override")
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    adapter = _build_adapter(args.products)
    targets = load_education_products(args.products)
    if args.limit is not None:
        targets = targets[: args.limit]

    result = adapter.collect_education(targets)
    for offer in result.offers:
        print(
            f"{offer.product_id}: A${offer.price_aud} education "
            f"(retail A${offer.standard_price_aud}, save A${offer.saving_aud}, "
            f"{offer.saving_pct}%)"
        )
    for error in result.errors:
        print(f"ERROR {error.error_type}: {error.message} [{error.product_id or error.url}]")
    for blocked in result.blocked:
        print(f"BLOCKED {blocked.seller_id}: {blocked.reason} ({blocked.evidence_url})")
    print(
        f"offers={len(result.offers)} errors={len(result.errors)} "
        f"blocked={len(result.blocked)} terms_verified={result.terms.limits_verified} "
        f"verification_verified={result.verification.verified}"
    )

    if result.blocked:
        return 2
    if not result.offers:
        return 1
    if args.dry_run:
        print("dry run: snapshot not written")
        return 0

    payload = build_snapshot(
        result,
        run_id=adapter.run_id,
        user_agent=adapter.http.user_agent,
        targets=targets,
    )
    path = args.snapshot or snapshot_path_for(result.fetched_at)
    if args.limit is not None and path.exists():
        # A truncated run is not a complete capture of the day's education prices,
        # so it must never replace the day's evidence with a subset.
        print(
            f"snapshot not written: {path} already exists and this was a --limit run; "
            "re-run without --limit to refresh the day's capture"
        )
        return 0
    try:
        written = write_snapshot(payload, path)
    except SnapshotConflictError:
        # Append-only: a different capture for the same day gets its own file.
        fallback = path.with_name(f"{path.stem}-{adapter.run_id}{path.suffix}")
        written = write_snapshot(payload, fallback)
        print(f"note: {path} already holds different evidence; wrote {written}")
    print(f"snapshot: {written}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
