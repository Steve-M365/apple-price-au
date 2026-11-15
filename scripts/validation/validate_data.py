"""Validate every committed Orchard dataset against the domain models.

Usage::

    python -m scripts.validation.validate_data
    python -m scripts.validation.validate_data --data-dir data --json --strict

Hard failures exit 1 and must block a deploy: an unparseable record, a duplicate
id, a dangling ``product_id`` or ``seller_id``, a deal pointing at an unknown
offer, an offer missing its captured timestamp, source hash or verification
verdict, or an enabled seller whose adapter module does not exist.

Soft failures are warnings: stale offers measured against
``config/freshness.yml``, a growing match review queue, and enabled sellers
whose authorised-reseller status is still unknown. ``--strict`` promotes
warnings to failures for pre-release validation.

A dataset that does not exist yet is treated as empty with a warning, never as a
crash.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

import yaml
from pydantic import BaseModel, ValidationError

from scripts.models.common import format_utc
from scripts.models.deal import Deal
from scripts.models.education import EducationOffer
from scripts.models.enums import CollectionMethod, MatchStatus
from scripts.models.gift_card import GiftCardOffer
from scripts.models.matching import MatchCandidate
from scripts.models.offer import Offer
from scripts.models.product import Product
from scripts.models.run import RunManifest
from scripts.models.seller import Seller
from scripts.validation.freshness import (
    FreshnessConfig,
    effective_verification,
    load_freshness_config,
    measure_age_hours,
)

DEFAULT_REVIEW_QUEUE_THRESHOLD: Final = 50

__all__ = [
    "DATASETS",
    "Issue",
    "Loaded",
    "Report",
    "main",
    "run_validation",
]


@dataclass(frozen=True, slots=True)
class Issue:
    """One validation finding, hard or soft."""

    code: str
    file: str
    field: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "file": self.file,
            "field": self.field,
            "message": self.message,
        }

    def line(self) -> str:
        location = self.field if self.field not in ("", "-") else self.file
        return f"{location}: {self.message} [{self.code}]"


@dataclass(slots=True)
class Report:
    """The complete validation result."""

    hard: list[Issue]
    warnings: list[Issue]
    stats: dict[str, int]
    generated_at: datetime

    def ok(self, *, strict: bool) -> bool:
        if self.hard:
            return False
        return not (strict and self.warnings)

    def as_dict(self, *, strict: bool) -> dict[str, object]:
        return {
            "ok": self.ok(strict=strict),
            "hard": [issue.as_dict() for issue in self.hard],
            "warnings": [issue.as_dict() for issue in self.warnings],
            "stats": dict(self.stats),
            "generated_at": format_utc(self.generated_at),
        }


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """Where a dataset lives and which keys may wrap its list."""

    key: str
    paths: tuple[str, ...]
    list_keys: tuple[str, ...]


DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec("products", ("products.json", "catalogue/products.json"), ("products", "items")),
    DatasetSpec("sellers", ("sellers.json",), ("sellers", "items")),
    DatasetSpec(
        "offers",
        ("latest_offers.json", "offers/latest.json"),
        ("offers", "latest", "items"),
    ),
    DatasetSpec(
        "education",
        ("education_offers.json", "education.json", "education/offers.json"),
        ("education_offers", "education", "offers", "items"),
    ),
    DatasetSpec(
        "gift_cards",
        ("gift_cards/offers.json", "gift_cards/latest.json", "gift_cards.json"),
        ("gift_cards", "gift_card_offers", "offers", "items"),
    ),
    DatasetSpec(
        "deals",
        ("deals/latest.json", "deals.json", "deals/offers.json"),
        ("deals", "items"),
    ),
    DatasetSpec(
        "candidates",
        ("matching/review_queue.json",),
        ("candidates", "review_queue", "pending", "queue", "items"),
    ),
    DatasetSpec("runs", ("runs.json", "health/runs.json"), ("runs", "manifests", "items")),
)


@dataclass(frozen=True, slots=True)
class Loaded:
    """A dataset after reading, before model validation."""

    key: str
    filename: str
    items: list[object]
    present: bool


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    """Thresholds read from ``config/validation.yml``."""

    review_queue_warn_threshold: int = DEFAULT_REVIEW_QUEUE_THRESHOLD
    #: When false, an enabled seller whose adapter module has not been written
    #: yet is a warning rather than a hard failure. This exists so that the
    #: foundation milestone can publish a green gate while the collector cards
    #: are still in the backlog. APA-033 flips it to true.
    require_adapter_modules: bool = True


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _locate(data_dir: Path, spec: DatasetSpec) -> Path | None:
    for relative in spec.paths:
        candidate = data_dir / relative
        if candidate.is_file():
            return candidate
    return None


def _extract_items(
    payload: object,
    spec: DatasetSpec,
    filename: str,
    hard: list[Issue],
) -> list[object]:
    if isinstance(payload, list):
        return cast(list[object], payload)
    if isinstance(payload, dict):
        mapping = cast(dict[str, object], payload)
        for key in spec.list_keys:
            value = mapping.get(key)
            if isinstance(value, list):
                return cast(list[object], value)
        expected = " or ".join(f"'{key}'" for key in spec.list_keys)
        hard.append(
            Issue(
                "bad_dataset_shape",
                filename,
                filename,
                f"expected a JSON list or an object keyed by {expected}",
            )
        )
        return []
    hard.append(
        Issue(
            "bad_dataset_shape",
            filename,
            filename,
            f"expected a JSON list or object, got {type(payload).__name__}",
        )
    )
    return []


def _load_dataset(
    data_dir: Path,
    spec: DatasetSpec,
    hard: list[Issue],
    warnings: list[Issue],
) -> Loaded:
    path = _locate(data_dir, spec)
    if path is None:
        canonical = spec.paths[0]
        warnings.append(
            Issue(
                "missing_dataset",
                canonical,
                canonical,
                "dataset not found; treated as empty",
            )
        )
        return Loaded(key=spec.key, filename=canonical, items=[], present=False)

    filename = path.relative_to(data_dir).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        hard.append(Issue("unreadable_dataset", filename, filename, f"cannot read file: {exc}"))
        return Loaded(key=spec.key, filename=filename, items=[], present=True)

    try:
        payload: object = json.loads(text)
    except json.JSONDecodeError as exc:
        hard.append(
            Issue(
                "invalid_json", filename, filename, f"invalid JSON: {exc.msg} (line {exc.lineno})"
            )
        )
        return Loaded(key=spec.key, filename=filename, items=[], present=True)

    items = _extract_items(payload, spec, filename, hard)
    return Loaded(key=spec.key, filename=filename, items=items, present=True)


# --------------------------------------------------------------------------- #
# Model and id checks
# --------------------------------------------------------------------------- #


def _validate_items[ModelT: BaseModel](
    items: Sequence[object],
    model: type[ModelT],
    filename: str,
    hard: list[Issue],
) -> list[tuple[int, ModelT]]:
    validated: list[tuple[int, ModelT]] = []
    for index, item in enumerate(items):
        try:
            validated.append((index, model.model_validate(item)))
        except ValidationError as exc:
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"])
                field_name = f"{filename}[{index}]" + (f".{location}" if location else "")
                hard.append(
                    Issue(
                        "invalid_record",
                        filename,
                        field_name,
                        f"{error['msg']} ({error['type']})",
                    )
                )
    return validated


def _record_ids(
    models: Sequence[tuple[int, BaseModel]],
    attribute: str,
    filename: str,
    hard: list[Issue],
    registry: dict[str, str],
) -> set[str]:
    ids: set[str] = set()
    for index, model in models:
        value = getattr(model, attribute, None)
        if not isinstance(value, str):
            continue
        if value in registry:
            hard.append(
                Issue(
                    "duplicate_id",
                    filename,
                    f"{filename}[{index}].{attribute}",
                    f"duplicate id {value!r}; already used in {registry[value]}",
                )
            )
            continue
        registry[value] = filename
        ids.add(value)
    return ids


def _check_references(
    models: Sequence[tuple[int, BaseModel]],
    attribute: str,
    filename: str,
    known: set[str],
    target_name: str,
    hard: list[Issue],
) -> None:
    for index, model in models:
        value = getattr(model, attribute, None)
        if not isinstance(value, str) or value in known:
            continue
        hard.append(
            Issue(
                "unknown_reference",
                filename,
                f"{filename}[{index}].{attribute}",
                f"{attribute} {value!r} does not exist in {target_name}",
            )
        )


def _check_seller_enabled(
    models: Sequence[tuple[int, BaseModel]],
    filename: str,
    sellers: dict[str, Seller],
    hard: list[Issue],
) -> None:
    for index, model in models:
        seller_id = getattr(model, "seller_id", None)
        if not isinstance(seller_id, str):
            continue
        seller = sellers.get(seller_id)
        if seller is not None and not seller.enabled:
            hard.append(
                Issue(
                    "disabled_seller_reference",
                    filename,
                    f"{filename}[{index}].seller_id",
                    f"seller {seller_id!r} is disabled ({seller.enabled_reason or 'no reason recorded'}) "
                    "but is referenced by this record",
                )
            )


def _adapter_file_exists(module: str, root: Path) -> bool:
    cleaned = module.split(":", 1)[0].strip()
    if not cleaned:
        return False
    if cleaned.endswith(".py"):
        return (root / cleaned).is_file()
    relative = Path(*cleaned.split("."))
    candidate = root / relative
    return candidate.with_suffix(".py").is_file() or (candidate / "__init__.py").is_file()


# --------------------------------------------------------------------------- #
# Configuration and freshness
# --------------------------------------------------------------------------- #


def _read_yaml(
    path: Path,
    label: str,
    warnings: list[Issue],
) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        warnings.append(Issue("bad_config", label, label, f"could not parse {label}: {exc}"))
        return None
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        warnings.append(Issue("bad_config", label, label, f"{label} must contain a mapping"))
        return None
    return cast(dict[str, object], payload)


def _load_validation_config(root: Path, warnings: list[Issue]) -> ValidationConfig:
    payload = _read_yaml(root / "config" / "validation.yml", "config/validation.yml", warnings)
    if payload is None:
        return ValidationConfig()
    require_adapters = payload.get("require_adapter_modules", True)
    if not isinstance(require_adapters, bool):
        require_adapters = True
    threshold = payload.get("review_queue_warn_threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, str, float)):
        return ValidationConfig(require_adapter_modules=require_adapters)
    try:
        parsed = int(threshold)
    except ValueError:
        return ValidationConfig(require_adapter_modules=require_adapters)
    return ValidationConfig(
        review_queue_warn_threshold=max(parsed, 0),
        require_adapter_modules=require_adapters,
    )


def _check_freshness(
    record: Offer | EducationOffer,
    index: int,
    filename: str,
    seller_type: str | None,
    category: str | None,
    config: FreshnessConfig,
    now: datetime,
    warnings: list[Issue],
) -> bool:
    """Apply the shared freshness policy and report a stale or skewed capture.

    Returns true when the record is stale, so callers can count it. The
    arithmetic and the threshold precedence live in
    :mod:`scripts.validation.freshness`; this function only turns the verdict
    into a validation issue.
    """
    verdict = effective_verification(
        record,
        seller_type=seller_type,
        category=category,
        config=config,
        now=now,
    )
    field_name = f"{filename}[{index}].captured_at"

    if verdict.clock_skew:
        warnings.append(
            Issue(
                "future_captured_at",
                filename,
                field_name,
                "captured_at is in the future; age clamped to 0 and the price is not treated as stale",
            )
        )

    if not verdict.stale:
        return False

    warnings.append(
        Issue(
            "stale_offer",
            filename,
            field_name,
            f"{verdict.reason}; render as indicative",
        )
    )
    return True


# --------------------------------------------------------------------------- #
# The validation run
# --------------------------------------------------------------------------- #


def _check_review_queue(
    candidates: Sequence[tuple[int, MatchCandidate]],
    filename: str,
    config: ValidationConfig,
    freshness: FreshnessConfig,
    now: datetime,
    warnings: list[Issue],
) -> None:
    if len(candidates) > config.review_queue_warn_threshold:
        warnings.append(
            Issue(
                "review_queue_growth",
                filename,
                f"{filename}",
                f"{len(candidates)} candidates awaiting review, above the "
                f"{config.review_queue_warn_threshold} threshold",
            )
        )

    threshold = freshness.review_queue_max_age_hours
    stale: list[tuple[int, MatchCandidate]] = []
    if threshold > 0:
        for index, candidate in candidates:
            if candidate.status is not MatchStatus.PENDING:
                continue
            age_hours, _clock_skew = measure_age_hours(candidate.captured_at, now)
            if age_hours > threshold:
                stale.append((index, candidate))
    if stale:
        index, oldest = stale[0]
        warnings.append(
            Issue(
                "review_queue_stale",
                filename,
                f"{filename}[{index}].status",
                f"{len(stale)} pending candidates are older than the "
                f"{threshold} h review budget; oldest is {oldest.candidate_id!r}",
            )
        )


def run_validation(data_dir: Path, *, now: datetime | None = None) -> Report:
    """Validate every dataset under ``data_dir`` and return a structured report."""
    generated_at = now or datetime.now(UTC)
    root = data_dir.resolve().parent

    hard: list[Issue] = []
    warnings: list[Issue] = []
    registries: dict[str, dict[str, str]] = {}
    freshness, freshness_problems = load_freshness_config(root / "config" / "freshness.yml")
    for problem in freshness_problems:
        warnings.append(
            Issue("bad_config", "config/freshness.yml", "config/freshness.yml", problem)
        )
    validation_config = _load_validation_config(root, warnings)

    loaded: dict[str, Loaded] = {
        spec.key: _load_dataset(data_dir, spec, hard, warnings) for spec in DATASETS
    }

    products = _validate_items(loaded["products"].items, Product, loaded["products"].filename, hard)
    sellers = _validate_items(loaded["sellers"].items, Seller, loaded["sellers"].filename, hard)
    offers = _validate_items(loaded["offers"].items, Offer, loaded["offers"].filename, hard)
    education = _validate_items(
        loaded["education"].items, EducationOffer, loaded["education"].filename, hard
    )
    gift_cards = _validate_items(
        loaded["gift_cards"].items, GiftCardOffer, loaded["gift_cards"].filename, hard
    )
    deals = _validate_items(loaded["deals"].items, Deal, loaded["deals"].filename, hard)
    candidates = _validate_items(
        loaded["candidates"].items, MatchCandidate, loaded["candidates"].filename, hard
    )
    runs = _validate_items(loaded["runs"].items, RunManifest, loaded["runs"].filename, hard)

    product_registry = registries.setdefault("product_id", {})
    seller_registry = registries.setdefault("seller_id", {})
    offer_registry = registries.setdefault("offer_id", {})

    product_ids = _record_ids(
        products, "product_id", loaded["products"].filename, hard, product_registry
    )
    seller_ids = _record_ids(
        sellers, "seller_id", loaded["sellers"].filename, hard, seller_registry
    )
    offer_ids = _record_ids(offers, "offer_id", loaded["offers"].filename, hard, offer_registry)
    offer_ids |= _record_ids(
        education, "offer_id", loaded["education"].filename, hard, offer_registry
    )
    offer_ids |= _record_ids(
        gift_cards, "offer_id", loaded["gift_cards"].filename, hard, offer_registry
    )
    _record_ids(
        deals, "deal_id", loaded["deals"].filename, hard, registries.setdefault("deal_id", {})
    )
    _record_ids(
        candidates,
        "candidate_id",
        loaded["candidates"].filename,
        hard,
        registries.setdefault("candidate_id", {}),
    )
    _record_ids(runs, "run_id", loaded["runs"].filename, hard, registries.setdefault("run_id", {}))

    # Referential integrity, skipped with a warning when the target dataset is absent.
    product_referenced: tuple[tuple[str, Sequence[tuple[int, BaseModel]]], ...] = (
        (loaded["offers"].filename, offers),
        (loaded["education"].filename, education),
        (loaded["gift_cards"].filename, gift_cards),
        (loaded["deals"].filename, deals),
    )
    if loaded["products"].present:
        for filename, models_ in product_referenced:
            _check_references(
                models_,
                "product_id",
                filename,
                product_ids,
                loaded["products"].filename,
                hard,
            )
        _check_references(
            candidates,
            "proposed_product_id",
            loaded["candidates"].filename,
            product_ids,
            loaded["products"].filename,
            hard,
        )
    else:
        warnings.append(
            Issue(
                "referential_check_skipped",
                loaded["products"].filename,
                loaded["products"].filename,
                "product references were not checked because the product catalogue is missing",
            )
        )

    seller_referenced: tuple[tuple[str, Sequence[tuple[int, BaseModel]]], ...] = (
        (loaded["offers"].filename, offers),
        (loaded["education"].filename, education),
        (loaded["gift_cards"].filename, gift_cards),
        (loaded["deals"].filename, deals),
        (loaded["candidates"].filename, candidates),
    )
    if loaded["sellers"].present:
        for filename, models_ in seller_referenced:
            _check_references(
                models_,
                "seller_id",
                filename,
                seller_ids,
                loaded["sellers"].filename,
                hard,
            )
        seller_index = {seller.seller_id: seller for _, seller in sellers}
        for filename, models_ in seller_referenced[:-1]:
            _check_seller_enabled(models_, filename, seller_index, hard)
    else:
        warnings.append(
            Issue(
                "referential_check_skipped",
                loaded["sellers"].filename,
                loaded["sellers"].filename,
                "seller references were not checked because the seller registry is missing",
            )
        )

    if loaded["offers"].present or loaded["education"].present or loaded["gift_cards"].present:
        for index, deal in deals:
            if deal.offer_id is not None and deal.offer_id not in offer_ids:
                hard.append(
                    Issue(
                        "unknown_reference",
                        loaded["deals"].filename,
                        f"{loaded['deals'].filename}[{index}].offer_id",
                        f"offer_id {deal.offer_id!r} does not exist in the offer datasets",
                    )
                )

    # Seller registry hygiene.
    automated_methods = frozenset(
        {
            CollectionMethod.AUTOMATED_HTML,
            CollectionMethod.AUTOMATED_JSONLD,
            CollectionMethod.AUTOMATED_API,
        }
    )
    for index, seller in sellers:
        if not seller.enabled:
            continue
        if seller.collection_method in automated_methods and not _adapter_file_exists(
            seller.adapter_module, root
        ):
            issue = Issue(
                "missing_adapter_module",
                loaded["sellers"].filename,
                f"{loaded['sellers'].filename}[{index}].adapter_module",
                f"enabled seller {seller.seller_id!r} names {seller.adapter_module!r}, "
                "but no such module file exists",
            )
            if validation_config.require_adapter_modules:
                hard.append(issue)
            else:
                warnings.append(issue)
        if seller.authorised_reseller is None:
            warnings.append(
                Issue(
                    "unverified_reseller_status",
                    loaded["sellers"].filename,
                    f"{loaded['sellers'].filename}[{index}].authorised_reseller",
                    f"enabled seller {seller.seller_id!r} has authorised_reseller=null; "
                    "record the evidence URL or set it to false",
                )
            )

    # Education offers only apply to education-eligible products.
    eligibility = {product.product_id: product.education_eligible for _, product in products}
    categories = {product.product_id: product.category.value for _, product in products}
    if loaded["products"].present:
        for index, education_offer in education:
            if not eligibility.get(education_offer.product_id, False):
                warnings.append(
                    Issue(
                        "education_ineligible_product",
                        loaded["education"].filename,
                        f"{loaded['education'].filename}[{index}].product_id",
                        f"product {education_offer.product_id!r} is not marked education_eligible",
                    )
                )

    # Freshness: per config/freshness.yml when present.
    seller_types = {seller.seller_id: seller.seller_type.value for _, seller in sellers}
    stale_count = 0
    for index, offer in offers:
        stale = _check_freshness(
            offer,
            index,
            loaded["offers"].filename,
            seller_types.get(offer.seller_id),
            categories.get(offer.product_id),
            freshness,
            generated_at,
            warnings,
        )
        stale_count += int(stale)
    for index, education_offer in education:
        stale = _check_freshness(
            education_offer,
            index,
            loaded["education"].filename,
            seller_types.get(education_offer.seller_id),
            categories.get(education_offer.product_id),
            freshness,
            generated_at,
            warnings,
        )
        stale_count += int(stale)

    _check_review_queue(
        candidates,
        loaded["candidates"].filename,
        validation_config,
        freshness,
        generated_at,
        warnings,
    )

    # Run manifests should exist for every offer's run.
    if loaded["runs"].present:
        run_ids = {manifest.run_id for _, manifest in runs}
        for index, offer in offers:
            if offer.run_id not in run_ids:
                warnings.append(
                    Issue(
                        "unknown_run_id",
                        loaded["offers"].filename,
                        f"{loaded['offers'].filename}[{index}].run_id",
                        f"run_id {offer.run_id!r} has no manifest in {loaded['runs'].filename}",
                    )
                )

    stats: dict[str, int] = {
        "datasets_present": sum(1 for dataset in loaded.values() if dataset.present),
        "datasets_expected": len(DATASETS),
        "products": len(products),
        "sellers": len(sellers),
        "offers": len(offers),
        "education_offers": len(education),
        "gift_card_offers": len(gift_cards),
        "deals": len(deals),
        "match_candidates": len(candidates),
        "run_manifests": len(runs),
        "stale_offers": stale_count,
        "hard_failures": len(hard),
        "warnings": len(warnings),
    }

    return Report(
        hard=sorted(hard, key=lambda issue: (issue.file, issue.field, issue.code)),
        warnings=sorted(warnings, key=lambda issue: (issue.file, issue.field, issue.code)),
        stats=stats,
        generated_at=generated_at,
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.validation.validate_data",
        description="Validate the Orchard datasets against the domain models.",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="directory holding the committed datasets (default: data)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a single machine-readable JSON report instead of human-readable lines",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as hard failures (use before a release)",
    )
    return parser.parse_args(argv)


def _print_human(report: Report, *, data_dir: Path, strict: bool) -> None:
    counts = ", ".join(f"{name}={value}" for name, value in sorted(report.stats.items()))
    print("Orchard data validation")
    print(f"  data dir:   {data_dir}")
    print(f"  generated:  {format_utc(report.generated_at)}")
    if strict:
        print("  mode:       strict (warnings are failures)")
    print()

    if report.hard:
        print(f"HARD FAILURES ({len(report.hard)})")
        for issue in report.hard:
            print(f"  HARD  {issue.line()}")
        print()
    else:
        print("HARD FAILURES: none")
        print()

    if report.warnings:
        print(f"WARNINGS ({len(report.warnings)})")
        for issue in report.warnings:
            print(f"  WARN  {issue.line()}")
        print()
    else:
        print("WARNINGS: none")
        print()

    print(f"stats: {counts}")
    verdict = "PASS" if report.ok(strict=strict) else "FAIL"
    reason = "hard failures" if report.hard else ("warnings under --strict" if strict else "")
    suffix = f" ({reason})" if reason else ""
    print(f"RESULT: {verdict}{suffix}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    data_dir = Path(args.data_dir)
    try:
        report = run_validation(data_dir)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 2

    try:
        if args.json:
            print(json.dumps(report.as_dict(strict=args.strict), indent=2, sort_keys=True))
        else:
            _print_human(report, data_dir=data_dir, strict=args.strict)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 2

    return 0 if report.ok(strict=args.strict) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
