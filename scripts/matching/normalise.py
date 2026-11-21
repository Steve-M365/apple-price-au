"""Deterministic helpers for matching seller listings to catalogue products.

The functions here are intentionally dependency-light and side-effect free:
the same title and configuration always produce the same normalised values,
similarity score and configuration diff.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

__all__ = [
    "configuration_diff",
    "configuration_similarity",
    "normalise_title",
    "title_similarity",
]

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_STORAGE = re.compile(r"\b(\d+)\s*(gb|tb)\b", re.I)
_MEMORY = re.compile(r"\b(\d+)\s*gb\s+(?:unified\s+)?memory\b", re.I)
_COLOUR = re.compile(
    r"\b(midnight|starlight|blue|black|white|silver|space\s+black|titanium)\b",
    re.I,
)
_CONNECTIVITY = re.compile(r"\b(wi-?fi|cellular|gps\s*\+\s*cellular)\b", re.I)
_SCREEN = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:inch|in|\")\b", re.I)


def normalise_title(value: str) -> str:
    """Return a stable, lowercase token form of a product title."""
    text = value.strip().lower()
    text = text.replace("&", " and ")
    text = _NON_ALPHANUMERIC.sub(" ", text)
    return " ".join(text.split())


def extract_configuration(value: str) -> dict[str, str]:
    """Extract conservative configuration signals from a listing title.

    Only unambiguous values are returned. The matcher must never infer an
    attribute that is absent from the source title.
    """
    configuration: dict[str, str] = {}

    if storage := _STORAGE.search(value):
        configuration["storage"] = f"{storage.group(1)}{storage.group(2).lower()}"

    if memory := _MEMORY.search(value):
        configuration["memory"] = f"{memory.group(1)}GB"

    if colour := _COLOUR.search(value):
        configuration["colour"] = " ".join(colour.group(1).lower().split())

    if connectivity := _CONNECTIVITY.search(value):
        configuration["connectivity"] = " ".join(connectivity.group(1).lower().split())

    if screen := _SCREEN.search(value):
        configuration["screen_size"] = f"{screen.group(1)} inch"

    return configuration


def configuration_diff(
    listing_configuration: dict[str, str],
    product_configuration: dict[str, str],
) -> list[str]:
    """Return every mismatched configuration field, in stable order."""
    differences: list[str] = []
    keys = sorted(set(listing_configuration) | set(product_configuration))
    for key in keys:
        listing_value = listing_configuration.get(key)
        product_value = product_configuration.get(key)
        if listing_value != product_value:
            differences.append(f"{key}: {listing_value!r} != {product_value!r}")
    return differences


def configuration_similarity(
    listing_configuration: dict[str, str],
    product_configuration: dict[str, str],
) -> float:
    """Return the proportion of unioned configuration keys that agree."""
    keys = set(listing_configuration) | set(product_configuration)
    if not keys:
        return 0.0
    matches = sum(
        listing_configuration.get(key) == product_configuration.get(key) for key in keys
    )
    return matches / len(keys)


def title_similarity(left: str, right: str) -> float:
    """Return a deterministic 0-1 similarity score for two product titles."""
    return SequenceMatcher(None, normalise_title(left), normalise_title(right)).ratio()
