"""Invariants for the generated Apple AU catalogue — APA-014.

The catalogue is produced by ``scripts.catalogue`` from live Apple AU pages, so
these tests guard the properties that make that output trustworthy rather than a
snapshot of today's prices:

* the seed catalogue really has grown past the hand-entered 20;
* every row still traces to an Apple AU URL and a committed evidence file;
* the identifiers follow the documented slug shape and are unique;
* all eight in-scope categories are represented;
* a price is a positive amount of dollars and cents, never a string or a guess;
* education pricing eligibility is confined to the Mac and iPad families, whose
  rows are the only ones the Apple Education Store sells;
* one part number never appears twice, so the collector cannot price the wrong
  configuration.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

PRODUCTS_RELATIVE = Path("data/products.json")
EVIDENCE_RELATIVE = Path("data/evidence/apple_au")

#: The catalogue is expected to be materially larger than the 20-row seed.
MINIMUM_PRODUCTS = 40

#: Every in-scope Apple AU category. AppleCare is tracked as a product category
#: in the model but is not sold as a catalogue configuration, so it is not
#: required here.
REQUIRED_CATEGORIES = frozenset(
    {"mac", "ipad", "iphone", "watch", "airpods", "tv", "homepod", "accessory"}
)

#: ``apple-<model>-<screen>-<chip>-<memory>-<storage>-<colour>`` with optional
#: axes omitted and every component slugified (``6.3-inch`` -> ``6-3-inch``).
_SLUG_RE = re.compile(r"^apple(?:-[a-z0-9]+)+$")

_EDUCATION_CATEGORIES = frozenset({"mac", "ipad"})


@pytest.fixture(scope="module")
def catalogue(repo_root: Path) -> list[dict[str, Any]]:
    payload = json.loads((repo_root / PRODUCTS_RELATIVE).read_text(encoding="utf-8"))
    products = payload["products"]
    assert isinstance(products, list) and products
    return products


@pytest.fixture(scope="module")
def evidence_dir(repo_root: Path) -> Path:
    directory = repo_root / EVIDENCE_RELATIVE
    assert directory.is_dir(), directory
    return directory


def test_catalogue_has_at_least_forty_products(catalogue: list[dict[str, Any]]) -> None:
    assert len(catalogue) >= MINIMUM_PRODUCTS, (
        f"only {len(catalogue)} products; the expansion target is {MINIMUM_PRODUCTS}"
    )


def test_product_ids_are_unique(catalogue: list[dict[str, Any]]) -> None:
    counts = Counter(product["product_id"] for product in catalogue)
    duplicates = sorted(product_id for product_id, count in counts.items() if count > 1)
    assert not duplicates, f"duplicate product_id: {', '.join(duplicates)}"


def test_product_ids_match_the_documented_slug_format(catalogue: list[dict[str, Any]]) -> None:
    bad = [
        product["product_id"] for product in catalogue if not _SLUG_RE.match(product["product_id"])
    ]
    assert not bad, f"product_id is not a documented slug: {', '.join(bad)}"


def test_catalogue_is_sorted_by_category_then_product_id(catalogue: list[dict[str, Any]]) -> None:
    keys = [(product["category"], product["product_id"]) for product in catalogue]
    assert keys == sorted(keys), "products.json must sort by category then product_id"


def test_every_category_is_present(catalogue: list[dict[str, Any]]) -> None:
    present = {product["category"] for product in catalogue}
    missing = sorted(REQUIRED_CATEGORIES - present)
    assert not missing, f"no product in categor{'y' if len(missing) == 1 else 'ies'}: {missing}"


def test_every_product_has_a_verified_rrp_and_an_evidence_file(
    catalogue: list[dict[str, Any]], evidence_dir: Path
) -> None:
    failures: list[str] = []
    for product in catalogue:
        product_id = product["product_id"]
        url = product.get("apple_au_url")
        if not isinstance(url, str) or not url.startswith("https://www.apple.com/au/"):
            failures.append(f"{product_id}: apple_au_url {url!r}")
        if product.get("rrp_verification") != "verified":
            failures.append(f"{product_id}: rrp_verification {product.get('rrp_verification')!r}")
        path = evidence_dir / f"{product_id}.json"
        if not path.is_file():
            failures.append(f"{product_id}: missing {path.name}")
            continue
        evidence = json.loads(path.read_text(encoding="utf-8"))
        if evidence.get("product_id") != product_id:
            failures.append(f"{product_id}: evidence names {evidence.get('product_id')!r}")
    assert not failures, "; ".join(failures)


def test_evidence_records_a_positive_price_matching_the_catalogue(
    catalogue: list[dict[str, Any]], evidence_dir: Path
) -> None:
    failures: list[str] = []
    for product in catalogue:
        product_id = product["product_id"]
        evidence = json.loads((evidence_dir / f"{product_id}.json").read_text(encoding="utf-8"))
        captured = evidence.get("price_numeric")
        if not isinstance(captured, (int, float)) or isinstance(captured, bool) or captured <= 0:
            failures.append(f"{product_id}: captured price {captured!r}")
        elif float(captured) != float(product["apple_rrp_aud"]):
            failures.append(
                f"{product_id}: catalogue {product['apple_rrp_aud']} != capture {captured}"
            )
    assert not failures, "; ".join(failures)


def test_prices_are_positive_with_two_decimals(catalogue: list[dict[str, Any]]) -> None:
    failures: list[str] = []
    for product in catalogue:
        price = product.get("apple_rrp_aud")
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            failures.append(f"{product['product_id']}: {price!r} is not a number")
            continue
        if price <= 0:
            failures.append(f"{product['product_id']}: price {price} is not positive")
        if round(float(price), 2) != float(price):
            failures.append(f"{product['product_id']}: price {price!r} carries fractions of a cent")
    assert not failures, "; ".join(failures)


def test_education_eligibility_is_confined_to_mac_and_ipad(
    catalogue: list[dict[str, Any]],
) -> None:
    wrong = [
        product["product_id"]
        for product in catalogue
        if product.get("education_eligible") and product["category"] not in _EDUCATION_CATEGORIES
    ]
    assert not wrong, f"education_eligible on a non-Mac/iPad row: {', '.join(wrong)}"


def test_every_mac_and_ipad_row_is_education_eligible(catalogue: list[dict[str, Any]]) -> None:
    missing = [
        product["product_id"]
        for product in catalogue
        if product["category"] in _EDUCATION_CATEGORIES and not product.get("education_eligible")
    ]
    assert not missing, f"Mac/iPad row not marked education eligible: {', '.join(missing)}"


def test_part_numbers_are_never_duplicated(catalogue: list[dict[str, Any]]) -> None:
    skus = [
        product["apple_sku"].strip()
        for product in catalogue
        if isinstance(product.get("apple_sku"), str) and product["apple_sku"].strip()
    ]
    counts = Counter(skus)
    duplicates = sorted(sku for sku, count in counts.items() if count > 1)
    assert not duplicates, f"part number on more than one row: {', '.join(duplicates)}"


def test_discontinued_products_are_marked_superseded_not_deleted(
    catalogue: list[dict[str, Any]],
) -> None:
    statuses = {product.get("status") for product in catalogue}
    assert statuses <= {"active", "superseded"}, f"unexpected status values: {statuses}"
