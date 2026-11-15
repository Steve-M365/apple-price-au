"""Validation CLI tests: clean tree, hard failures, warnings and JSON output.

Every hard-failure test asserts both the exit code and that the message names
the file and the field, because that is what someone triaging CI actually
needs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.models import Offer
from scripts.validation import validate_data
from tests.unit.test_models import (
    valid_candidate,
    valid_deal,
    valid_offer,
    valid_product,
    valid_seller,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _clean_tree(tmp_path: Path) -> Path:
    """A minimal but valid data tree with an existing adapter module."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    collectors = tmp_path / "scripts" / "collectors"
    collectors.mkdir(parents=True)
    (collectors / "apple_au.py").write_text(
        "# stub adapter for validation tests\n", encoding="utf-8"
    )

    _write_json(data_dir / "products.json", {"products": [valid_product()]})
    _write_json(data_dir / "sellers.json", {"sellers": [valid_seller()]})
    _write_json(data_dir / "latest_offers.json", {"offers": [valid_offer()]})
    return data_dir


def _run(data_dir: Path, *extra: str) -> tuple[int, str]:
    import contextlib
    import io

    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        code = validate_data.main(["--data-dir", str(data_dir), *extra])
    return code, stream.getvalue()


def test_clean_tree_exits_zero(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    code, output = _run(data_dir)

    assert code == 0
    assert "HARD FAILURES: none" in output
    assert "RESULT: PASS" in output
    assert "products=1" in output
    assert "sellers=1" in output


def test_json_report_has_the_documented_shape(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    code, output = _run(data_dir, "--json")

    assert code == 0
    report: dict[str, Any] = json.loads(output)
    assert set(report) == {"ok", "hard", "warnings", "stats", "generated_at"}
    assert report["ok"] is True
    assert report["hard"] == []
    assert isinstance(report["warnings"], list)
    assert report["stats"]["products"] == 1
    assert report["stats"]["offers"] == 1
    assert report["stats"]["sellers"] == 1
    assert report["generated_at"].endswith("Z")


def test_unknown_product_reference_fails_and_names_file_and_field(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    offer = valid_offer(product_id="ghost-product")
    _write_json(data_dir / "latest_offers.json", {"offers": [offer]})

    code, output = _run(data_dir)

    assert code == 1
    assert "HARD" in output
    assert "latest_offers.json[0].product_id" in output
    assert "ghost-product" in output


def test_unknown_seller_reference_fails(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    offer = valid_offer(seller_id="ghost-seller")
    _write_json(data_dir / "latest_offers.json", {"offers": [offer]})

    code, output = _run(data_dir)

    assert code == 1
    assert "latest_offers.json[0].seller_id" in output
    assert "ghost-seller" in output
    assert "sellers.json" in output


def test_duplicate_offer_ids_fail(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    offer = valid_offer()
    _write_json(data_dir / "latest_offers.json", {"offers": [offer, dict(offer)]})

    code, output = _run(data_dir)

    assert code == 1
    assert "duplicate_id" in output
    assert "duplicate id" in output


@pytest.mark.parametrize("missing_field", ["captured_at", "source_hash", "verification_status"])
def test_offer_missing_freshness_field_fails(tmp_path: Path, missing_field: str) -> None:
    data_dir = _clean_tree(tmp_path)
    offer = valid_offer()
    offer.pop(missing_field)
    _write_json(data_dir / "latest_offers.json", {"offers": [offer]})

    code, output = _run(data_dir)

    assert code == 1
    assert f"latest_offers.json[0].{missing_field}" in output


def test_negative_price_fails_with_field_name(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    _write_json(data_dir / "latest_offers.json", {"offers": [valid_offer(price_aud="-10.00")]})

    code, output = _run(data_dir)

    assert code == 1
    assert "latest_offers.json[0].price_aud" in output
    assert "greater than or equal to 0" in output


def test_missing_dataset_is_a_warning_not_a_crash(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    code, output = _run(data_dir)

    assert code == 0
    assert "missing_dataset" in output
    assert "latest_offers.json" in output
    assert "RESULT: PASS" in output


def test_strict_mode_promotes_warnings_to_failures(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    code, output = _run(data_dir, "--strict")

    assert code == 1
    assert "RESULT: FAIL" in output
    assert "strict" in output


def test_enabled_seller_with_missing_adapter_module_fails(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    (tmp_path / "scripts" / "collectors" / "apple_au.py").unlink()

    code, output = _run(data_dir)

    assert code == 1
    assert "sellers.json[0].adapter_module" in output
    assert "no such module file exists" in output


def test_offer_referencing_a_disabled_seller_fails(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    seller = valid_seller(
        enabled=False,
        enabled_reason="Phase 2: marketplace provenance not signed off",
        adapter_module="",
        collection_method="curated",
        rate_limit_rps=0.0,
    )
    _write_json(data_dir / "sellers.json", {"sellers": [seller]})

    code, output = _run(data_dir)

    assert code == 1
    assert "latest_offers.json[0].seller_id" in output
    assert "disabled" in output


def test_deal_referencing_an_unknown_offer_fails(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    deal = valid_deal(offer_id="ghost-offer")
    _write_json(data_dir / "deals" / "latest.json", {"deals": [deal]})

    code, output = _run(data_dir)

    assert code == 1
    assert "deals/latest.json[0].offer_id" in output
    assert "ghost-offer" in output


def test_duplicate_ids_across_offer_datasets_fail(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    offer = valid_offer()
    gift_card = {
        "offer_id": offer["offer_id"],
        "card_type": "apple_gift_card",
        "seller_id": "apple_au",
        "loyalty_program": "None",
        "channel": "online",
        "face_value_aud": "500.00",
        "purchase_price_aud": "500.00",
        "effective_value_aud": "500.00",
        "effective_discount_pct": "0.00",
        "starts_at": "2026-11-15T00:00:00Z",
        "ends_at": "2026-12-15T00:00:00Z",
        "source_url": "https://www.apple.com/au/shop/buy-gift-card",
        "verification_status": "indicative",
        "risks": ["No discount; reference record only"],
    }
    _write_json(data_dir / "gift_cards" / "offers.json", {"gift_cards": [gift_card]})

    code, output = _run(data_dir)

    assert code == 1
    assert "duplicate_id" in output
    assert "gift_cards/offers.json" in output


def test_stale_offer_warns_against_freshness_config(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    (config / "freshness.yml").write_text(
        "default_max_age_hours: 12\nby_seller_type:\n  apple_direct: 6\n",
        encoding="utf-8",
    )
    now = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
    report = validate_data.run_validation(data_dir, now=now)

    stale = [issue for issue in report.warnings if issue.code == "stale_offer"]
    assert len(stale) == 1
    assert "freshness budget" in stale[0].message
    assert report.ok(strict=False) is True


def test_review_queue_growth_warns(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    (config / "validation.yml").write_text("review_queue_warn_threshold: 1\n", encoding="utf-8")
    candidates = [valid_candidate(candidate_id=f"cand-{index}") for index in range(2)]
    _write_json(data_dir / "matching" / "review_queue.json", {"candidates": candidates})

    report = validate_data.run_validation(data_dir)

    growth = [issue for issue in report.warnings if issue.code == "review_queue_growth"]
    assert len(growth) == 1
    assert "above the 1 threshold" in growth[0].message


def test_corrupt_json_is_a_hard_failure_not_a_crash(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    (data_dir / "latest_offers.json").write_text("{not json", encoding="utf-8")

    code, output = _run(data_dir)

    assert code == 1
    assert "invalid_json" in output
    assert "latest_offers.json" in output


def test_unexpected_exception_returns_exit_code_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(validate_data, "run_validation", boom)
    code = validate_data.main(["--data-dir", str(data_dir)])

    assert code == 2


def test_offer_dump_shape_is_stable_for_writers(tmp_path: Path) -> None:
    data_dir = _clean_tree(tmp_path)
    payload = json.loads((data_dir / "latest_offers.json").read_text(encoding="utf-8"))
    offer = Offer.model_validate(payload["offers"][0])
    dumped = json.loads(offer.model_dump_json())

    assert dumped["price_aud"] == 1999.0
    assert isinstance(dumped["price_aud"], float)
    assert dumped["captured_at"] == "2026-11-15T08:00:00Z"
    assert dumped["match_status"] == "auto_published"
    assert Offer.model_validate_json(offer.model_dump_json()) == offer
