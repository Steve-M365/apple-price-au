"""Write the JSON Schema for every Orchard domain model into ``data/schema/``.

Usage::

    python -m scripts.models.export_schema
    python -m scripts.models.export_schema --out-dir data/schema --check

``--check`` writes nothing and exits 1 when a committed schema file is missing
or stale, so CI can fail on schema drift.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from .deal import Deal, PurchaseRoute
from .education import EducationOffer
from .gift_card import GiftCardOffer
from .matching import MatchCandidate
from .offer import Offer
from .product import Product
from .run import RunError, RunManifest
from .seller import Seller

SCHEMAS: Final[dict[str, type[BaseModel]]] = {
    "product": Product,
    "seller": Seller,
    "offer": Offer,
    "education_offer": EducationOffer,
    "gift_card_offer": GiftCardOffer,
    "deal": Deal,
    "purchase_route": PurchaseRoute,
    "match_candidate": MatchCandidate,
    "run_manifest": RunManifest,
    "run_error": RunError,
}


def render_schema(model: type[BaseModel]) -> str:
    """Render one model's validation JSON Schema as stable, sorted JSON."""
    schema: dict[str, Any] = model.model_json_schema(mode="validation")
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.models.export_schema",
        description="Export JSON Schema for the Orchard domain models.",
    )
    parser.add_argument(
        "--out-dir",
        default="data/schema",
        help="directory to write <model>.schema.json into (default: data/schema)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed schemas are current instead of writing them",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stale: list[str] = []
    for name, model in SCHEMAS.items():
        path = out_dir / f"{name}.schema.json"
        rendered = render_schema(model)
        if args.check:
            current = path.read_text(encoding="utf-8") if path.is_file() else ""
            if current != rendered:
                stale.append(str(path))
        else:
            path.write_text(rendered, encoding="utf-8")
            print(f"wrote {path}")

    if args.check:
        if stale:
            for stale_path in stale:
                print(f"stale: {stale_path}")
            print("run 'python -m scripts.models.export_schema' and commit the result")
            return 1
        print(f"{len(SCHEMAS)} schemas up to date in {out_dir}")
        return 0

    print(f"{len(SCHEMAS)} schemas written to {out_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
