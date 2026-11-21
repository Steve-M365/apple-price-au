"""Tests for deterministic matching normalisation helpers."""

from __future__ import annotations

from scripts.matching.normalise import (
    configuration_diff,
    configuration_similarity,
    extract_configuration,
    normalise_title,
    title_similarity,
)


def test_normalise_title_is_deterministic() -> None:
    assert normalise_title(" MacBook Air 13” M4 — 16GB ") == "macbook air 13 m4 16gb"
    assert normalise_title("MacBook Air 13 M4 16GB") == "macbook air 13 m4 16gb"


def test_extract_configuration_only_uses_present_values() -> None:
    configuration = extract_configuration("iPhone 16 Pro 256GB Black Titanium")
    assert configuration["storage"] == "256gb"
    assert configuration["colour"] == "titanium"
    assert "memory" not in configuration


def test_configuration_diff_lists_every_mismatch() -> None:
    differences = configuration_diff(
        {"storage": "256gb", "colour": "black"},
        {"storage": "512gb", "colour": "black", "memory": "16GB"},
    )
    assert differences == [
        "memory: None != '16GB'",
        "storage: '256gb' != '512gb'",
    ]


def test_configuration_similarity_handles_union() -> None:
    assert configuration_similarity({"storage": "256gb"}, {"storage": "256gb"}) == 1.0
    assert configuration_similarity({}, {"storage": "256gb"}) == 0.0


def test_title_similarity_is_stable() -> None:
    assert title_similarity("iPhone 16 Pro 256GB", "iPhone 16 Pro 256GB") == 1.0
    assert title_similarity("iPhone 16 Pro", "iPhone 15 Pro") < 1.0
