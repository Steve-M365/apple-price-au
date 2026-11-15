"""Canonical Apple product titles to deterministic product identifiers.

The collectors capture a *raw* Apple title — normally the JSON-LD ``Product.name``
from an Apple AU product page — and hand it here. This module turns that title
into the catalogue's canonical identity:

``product_id``
    ``apple-<model>-<screen>-<chip>-<memory>-<storage>-<colour>`` where every
    axis is optional and omitted when the product does not have it. The axes
    always appear in the same order (:data:`SLUG_AXIS_ORDER`), so a Mac mini is
    ``apple-mac-mini-m6-16gb-256gb`` and a Watch is
    ``apple-watch-series-12-42mm-space-grey-aluminium-gps``. Nothing is ever
    numbered, hashed or counted: the id is a pure function of the title.

``configuration``
    A ``dict[str, str]`` using the catalogue's conventional keys. Absent axes
    are omitted; nothing is inferred from a price or a URL.

``family`` / ``model`` / ``name``
    The catalogue's grouping key, human model name and readable display name.

Normalisation is intentionally multi-pass: :func:`normalise` accepts a raw Apple
title, a lightly tidied title, or an already-canonical catalogue name, and all
three produce the same id. Dashes, quotes and spaces are unified first (Apple
writes ``13-inch``, ``13\u2011inch`` (a non-breaking hyphen), ``13"`` and
``13 inch``), then the model
is matched against :mod:`scripts.normalise_data.catalogue`, then each axis is
extracted and consumed so later passes cannot re-read the same text.

Watch bundles
--------------
Apple's Watch JSON-LD name bundles a case and a band, e.g. ``"Apple Watch
Series 12 GPS, 42-mm Space Grey Aluminium Case with After Dark Black Nike Sport
Band"``. **The id is the case, not the bundle**: size, case colour, case
material and connectivity (``apple-watch-series-12-42mm-space-grey-aluminium-gps``).
The band is recorded in ``configuration["band"]`` and in the readable ``name``,
and is deliberately excluded from the id so one case does not fragment into one
product per band. Because a different band is still a genuinely different,
differently priced bundle, the collision check compares the *full* canonical
name: a second band for a case already in the catalogue raises
:class:`NormalisationCollision` rather than silently merging the two bundles.

Catalogue-anchored defaults
---------------------------
A handful of Apple titles omit an axis that the catalogue treats as fixed for
that generation — the base iPad page is just ``"iPad Wi-Fi 128GB — Blue"``
although the base iPad is the 11-inch A16 model, an iPhone title never names
its screen size, and the Apple TV page never says "4th generation". Those axes
live in each model's ``defaults`` in the registry. They are *catalogue anchors*
sourced from ``data/products.json``, never guesses about a new product: a
default only fills a gap, never overrides the title, and if it produces an id
that already means something else in the catalogue the collision check stops
the run.

Unknown, incomplete or ambiguous titles raise :class:`UnrecognisedTitle` with
the raw string. Two different configurations may never collapse to one id: that
raises :class:`NormalisationCollision` naming both titles.

Idempotency
-----------
The identity is a fixed point: ``normalise(normalise(x).name)`` yields the same
``product_id`` and ``name``, and normalising the same title twice is equal.
Configuration is necessarily a little richer than the display name — Apple's
canonical name omits CPU/GPU core counts and display finish — so a second pass
over a *name* cannot re-derive those details. Collectors pass the raw captured
title, where they are present.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

__all__ = [
    "NormalisationCollision",
    "NormalisationError",
    "NormalisedProduct",
    "UnrecognisedTitle",
    "normalise",
    "slugify",
]

_DATA_DIR: Final = Path(__file__).with_name("normalise_data")
_REGISTRY_PATH: Final = _DATA_DIR / "catalogue.json"
_PRODUCTS_PATH: Final = Path(__file__).resolve().parents[1] / "data" / "products.json"

#: Canonical order of the axes that may appear in a ``product_id``.
SLUG_AXIS_ORDER: Final = (
    "screen_size",
    "size",
    "chip",
    "memory",
    "storage",
    "layout",
    "colour",
    "case_material",
    "connectivity",
    "case",
    "generation",
)

#: Apple's dash, quote and space variants. U+2011 is the non-breaking hyphen
#: Apple uses in names such as "Wi-Fi" and "11-inch".
_TRANSLATE: Final = str.maketrans(
    {
        "\u00a0": " ",  # no-break space
        "\u2007": " ",  # figure space
        "\u2009": " ",  # thin space
        "\u202f": " ",  # narrow no-break space
        "\u200b": "",  # zero-width space
        "\u00ad": "",  # soft hyphen
        "\u2010": "-",  # hyphen
        "\u2011": "-",  # non-breaking hyphen
        "\u2012": "-",  # figure dash
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2015": "-",  # horizontal bar
        "\u2212": "-",  # minus sign
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2032": "'",
        "\u2033": '"',
    }
)

_WHITESPACE_RE: Final = re.compile(r"\s+")
_SLUG_RE: Final = re.compile(r"[^a-z0-9]+")
_SCREEN_RE: Final = re.compile(
    r'(?<![\w.])(?P<number>\d{1,2}(?:[.,]\d)?)\s*(?:-\s*)?(?:inch(?:es)?|")(?!\w)',
    re.IGNORECASE,
)
_WATCH_SIZE_RE: Final = re.compile(
    r"(?<![\w.])(?P<number>\d{2,3})\s*(?:-\s*)?mm(?!\w)", re.IGNORECASE
)
_CHIP_RE: Final = re.compile(
    r"(?<!\w)(?P<base>[AM]\d{1,2})(?:\s+(?P<tier>Pro|Max|Ultra))?(?:\s+[Cc]hip)?(?!\w)",
    re.IGNORECASE,
)
_CORE_RE: Final = re.compile(
    r"(?<!\w)(?P<count>\d{1,2})\s*-?\s*core\s+(?P<part>CPU|GPU)(?!\w)", re.IGNORECASE
)
_CAPACITY_RE: Final = re.compile(
    r"(?<![\w.])(?P<number>\d{1,4})\s*(?P<unit>GB|TB)(?!\w)", re.IGNORECASE
)
_GENERATION_RE: Final = re.compile(
    r"(?<!\w)(?P<number>\d{1,2})(?:st|nd|rd|th)?\s+generation(?!\w)", re.IGNORECASE
)
_BAND_WITH_RE: Final = re.compile(r"(?<!\w)with\s+(?P<band>.+)$", re.IGNORECASE)
_BAND_PAREN_RE: Final = re.compile(r"\((?P<band>[^()]*)\)\s*$")
_TRAILING_CONNECTOR_RE: Final = re.compile(r"\s+(?:with|for|and|of)$", re.IGNORECASE)
_EMPTY_PARENS_RE: Final = re.compile(r"\s*\(\s*\)")
_ALPHA_TOKEN_RE: Final = re.compile(r"[A-Za-z]{2,}")

_CONNECTIVITY: Final = (
    (re.compile(r"gps\s*\+\s*cellular", re.IGNORECASE), "GPS + Cellular"),
    (re.compile(r"wi-?fi\s*\+\s*cellular", re.IGNORECASE), "Wi-Fi + Cellular"),
    (re.compile(r"(?<!\w)gps(?!\w)", re.IGNORECASE), "GPS"),
    (re.compile(r"(?<!\w)lte(?!\w)", re.IGNORECASE), "LTE"),
    (re.compile(r"wi-?fi", re.IGNORECASE), "Wi-Fi"),
    (re.compile(r"(?<!\w)5g(?!\w)", re.IGNORECASE), "5G"),
)

_DISPLAY: Final = (
    (re.compile(r"nano[\s-]*texture\s+glass", re.IGNORECASE), "Nano-texture glass"),
    (re.compile(r"standard\s+glass", re.IGNORECASE), "Standard glass"),
    (re.compile(r"nano[\s-]*texture\s+display", re.IGNORECASE), "Nano-texture display"),
    (re.compile(r"standard\s+display", re.IGNORECASE), "Standard display"),
)

_STAND: Final = (
    (re.compile(r"(?<!\w)vesa\s+mount(?!\w)", re.IGNORECASE), "VESA mount"),
    (re.compile(r"(?<!\w)stand(?!\w)", re.IGNORECASE), "Standard stand"),
)


class NormalisationError(ValueError):
    """Base class for every title-normalisation failure."""


class UnrecognisedTitle(NormalisationError):  # noqa: N818 - name fixed by APA-013
    """The title is unknown, incomplete or ambiguous; nothing was guessed."""

    def __init__(self, raw_title: str, reason: str) -> None:
        self.raw_title = raw_title
        self.reason = reason
        super().__init__(f"cannot normalise {raw_title!r}: {reason}")


class NormalisationCollision(NormalisationError):  # noqa: N818 - name fixed by APA-013
    """An existing ``product_id`` already means a different configuration."""

    def __init__(self, product_id: str, existing_title: str, candidate_title: str) -> None:
        self.product_id = product_id
        self.existing_title = existing_title
        self.candidate_title = candidate_title
        super().__init__(
            f"product_id {product_id!r} already exists as {existing_title!r} but "
            f"{candidate_title!r} normalises to the same id; refusing to merge two "
            "different configurations"
        )


@dataclass(frozen=True)
class NormalisedProduct:
    """One canonical Apple product identity derived from a raw title."""

    product_id: str
    name: str
    family: str
    model: str
    category: str
    configuration: dict[str, str]
    source_title: str

    def as_dict(self) -> dict[str, Any]:
        """Return the normalised record as plain JSON-ready data."""
        return {
            "product_id": self.product_id,
            "name": self.name,
            "family": self.family,
            "model": self.model,
            "category": self.category,
            "configuration": dict(self.configuration),
            "source_title": self.source_title,
        }


@dataclass(frozen=True)
class _ModelSpec:
    family: str
    category: str
    pattern: re.Pattern[str]
    model_template: str
    slug_template: str
    name_template: str
    emit: frozenset[str]
    required: frozenset[str]
    capacities: tuple[str, ...]
    defaults: Mapping[str, str]
    fixed: Mapping[str, str]
    generation_template: str | None


@dataclass(frozen=True)
class _Registry:
    models: tuple[_ModelSpec, ...]
    colour_pattern: re.Pattern[str]
    colour_map: Mapping[str, str]
    material_pattern: re.Pattern[str]
    material_map: Mapping[str, str]
    layout_pattern: re.Pattern[str]
    layout_map: Mapping[str, str]
    filler_words: frozenset[str]


class _EmptyDefault(dict[str, str]):
    """``str.format_map`` helper: a missing axis renders as an empty string."""

    def __missing__(self, key: str) -> str:
        return ""


class _Scanner:
    """A mutable view of the title where consumed spans become blanks."""

    __slots__ = ("view",)

    def __init__(self, text: str) -> None:
        self.view = text

    def take(self, match: re.Match[str]) -> str:
        value = match.group(0)
        start, end = match.span()
        self.view = self.view[:start] + (" " * (end - start)) + self.view[end:]
        return value


def _canonicalise(text: str) -> str:
    """Unify dash, quote, space and Unicode width variants."""
    normalised = unicodedata.normalize("NFC", text).translate(_TRANSLATE)
    return _WHITESPACE_RE.sub(" ", normalised).strip()


def slugify(value: str) -> str:
    """Lowercase, hyphen-separated, punctuation-free slug component.

    A decimal point becomes a hyphen (``6.3-inch`` -> ``6-3-inch``), every run
    of non-alphanumerics collapses to one hyphen, and no hyphen is ever left at
    either end.
    """
    return _SLUG_RE.sub("-", _canonicalise(value).lower()).strip("-")


def _require(entry: Mapping[str, Any], key: str, kind: type, where: str) -> Any:
    value = entry.get(key)
    if not isinstance(value, kind):
        raise RuntimeError(f"{_REGISTRY_PATH}: {where}[{key!r}] must be {kind.__name__}")
    return value


def _str_tuple(entry: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    raw = _require(entry, key, list, where)
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise RuntimeError(f"{_REGISTRY_PATH}: {where}[{key!r}] must hold strings")
        values.append(item)
    return tuple(values)


def _str_map(entry: Mapping[str, Any], key: str, where: str) -> dict[str, str]:
    raw = _require(entry, key, dict, where)
    values: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise RuntimeError(f"{_REGISTRY_PATH}: {where}[{key!r}] must map strings to strings")
        values[name] = value
    return values


def _alias_pattern(aliases: Iterable[str]) -> re.Pattern[str]:
    ordered = sorted({alias.lower() for alias in aliases}, key=len, reverse=True)
    body = "|".join(re.escape(alias) for alias in ordered)
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.IGNORECASE)


@lru_cache(maxsize=1)
def _registry() -> _Registry:
    try:
        payload = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except OSError as exc:  # pragma: no cover - packaging failure
        raise RuntimeError(f"cannot read {_REGISTRY_PATH}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{_REGISTRY_PATH}: top-level value must be an object")

    raw_models = payload.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise RuntimeError(f"{_REGISTRY_PATH}: 'models' must be a non-empty list")

    models: list[_ModelSpec] = []
    for index, entry in enumerate(raw_models):
        if not isinstance(entry, dict):
            raise RuntimeError(f"{_REGISTRY_PATH}: models[{index}] must be an object")
        where = f"models[{index}]"
        match = _require(entry, "match", str, where)
        generation = entry.get("generation")
        if generation is not None and not isinstance(generation, str):
            raise RuntimeError(f"{_REGISTRY_PATH}: {where}['generation'] must be a string or null")
        emit = frozenset(_str_tuple(entry, "emit", where))
        raw_required = entry.get("required")
        required = emit if raw_required is None else frozenset(_str_tuple(entry, "required", where))
        models.append(
            _ModelSpec(
                family=_require(entry, "family", str, where),
                category=_require(entry, "category", str, where),
                pattern=re.compile(rf"(?<!\w)(?:{match})(?!\w)", re.IGNORECASE),
                model_template=_require(entry, "model", str, where),
                slug_template=_require(entry, "slug", str, where),
                name_template=_require(entry, "name", str, where),
                emit=emit,
                required=required,
                capacities=_str_tuple(entry, "capacities", where),
                defaults=_str_map(entry, "defaults", where),
                fixed=_str_map(entry, "fixed", where),
                generation_template=generation,
            )
        )

    def alias_block(key: str) -> tuple[re.Pattern[str], Mapping[str, str]]:
        raw = payload.get(key)
        if not isinstance(raw, list) or not raw:
            raise RuntimeError(f"{_REGISTRY_PATH}: {key!r} must be a non-empty list")
        lookup: dict[str, str] = {}
        aliases: list[str] = []
        for index, entry in enumerate(raw):
            if not isinstance(entry, dict):
                raise RuntimeError(f"{_REGISTRY_PATH}: {key}[{index}] must be an object")
            where = f"{key}[{index}]"
            canonical = _require(entry, "canonical", str, where)
            for alias in _str_tuple(entry, "aliases", where):
                aliases.append(alias)
                lookup[alias.lower()] = canonical
        return _alias_pattern(lookup), lookup

    colour_pattern, colour_map = alias_block("colours")
    material_pattern, material_map = alias_block("materials")
    layout_pattern, layout_map = alias_block("layouts")
    filler_words = frozenset(word.lower() for word in _str_tuple(payload, "filler_words", "root"))

    return _Registry(
        models=tuple(models),
        colour_pattern=colour_pattern,
        colour_map=colour_map,
        material_pattern=material_pattern,
        material_map=material_map,
        layout_pattern=layout_pattern,
        layout_map=layout_map,
        filler_words=filler_words,
    )


@lru_cache(maxsize=1)
def _default_catalogue() -> dict[str, str]:
    """Map every catalogue ``product_id`` to its canonical name.

    Only the identity and display name are read: the normaliser must never need
    a price to decide what a product is.
    """
    try:
        payload = json.loads(_PRODUCTS_PATH.read_text(encoding="utf-8"))
    except OSError:
        return {}
    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list):
        return {}
    catalogue: dict[str, str] = {}
    for product in products:
        if not isinstance(product, dict):
            continue
        product_id = product.get("product_id")
        name = product.get("name")
        if isinstance(product_id, str) and isinstance(name, str):
            catalogue[product_id] = name
    return catalogue


def _find_alias(
    view: str, pattern: re.Pattern[str], lookup: Mapping[str, str]
) -> tuple[re.Match[str], str] | None:
    match = pattern.search(view)
    if match is None:
        return None
    return match, lookup[match.group(0).lower()]


def _tidy_name(name: str) -> str:
    cleaned = _EMPTY_PARENS_RE.sub("", name)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip(" ,-")
    cleaned = _TRAILING_CONNECTOR_RE.sub("", cleaned).strip(" ,-")
    return cleaned


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix} generation"


def _chip_display(base: str, tier: str | None) -> str:
    core = base.upper()
    return f"{core} {tier.title()}" if tier else core


class _Axes:
    """Extract every axis from a matched title, consuming text as it goes."""

    def __init__(self, scanner: _Scanner, spec: _ModelSpec, raw_title: str) -> None:
        self._scanner = scanner
        self._spec = spec
        self._raw = raw_title
        self.configuration: dict[str, str] = dict(spec.fixed)

    # -- helpers ---------------------------------------------------------

    def _fail(self, reason: str) -> UnrecognisedTitle:
        return UnrecognisedTitle(self._raw, reason)

    def _search(self, pattern: re.Pattern[str]) -> re.Match[str] | None:
        return pattern.search(self._scanner.view)

    def _take_first(self, patterns: Iterable[tuple[re.Pattern[str], str]]) -> str | None:
        for pattern, canonical in patterns:
            match = self._search(pattern)
            if match is not None:
                self._scanner.take(match)
                return canonical
        return None

    # -- axes ------------------------------------------------------------

    def band(self) -> None:
        """Watch band: everything after 'with', else a trailing parenthesis."""
        match = _BAND_WITH_RE.search(self._scanner.view)
        if match is not None:
            band = match.group("band").strip(" .")
            self._scanner.take(match)
        else:
            match = _BAND_PAREN_RE.search(self._scanner.view)
            if match is None or not match.group("band").strip():
                return
            band = match.group("band").strip()
            self._scanner.take(match)
        self.configuration["band"] = _WHITESPACE_RE.sub(" ", band).strip()

    def accessory_case(self) -> None:
        """AirPods case: the 'with …' suffix of an AirPods title."""
        match = _BAND_WITH_RE.search(self._scanner.view)
        if match is None:
            return
        case = match.group("band").strip(" .")
        self._scanner.take(match)
        self.configuration["case"] = _WHITESPACE_RE.sub(" ", case).strip()

    def screen_size(self) -> None:
        match = self._search(_SCREEN_RE)
        if match is None:
            return
        self._scanner.take(match)
        number = match.group("number").replace(",", ".")
        self.configuration["screen_size"] = f"{number}-inch"

    def watch_size(self) -> None:
        match = self._search(_WATCH_SIZE_RE)
        if match is None:
            return
        self._scanner.take(match)
        self.configuration["size"] = f"{match.group('number')}mm"

    def chip(self) -> None:
        match = self._search(_CHIP_RE)
        if match is None:
            return
        self._scanner.take(match)
        base = match.group("base").upper()
        tier = match.group("tier")
        cores: list[str] = []
        for core_match in list(_CORE_RE.finditer(self._scanner.view)):
            self._scanner.take(core_match)
            cores.append(f"{core_match.group('count')}-core {core_match.group('part').upper()}")
        chip = f"Apple {_chip_display(base, tier)}"
        if cores:
            chip = f"{chip} ({', '.join(cores)})"
        self.configuration["chip"] = chip

    def capacities(self) -> None:
        matches = list(_CAPACITY_RE.finditer(self._scanner.view))
        if not matches:
            return
        labelled: dict[str, re.Match[str]] = {}
        bare: list[re.Match[str]] = []
        for match in matches:
            tail = self._scanner.view[match.end() : match.end() + 16].lower()
            if tail.startswith(" memory") or tail.startswith(" unified memory"):
                key = "memory"
            elif tail.startswith(" storage"):
                key = "storage"
            else:
                key = ""
            if key:
                if key not in self._spec.capacities:
                    raise self._fail(f"a {key} capacity is not a {self._spec.family} axis")
                if key in labelled:
                    raise self._fail(f"{key} is specified more than once")
                labelled[key] = match
            else:
                bare.append(match)

        remaining = [axis for axis in self._spec.capacities if axis not in labelled]
        if bare and len(remaining) == len(bare):
            for axis, match in zip(remaining, bare, strict=True):
                labelled[axis] = match
        elif bare:
            raise self._fail(
                "ambiguous capacity: cannot tell memory from storage in "
                + ", ".join(match.group(0) for match in bare)
            )

        for axis, match in labelled.items():
            unit = match.group("unit").upper()
            if axis == "memory" and unit != "GB":
                raise self._fail(f"memory must be quoted in GB, got {match.group(0)!r}")
            self.configuration[axis] = f"{int(match.group('number'))}{unit}"

        for match in matches:
            self._scanner.take(match)

    def connectivity(self) -> None:
        canonical = self._take_first(_CONNECTIVITY)
        if canonical is not None:
            self.configuration["connectivity"] = canonical

    def layout(self) -> None:
        found = _find_alias(self._scanner.view, _registry().layout_pattern, _registry().layout_map)
        if found is None:
            return
        match, canonical = found
        self._scanner.take(match)
        self.configuration["layout"] = canonical

    def display(self) -> None:
        canonical = self._take_first(_DISPLAY)
        if canonical is not None:
            self.configuration["display"] = canonical

    def stand(self) -> None:
        canonical = self._take_first(_STAND)
        if canonical is not None:
            self.configuration["stand"] = canonical

    def case_material(self) -> None:
        registry = _registry()
        found = _find_alias(self._scanner.view, registry.material_pattern, registry.material_map)
        if found is None:
            return
        match, canonical = found
        self._scanner.take(match)
        self.configuration["case_material"] = canonical

    def colour(self) -> None:
        registry = _registry()
        found = _find_alias(self._scanner.view, registry.colour_pattern, registry.colour_map)
        if found is None:
            return
        match, canonical = found
        self._scanner.take(match)
        self.configuration["colour"] = canonical

    def generation(self) -> None:
        match = self._search(_GENERATION_RE)
        if match is None:
            return
        self._scanner.take(match)
        self.configuration["generation"] = _ordinal(int(match.group("number")))

    def reject_residue(self) -> None:
        unknown = sorted(
            {
                token.lower()
                for token in _ALPHA_TOKEN_RE.findall(self._scanner.view)
                if token.lower() not in _registry().filler_words
            }
        )
        if unknown:
            raise self._fail("unrecognised token(s) " + ", ".join(repr(token) for token in unknown))


def _match_model(title: str) -> tuple[_ModelSpec, re.Match[str]]:
    for spec in _registry().models:
        match = spec.pattern.search(title)
        if match is not None:
            return spec, match
    raise UnrecognisedTitle(title, "unknown Apple product family")


def _chip_short(chip: str) -> str:
    short = chip.removeprefix("Apple ")
    return short.split(" (", 1)[0]


def normalise(raw_title: str, *, catalogue: Mapping[str, str] | None = None) -> NormalisedProduct:
    """Normalise one raw Apple title into a canonical product identity.

    ``catalogue`` maps known ``product_id`` values to their canonical names; it
    defaults to the committed ``data/products.json``. A computed id that is
    already taken by a different configuration raises
    :class:`NormalisationCollision` instead of overwriting or merging.
    """
    if not isinstance(raw_title, str):
        raise TypeError(f"raw_title must be str, got {type(raw_title).__name__}")
    title = _canonicalise(raw_title)
    if not title:
        raise UnrecognisedTitle(raw_title, "empty title")

    spec, model_match = _match_model(title)
    scanner = _Scanner(title)
    scanner.take(model_match)
    groups = {key: value for key, value in model_match.groupdict().items() if value is not None}
    model = spec.model_template.format_map(_EmptyDefault(groups))
    slug = spec.slug_template.format_map(_EmptyDefault(groups))

    axes = _Axes(scanner, spec, raw_title)
    if spec.family.startswith("apple_watch"):
        axes.band()
    if spec.family == "airpods":
        axes.accessory_case()
    axes.screen_size()
    if spec.family.startswith("apple_watch"):
        axes.watch_size()
    axes.chip()
    axes.capacities()
    axes.connectivity()
    axes.layout()
    axes.display()
    axes.stand()
    if spec.family.startswith("apple_watch"):
        axes.case_material()
    axes.colour()
    axes.generation()
    axes.reject_residue()

    configuration = axes.configuration
    if spec.generation_template is not None:
        configuration.setdefault(
            "generation", spec.generation_template.format_map(_EmptyDefault(groups))
        )
    for key, value in spec.defaults.items():
        configuration.setdefault(key, value)

    chip_short = _chip_short(configuration["chip"]) if "chip" in configuration else ""

    missing = sorted(axis for axis in spec.required if axis not in configuration)
    if missing:
        raise UnrecognisedTitle(
            raw_title,
            "missing required "
            + ("axis" if len(missing) == 1 else "axes")
            + ": "
            + ", ".join(missing),
        )

    values = _EmptyDefault(configuration)
    values["model"] = model
    values["chip_short"] = chip_short

    parts = ["apple", slug]
    for axis in SLUG_AXIS_ORDER:
        if axis in spec.emit and axis in configuration:
            parts.append(slugify(chip_short if axis == "chip" else configuration[axis]))
    product_id = "-".join(part for part in parts if part)

    name = _tidy_name(spec.name_template.format_map(values))

    known = (catalogue if catalogue is not None else _default_catalogue()).get(product_id)
    if known is not None and known != name:
        raise NormalisationCollision(product_id, existing_title=known, candidate_title=name)

    return NormalisedProduct(
        product_id=product_id,
        name=name,
        family=spec.family,
        model=model,
        category=spec.category,
        configuration=dict(configuration),
        source_title=raw_title,
    )
