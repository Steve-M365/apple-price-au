"""Tests for :mod:`scripts.normalise` — APA-013.

The fixtures are the committed catalogue itself: every one of the 20 real
products in ``data/products.json`` has a live-captured Apple AU JSON-LD title in
``data/evidence/apple_au/<product_id>.json``. Feeding that raw title back
through the normaliser must reproduce the exact ``product_id``, canonical
``name`` and configuration already in the catalogue, otherwise the collectors
and the catalogue would disagree about what a product is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.normalise import (
    NormalisationCollision,
    NormalisedProduct,
    UnrecognisedTitle,
    normalise,
    slugify,
)

EVIDENCE_RELATIVE = Path("data/evidence/apple_au")
PRODUCTS_RELATIVE = Path("data/products.json")

#: The Magic Keyboard's catalogue entry omits ``chip`` although its id carries
#: ``m4``; the title names the chip, so the normaliser records it. Every other
#: catalogue key still has to match exactly.
KNOWN_CONFIGURATION_GAPS = {
    "apple-magic-keyboard-ipad-air-11-inch-m4-us-english-white": {"chip"},
}

#: The first ``name`` in the JSON-LD fragment is the Product name. The captured
#: fragment is deliberately truncated with an ellipsis, so it is parsed with a
#: regex rather than as JSON.
_JSONLD_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]*)"')

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _products(repo_root: Path) -> list[dict[str, Any]]:
    payload = json.loads((repo_root / PRODUCTS_RELATIVE).read_text(encoding="utf-8"))
    products = payload["products"]
    assert isinstance(products, list) and products
    return products


def _evidence_titles(repo_root: Path) -> dict[str, str]:
    """Map ``product_id`` to the raw JSON-LD Product name in its evidence file."""
    titles: dict[str, str] = {}
    directory = repo_root / EVIDENCE_RELATIVE
    if not directory.is_dir():
        return titles
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("_"):
            continue
        evidence = json.loads(path.read_text(encoding="utf-8"))
        fragment = evidence.get("raw_fragment")
        product_id = evidence.get("product_id")
        if not isinstance(fragment, str) or not isinstance(product_id, str):
            continue
        match = _JSONLD_NAME_RE.search(fragment)
        if match is not None:
            titles[product_id] = match.group(1)
    return titles


@pytest.fixture(scope="module")
def catalogue(repo_root: Path) -> list[dict[str, Any]]:
    return _products(repo_root)


@pytest.fixture(scope="module")
def evidence_titles(repo_root: Path) -> dict[str, str]:
    return _evidence_titles(repo_root)


@pytest.fixture(scope="module")
def known(catalogue: list[dict[str, Any]]) -> dict[str, str]:
    return {product["product_id"]: product["name"] for product in catalogue}


def test_evidence_coverage(
    catalogue: list[dict[str, Any]], evidence_titles: dict[str, str]
) -> None:
    """Report exactly how many of the catalogue products have a captured title."""
    missing = [
        product["product_id"]
        for product in catalogue
        if product["product_id"] not in evidence_titles
    ]
    covered = len(catalogue) - len(missing)
    assert not missing, (
        f"covered {covered}/{len(catalogue)}; no captured title for: {', '.join(missing)}"
    )
    assert covered == len(catalogue) == 20


def test_real_page_title_reproduces_catalogue(
    catalogue: list[dict[str, Any]],
    evidence_titles: dict[str, str],
    known: dict[str, str],
) -> None:
    """Every real Apple page title must round-trip to its committed identity.

    A product with no captured title would be counted as uncovered rather than
    silently passing; :func:`test_evidence_coverage` asserts the count is 20/20.
    """
    checked = 0
    failures: list[str] = []
    for product in catalogue:
        raw = evidence_titles.get(product["product_id"])
        if raw is None:
            continue
        checked += 1
        normalised = normalise(raw, catalogue=known)
        if normalised.product_id != product["product_id"]:
            failures.append(
                f"{product['product_id']}: raw title normalised to {normalised.product_id}"
            )
        elif normalised.name != product["name"]:
            failures.append(
                f"{product['product_id']}: name {normalised.name!r} != {product['name']!r}"
            )
    assert not failures, f"covered {checked}/{len(catalogue)} titles; " + "; ".join(failures)


def test_real_page_title_reproduces_configuration(
    catalogue: list[dict[str, Any]],
    evidence_titles: dict[str, str],
    known: dict[str, str],
) -> None:
    """Configuration agrees with the catalogue, except documented gaps."""
    checked = 0
    failures: list[str] = []
    for product in catalogue:
        raw = evidence_titles.get(product["product_id"])
        if raw is None:
            continue
        checked += 1
        normalised = normalise(raw, catalogue=known)
        expected = dict(product["configuration"])
        actual = dict(normalised.configuration)
        for gap in KNOWN_CONFIGURATION_GAPS.get(product["product_id"], set()):
            expected.pop(gap, None)
            actual.pop(gap, None)
        if actual != expected:
            failures.append(f"{product['product_id']}: {actual} != {expected}")
    assert not failures, f"covered {checked}/{len(catalogue)} titles; " + "; ".join(failures)


def test_magic_keyboard_chip_is_recorded(
    catalogue: list[dict[str, Any]], evidence_titles: dict[str, str], known: dict[str, str]
) -> None:
    """The id carries ``m4``, the title names it, and the catalogue now agrees."""
    product_id = "apple-magic-keyboard-ipad-air-11-inch-m4-us-english-white"
    product = next(product for product in catalogue if product["product_id"] == product_id)
    normalised = normalise(evidence_titles[product_id], catalogue=known)
    assert normalised.product_id == product_id
    assert normalised.configuration["chip"] == "Apple M4"
    # The catalogue previously omitted this key while the id encoded it; the
    # normaliser was right and the data was backfilled on 2026-11-15.
    assert product["configuration"]["chip"] == "Apple M4"


# --------------------------------------------------------------------------
# Apple's spelling variants
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_id"),
    [
        (
            "MacBook Air, 13\u2011inch, M5 Chip, 10-core CPU, 8-core GPU, "
            "Midnight, 16GB memory, 512GB storage",
            "apple-macbook-air-13-inch-m5-16gb-512gb-midnight",
        ),
        (
            "MacBook Air, 13 inch, M5 Chip, 10-core CPU, 8-core GPU, "
            "Midnight, 16GB memory, 512GB storage",
            "apple-macbook-air-13-inch-m5-16gb-512gb-midnight",
        ),
        (
            'MacBook Air, 13", M5 Chip, 10-core CPU, 8-core GPU, '
            "Midnight, 16GB memory, 512GB storage",
            "apple-macbook-air-13-inch-m5-16gb-512gb-midnight",
        ),
        (
            "MacBook Air, 13\u2013inch, M5 Chip, 10-core CPU, 8-core GPU, "
            "Midnight, 16 GB memory, 512 GB storage",
            "apple-macbook-air-13-inch-m5-16gb-512gb-midnight",
        ),
        (
            "macbook air 13-inch m5 chip 10-core cpu 8-core gpu 16gb memory 512gb storage midnight",
            "apple-macbook-air-13-inch-m5-16gb-512gb-midnight",
        ),
        (
            "11\u2011inch iPad Air Wi\u2011Fi 128GB \u2014 Space Gray",
            "apple-ipad-air-11-inch-m4-128gb-space-grey",
        ),
        (
            "11-inch iPad Air WiFi 128 GB \u2014 Space Grey",
            "apple-ipad-air-11-inch-m4-128gb-space-grey",
        ),
        (
            "Apple\u00a0Watch Series\u00a012 GPS, 42-mm Space Gray Aluminium Case "
            "with After Dark Black Nike Sport Band",
            "apple-watch-series-12-42mm-space-grey-aluminium-gps",
        ),
        ("HomePod\u00a0mini\u00a02 \u2014 Midnight", "apple-homepod-mini-2-midnight"),
        ("AirPods\u00a0Max\u00a02 \u2014 Midnight", "apple-airpods-max-2-midnight"),
        ("iPhone\u00a017 256GB Black", "apple-iphone-17-6-3-inch-256gb-black"),
        (
            "MacBook Pro, 14-inch, M6 Chip, 12-core CPU, 12-core GPU, "
            "Space Black, Standard display, 16GB memory, 1TB storage",
            "apple-macbook-pro-14-inch-m6-16gb-1tb-space-black",
        ),
    ],
)
def test_variant_spellings(raw: str, expected_id: str) -> None:
    assert normalise(raw, catalogue={}).product_id == expected_id


@pytest.mark.parametrize(
    ("raw", "expected_id"),
    [
        (
            "Magic Keyboard for iPad Air 11\u2011inch (M4) \u2014 US English \u2014 White",
            "apple-magic-keyboard-ipad-air-11-inch-m4-us-english-white",
        ),
        ("iPad Wi\u2011Fi 128GB \u2014 Blue", "apple-ipad-11-inch-a16-128gb-blue"),
        ("iPad WiFi 128gb \u2014 blue", "apple-ipad-11-inch-a16-128gb-blue"),
    ],
)
def test_accessory_and_ipad_spellings(raw: str, expected_id: str) -> None:
    assert normalise(raw, catalogue={}).product_id == expected_id


# --------------------------------------------------------------------------
# Determinism, idempotency and slug hygiene
# --------------------------------------------------------------------------


def test_normalisation_is_deterministic(
    catalogue: list[dict[str, Any]], evidence_titles: dict[str, str], known: dict[str, str]
) -> None:
    for product in catalogue:
        raw = evidence_titles.get(product["product_id"])
        if raw is None:  # pragma: no cover - fixture note
            continue
        first = normalise(raw, catalogue=known)
        second = normalise(raw, catalogue=known)
        assert first == second


def test_idempotent_on_canonical_names(
    catalogue: list[dict[str, Any]], evidence_titles: dict[str, str], known: dict[str, str]
) -> None:
    """Re-normalising the readable name is a no-op for the identity."""
    checked = 0
    for product in catalogue:
        raw = evidence_titles.get(product["product_id"])
        if raw is None:  # pragma: no cover - fixture note
            continue
        checked += 1
        first = normalise(raw, catalogue=known)
        second = normalise(first.name, catalogue=known)
        assert second.product_id == first.product_id, first.name
        assert second.name == first.name, first.name
        third = normalise(second.name, catalogue=known)
        assert third == second, first.name
    assert checked == len(catalogue)


@pytest.mark.parametrize(
    ("raw", "expected_id"),
    [
        (
            "MacBook Air 13-inch M5 16GB 512GB Midnight",
            "apple-macbook-air-13-inch-m5-16gb-512gb-midnight",
        ),
        ("iPad 11-inch A16 128GB Blue (Wi-Fi)", "apple-ipad-11-inch-a16-128gb-blue"),
        (
            "Apple Watch Series 12 42mm Space Grey Aluminium GPS "
            "(After Dark Black Nike Sport Band)",
            "apple-watch-series-12-42mm-space-grey-aluminium-gps",
        ),
    ],
)
def test_canonical_name_is_stable(raw: str, expected_id: str) -> None:
    assert normalise(raw, catalogue={}).product_id == expected_id


def test_slug_hygiene(catalogue: list[dict[str, Any]]) -> None:
    for product in catalogue:
        product_id = product["product_id"]
        assert product_id.startswith("apple-"), product_id
        assert product_id == product_id.lower(), product_id
        assert "--" not in product_id, product_id
        assert _SLUG_RE.match(product_id), product_id


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Space Black", "space-black"),
        ("Space Grey", "space-grey"),
        ("6.3-inch", "6-3-inch"),
        ("4th generation", "4th-generation"),
        ("Wi-Fi", "wi-fi"),
        ("  --Hello, World--  ", "hello-world"),
    ],
)
def test_slugify(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


def test_slugify_never_leaves_empty_parts() -> None:
    for raw in ["...", " - ", "M5", "16 GB"]:
        slug = slugify(raw)
        assert slug == slug.strip("-")
        assert "--" not in slug


# --------------------------------------------------------------------------
# The Watch bundle rule
# --------------------------------------------------------------------------


def test_watch_id_is_the_case_not_the_band(
    evidence_titles: dict[str, str], known: dict[str, str]
) -> None:
    raw = evidence_titles["apple-watch-series-12-42mm-space-grey-aluminium-gps"]
    normalised = normalise(raw, catalogue=known)
    assert normalised.product_id == "apple-watch-series-12-42mm-space-grey-aluminium-gps"
    assert normalised.configuration["band"] == "After Dark Black Nike Sport Band"
    assert "nike" not in normalised.product_id
    assert "band" not in normalised.product_id
    assert "After Dark Black Nike Sport Band" in normalised.name


def test_watch_different_band_is_a_collision(known: dict[str, str]) -> None:
    """Same case, different band is a different bundle: refuse to merge."""
    with pytest.raises(NormalisationCollision) as excinfo:
        normalise(
            "Apple Watch Series 12 GPS, 42-mm Space Grey Aluminium Case with Black Sport Band",
            catalogue=known,
        )
    assert "After Dark Black Nike Sport Band" in str(excinfo.value)
    assert "Black Sport Band" in str(excinfo.value)


def test_watch_without_band_is_incomplete(known: dict[str, str]) -> None:
    with pytest.raises(UnrecognisedTitle, match="band"):
        normalise(
            "Apple Watch Series 12 GPS, 42-mm Space Grey Aluminium Case",
            catalogue=known,
        )


# --------------------------------------------------------------------------
# Failures are loud
# --------------------------------------------------------------------------


def test_collision_names_both_titles() -> None:
    raw = 'iMac, 24", M6 Chip, 10-core CPU, 10-core GPU, Blue, Stand, 16GB memory, 256GB storage'
    catalogue = {"apple-imac-24-inch-m6-16gb-256gb-blue": "iMac 24-inch M6 24GB 1TB Blue"}
    with pytest.raises(NormalisationCollision) as excinfo:
        normalise(raw, catalogue=catalogue)
    message = str(excinfo.value)
    assert "apple-imac-24-inch-m6-16gb-256gb-blue" in message
    assert "iMac 24-inch M6 24GB 1TB Blue" in message
    assert "iMac 24-inch M6 16GB 256GB Blue" in message


def test_matching_catalogue_entry_is_not_a_collision() -> None:
    raw = "iPad Wi-Fi 128GB \u2014 Blue"
    catalogue = {"apple-ipad-11-inch-a16-128gb-blue": "iPad 11-inch A16 128GB Blue (Wi-Fi)"}
    assert normalise(raw, catalogue=catalogue).product_id == "apple-ipad-11-inch-a16-128gb-blue"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("Apple Vision Pro 512GB", "unknown Apple product family"),
        ("AirTag 4 pack", "unknown Apple product family"),
        ("", "empty title"),
        ("   ", "empty title"),
        ("MacBook Air 13-inch M5 16GB", "ambiguous capacity"),
        ("MacBook Air 13-inch M5", "missing required"),
        ("iPad", "missing required"),
    ],
)
def test_unrecognised_titles_raise(raw: str, reason: str) -> None:
    with pytest.raises(UnrecognisedTitle) as excinfo:
        normalise(raw, catalogue={})
    assert reason in str(excinfo.value)
    if raw.strip():
        assert raw in str(excinfo.value)


def test_raw_title_is_preserved() -> None:
    raw = "Nonsense Product 128GB"
    normalised_input = "iPad Wi-Fi 128GB \u2014 Blue"
    result: NormalisedProduct = normalise(normalised_input, catalogue={})
    assert result.source_title == normalised_input
    with pytest.raises(UnrecognisedTitle) as excinfo:
        normalise(raw, catalogue={})
    assert excinfo.value.raw_title == raw


# --------------------------------------------------------------------------
# Configuration shape
# --------------------------------------------------------------------------


def test_absent_axes_are_omitted(evidence_titles: dict[str, str], known: dict[str, str]) -> None:
    mac_mini = normalise(evidence_titles["apple-mac-mini-m6-16gb-256gb"], catalogue=known)
    assert "screen_size" not in mac_mini.configuration
    assert "colour" not in mac_mini.configuration

    ipad = normalise(evidence_titles["apple-ipad-11-inch-a16-128gb-blue"], catalogue=known)
    assert "memory" not in ipad.configuration
    assert ipad.configuration["storage"] == "128GB"

    pencil = normalise("Apple Pencil Pro", catalogue={})
    assert pencil.configuration == {"type": "stylus"}
    assert pencil.product_id == "apple-pencil-pro"


def test_connectivity_is_not_in_an_ipad_id(
    evidence_titles: dict[str, str], known: dict[str, str]
) -> None:
    """The catalogue treats Wi-Fi as the iPad baseline; it is not an id axis."""
    normalised = normalise(
        evidence_titles["apple-ipad-pro-11-inch-m5-256gb-space-black"], catalogue=known
    )
    assert normalised.configuration["connectivity"] == "Wi-Fi"
    assert "wifi" not in normalised.product_id
    assert "wi-fi" not in normalised.product_id


def test_values_are_always_strings(evidence_titles: dict[str, str], known: dict[str, str]) -> None:
    for product_id, raw in evidence_titles.items():
        normalised = normalise(raw, catalogue=known)
        assert all(isinstance(key, str) for key in normalised.configuration)
        assert all(isinstance(value, str) for value in normalised.configuration.values())
        assert normalised.product_id == product_id
