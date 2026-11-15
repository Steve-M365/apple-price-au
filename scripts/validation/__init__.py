"""Orchard dataset validation.

Run ``python -m scripts.validation.validate_data`` to check every committed
dataset against the domain models and the dataset-level invariants. Hard
failures exit 1 and block deployment; warnings are reported and, with
``--strict``, also fail the run.

The validated entry points live in :mod:`scripts.validation.validate_data`:
``Issue``, ``Report``, ``main`` and ``run_validation``.
"""

from __future__ import annotations

__all__ = ["validate_data"]
