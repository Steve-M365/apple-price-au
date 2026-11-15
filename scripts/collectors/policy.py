"""The robots.txt policy gate: the check that runs before any fetch.

This module exists so that no collector can reach a retailer without a recorded
permission decision. It parses ``robots.txt`` per origin, caches the result for
the duration of a run, and answers one question for one URL: may this user agent
fetch it?

Three rules shape the implementation, and they are not negotiable:

1. **An unreadable policy is not permission.** A network failure, a timeout, a
   ``429`` or a ``5xx`` on ``robots.txt`` yields ``allowed=False``. Deny by
   default is the only safe reading of a policy file we could not read.
2. **An absent policy is permission for all.** A ``404`` (or ``410``) means the
   origin publishes no restrictions, and RFC 9309 says the crawler may proceed.
   That exception is deliberate, narrow and tested.
3. **There is no bypass.** This module never spoofs a user agent to dodge a
   block, never proxies, and never drives a headless browser. When a decision is
   negative the caller records it and routes to the curated path.

Rule precedence follows RFC 9309 and the de-facto Google reading: the group for
the most specific matching ``User-agent`` wins over ``*``; within a group the
longest matching path wins; on an equal-length tie ``Allow`` beats ``Disallow``.

Percent-encoded octets are compared literally rather than decoded, which is
stricter than RFC 9309 in the rare case of an encoded pattern: a stricter gate
can only under-collect, never over-collect.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

import httpx

__all__ = [
    "DEFAULT_USER_AGENT",
    "PolicyDecision",
    "PolicyDenied",
    "RobotsDenied",
    "RobotsDocument",
    "RobotsGate",
    "RobotsGroup",
    "RobotsResource",
    "RobotsRule",
    "document_from_resource",
    "http_robots_fetcher",
    "main",
    "origin_of",
    "parse_robots",
    "request_target",
]

#: Identifying user agent. It names the project and links to the repository so a
#: site operator can find us; it is never swapped to impersonate a browser.
DEFAULT_USER_AGENT: Final = "OrchardPriceBot/1.0 (+https://github.com/Steve-M365/apple-price-au)"

#: Statuses that mean "this origin publishes no robots.txt", i.e. allow all.
ROBOTS_ABSENT_STATUSES: Final = frozenset({404, 410})

#: Upper bound on the robots.txt body we will parse, in characters. A policy file
#: larger than this is pathological; truncating keeps the fetch polite.
MAX_ROBOTS_BYTES: Final = 512_000

_LINE: Final = re.compile(r"^(?P<key>[A-Za-z][A-Za-z-]*)\s*:\s*(?P<value>.*)$")


class PolicyDenied(Exception):  # noqa: N818 - the card names this exception
    """A source refused automated access, or the policy gate said no.

    This is a **decision**, not a transient fault. Nothing in the pipeline may
    retry it: retrying a refusal is how a crawler becomes abusive. Adapters
    convert it into a :class:`scripts.collectors.base.BlockedSource` and route
    the seller to its curated feed.
    """

    def __init__(self, url: str, reason: str, *, status: int | None = None) -> None:
        super().__init__(f"{reason} ({url})")
        self.url = url
        self.reason = reason
        self.status = status


#: The card's vocabulary for a robots.txt refusal. Same class, deliberately: both
#: are policy decisions, and neither is retried.
RobotsDenied = PolicyDenied


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def origin_of(url: str) -> str:
    """Return ``scheme://host[:port]`` for an absolute http(s) URL."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"{url!r} is not an absolute http(s) URL")
    return f"{parts.scheme}://{parts.netloc}"


def request_target(url: str) -> str:
    """Return the path (plus query) that robots.txt patterns are matched against."""
    parts = urlsplit(url)
    path = parts.path or "/"
    return f"{path}?{parts.query}" if parts.query else path


@dataclass(frozen=True, slots=True)
class RobotsResource:
    """The raw outcome of fetching one ``robots.txt``.

    ``status_code`` is ``None`` only when transport failed before a response
    existed; the fetcher raises in that case and the gate synthesises the
    denial, so a resource always carries a real status.
    """

    url: str
    status_code: int
    content_type: str
    body: str
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class RobotsRule:
    """One ``Allow`` or ``Disallow`` line, with its path pattern."""

    allow: bool
    pattern: str


@dataclass(frozen=True, slots=True)
class RobotsGroup:
    """Rules shared by one or more user agents."""

    agents: tuple[str, ...]
    rules: tuple[RobotsRule, ...]
    crawl_delay_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class RobotsDocument:
    """A parsed (or deliberately unparsed) robots.txt for one origin.

    ``readable`` is False when the file could not be obtained or understood. A
    document that is not readable may only ever produce a denial.
    """

    robots_url: str
    status_code: int | None
    content_type: str
    body: str
    fetched_at: datetime
    groups: tuple[RobotsGroup, ...]
    readable: bool
    reason: str


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """The answer for one URL. ``allowed=False`` is final."""

    allowed: bool
    reason: str
    crawl_delay_seconds: float | None
    robots_url: str
    fetched_at: datetime
    user_agent: str
    status: int | None = None
    matched_rule: str | None = None


def _crawl_delay(value: str) -> float | None:
    try:
        parsed = float(value.strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def parse_robots(text: str) -> tuple[RobotsGroup, ...]:
    """Parse a robots.txt body into groups.

    A new ``User-agent`` line after any rule opens a new group, and a blank line
    closes the current one, which is the reading every mainstream crawler uses.
    A rule appearing outside a group is ignored rather than guessed at.
    """
    groups: list[RobotsGroup] = []
    agents: list[str] = []
    rules: list[RobotsRule] = []
    crawl_delay: float | None = None

    def flush() -> None:
        nonlocal agents, rules, crawl_delay
        if agents:
            groups.append(RobotsGroup(tuple(agents), tuple(rules), crawl_delay))
        agents, rules, crawl_delay = [], [], None

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            flush()
            continue
        match = _LINE.match(line)
        if match is None:
            continue
        key = match.group("key").lower()
        value = match.group("value").strip()
        if key == "user-agent":
            if rules or crawl_delay is not None:
                flush()
            if value:
                agents.append(value)
        elif key in {"allow", "disallow"}:
            if not agents:
                continue
            # An empty value means "nothing to disallow" and is not a rule.
            if value:
                rules.append(RobotsRule(allow=key == "allow", pattern=value))
        elif key == "crawl-delay":
            if not agents:
                continue
            delay = _crawl_delay(value)
            if delay is not None:
                crawl_delay = delay
    flush()
    return tuple(groups)


def document_from_resource(resource: RobotsResource) -> RobotsDocument:
    """Turn a fetched resource into a document, applying the failure policy."""
    status = resource.status_code
    if status in ROBOTS_ABSENT_STATUSES:
        return RobotsDocument(
            robots_url=resource.url,
            status_code=status,
            content_type=resource.content_type,
            body=resource.body,
            fetched_at=resource.fetched_at,
            groups=(),
            readable=True,
            reason=(
                f"robots.txt is absent (HTTP {status}); RFC 9309 treats a missing "
                "policy file as permission for all paths"
            ),
        )
    if not 200 <= status <= 299:
        return RobotsDocument(
            robots_url=resource.url,
            status_code=status,
            content_type=resource.content_type,
            body=resource.body,
            fetched_at=resource.fetched_at,
            groups=(),
            readable=False,
            reason=(
                f"robots.txt could not be read (HTTP {status}); an unreadable "
                "policy is not permission, so this origin is denied"
            ),
        )
    return RobotsDocument(
        robots_url=resource.url,
        status_code=status,
        content_type=resource.content_type,
        body=resource.body[:MAX_ROBOTS_BYTES],
        fetched_at=resource.fetched_at,
        groups=parse_robots(resource.body[:MAX_ROBOTS_BYTES]),
        readable=True,
        reason=f"robots.txt read (HTTP {status})",
    )


def _pattern_matches(pattern: str, target: str) -> bool:
    """Match one robots.txt pattern against a path+query, honouring ``*`` and ``$``."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    expression = ".*".join(re.escape(part) for part in body.split("*"))
    return re.match(expression + ("$" if anchored else ""), target) is not None


def _governing_groups(groups: Iterable[RobotsGroup], user_agent: str) -> tuple[RobotsGroup, ...]:
    """Select the groups that govern ``user_agent``.

    The most specific matching user-agent token wins outright; ``*`` is only a
    fallback when nothing names us. Groups are not merged across specificity
    levels, because a specific group is an explicit instruction.
    """
    token = user_agent.lower()
    specific: list[tuple[int, RobotsGroup]] = []
    generic: list[RobotsGroup] = []
    for group in groups:
        for agent in group.agents:
            name = agent.strip().lower()
            if name == "*":
                if group not in generic:
                    generic.append(group)
                continue
            if name and name in token:
                specific.append((len(name), group))
    if not specific:
        return tuple(generic)
    longest = max(length for length, _ in specific)
    picked: dict[int, RobotsGroup] = {}
    for length, group in specific:
        if length == longest:
            picked[id(group)] = group
    return tuple(picked.values())


class RobotsGate:
    """Per-origin robots.txt gate with a run-scoped cache.

    The fetcher and clock are injected so tests can run with the network off and
    a virtual clock. The default fetcher is a single, plain, unauthenticated GET.
    """

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        fetcher: Callable[[str], RobotsResource] | None = None,
        clock: Callable[[], datetime] = utc_now,
        timeout_seconds: float = 20.0,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("a user agent must identify the crawler; empty is not allowed")
        self._user_agent = user_agent
        self._fetcher = fetcher or http_robots_fetcher(
            timeout_seconds=timeout_seconds, user_agent=user_agent
        )
        self._clock = clock
        self._cache: dict[str, RobotsDocument] = {}

    @property
    def user_agent(self) -> str:
        return self._user_agent

    def fetch(self, url: str) -> RobotsDocument:
        """Return the (cached) robots.txt document for ``url``'s origin."""
        origin = origin_of(url)
        cached = self._cache.get(origin)
        if cached is not None:
            return cached
        robots_url = f"{origin}/robots.txt"
        try:
            document = document_from_resource(self._fetcher(robots_url))
        except (httpx.HTTPError, OSError, ValueError) as exc:
            document = RobotsDocument(
                robots_url=robots_url,
                status_code=None,
                content_type="",
                body="",
                fetched_at=self._clock(),
                groups=(),
                readable=False,
                reason=(
                    f"robots.txt could not be read ({type(exc).__name__}: {exc}); "
                    "an unreadable policy is not permission, so this origin is denied"
                ),
            )
        self._cache[origin] = document
        return document

    def is_allowed(self, url: str, user_agent: str | None = None) -> PolicyDecision:
        """Decide whether ``user_agent`` may fetch ``url``, and why."""
        agent = user_agent or self._user_agent
        document = self.fetch(url)

        if not document.readable:
            return PolicyDecision(
                allowed=False,
                reason=document.reason,
                crawl_delay_seconds=None,
                robots_url=document.robots_url,
                fetched_at=document.fetched_at,
                user_agent=agent,
                status=document.status_code,
            )

        governing = _governing_groups(document.groups, agent)
        delays = [
            group.crawl_delay_seconds
            for group in governing
            if group.crawl_delay_seconds is not None
        ]
        crawl_delay = max(delays) if delays else None
        rules = [rule for group in governing for rule in group.rules]
        target = request_target(url)

        matching = [rule for rule in rules if _pattern_matches(rule.pattern, target)]
        if not matching:
            if rules:
                reason = (
                    f"robots.txt allows {target}: {len(rules)} rule(s) apply to this "
                    "user agent and none match"
                )
            else:
                reason = (
                    f"{document.reason}; no rule applies to this user agent, so "
                    f"{target} is unrestricted"
                )
            return PolicyDecision(
                allowed=True,
                reason=reason,
                crawl_delay_seconds=crawl_delay,
                robots_url=document.robots_url,
                fetched_at=document.fetched_at,
                user_agent=agent,
                status=document.status_code,
            )

        # Longest pattern wins; on an equal-length tie Allow beats Disallow.
        winner = max(matching, key=lambda rule: (len(rule.pattern), rule.allow))
        label = "Allow" if winner.allow else "Disallow"
        return PolicyDecision(
            allowed=winner.allow,
            reason=(
                f"robots.txt {label}: '{winner.pattern}' is the most specific match for {target}"
            ),
            crawl_delay_seconds=crawl_delay,
            robots_url=document.robots_url,
            fetched_at=document.fetched_at,
            user_agent=agent,
            status=document.status_code,
            matched_rule=f"{label}: {winner.pattern}",
        )


def http_robots_fetcher(
    *, timeout_seconds: float = 20.0, user_agent: str = DEFAULT_USER_AGENT
) -> Callable[[str], RobotsResource]:
    """Return a fetcher that performs one plain GET of a robots.txt file."""

    def fetch(robots_url: str) -> RobotsResource:
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/plain,*/*;q=0.8",
        }
        with httpx.Client(
            timeout=timeout_seconds, follow_redirects=True, headers=headers
        ) as client:
            response = client.get(robots_url)
        return RobotsResource(
            url=str(response.url),
            status_code=response.status_code,
            content_type=response.headers.get("content-type", ""),
            body=response.text[:MAX_ROBOTS_BYTES],
            fetched_at=utc_now(),
        )

    return fetch


# --------------------------------------------------------------------------
# Recording a finding as evidence
# --------------------------------------------------------------------------


def build_finding(
    seller_id: str,
    decisions: Sequence[PolicyDecision],
    *,
    evidence_file: str,
    notes: str = "",
) -> dict[str, object]:
    """Assemble one ``policy_findings.json`` record from checked decisions."""
    if not decisions:
        raise ValueError("a policy finding needs at least one checked URL")
    first = decisions[0]
    delays = [
        decision.crawl_delay_seconds
        for decision in decisions
        if decision.crawl_delay_seconds is not None
    ]
    verdicts = "; ".join(
        f"{'ALLOWED' if decision.allowed else 'DENIED'} ({decision.reason})"
        for decision in decisions
    )
    return {
        "seller_id": seller_id,
        "robots_url": first.robots_url,
        "status": first.status,
        "allowed": all(decision.allowed for decision in decisions),
        "crawl_delay_seconds": max(delays) if delays else None,
        "checked_at": first.fetched_at.isoformat().replace("+00:00", "Z"),
        "evidence_file": evidence_file,
        "notes": f"{verdicts}. {notes}".strip(),
    }


def record_finding(path: Path, finding: Mapping[str, object]) -> None:
    """Insert or replace one seller's finding, leaving other sellers untouched."""
    findings: list[object] = []
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and isinstance(existing.get("findings"), list):
            findings = [
                item
                for item in existing["findings"]
                if not (isinstance(item, dict) and item.get("seller_id") == finding["seller_id"])
            ]
    findings.append(dict(finding))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"findings": findings}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _evidence_path(directory: Path, stem: str) -> Path:
    return directory / f"{stem}.txt"


def _parse_checked_at(value: str | None) -> datetime:
    """Parse an evidence timestamp, requiring an explicit offset."""
    if not value:
        return utc_now()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--checked-at must include an offset, e.g. 2026-11-15T11:04:23Z")
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.collectors.policy",
        description="Check robots.txt for a seller and record the finding as evidence.",
    )
    parser.add_argument("--seller", required=True, help="seller_id the finding is filed under")
    parser.add_argument(
        "--url",
        action="append",
        required=True,
        dest="urls",
        help="URL to check; repeat for every representative path",
    )
    parser.add_argument(
        "--findings",
        type=Path,
        default=Path("data/health/policy_findings.json"),
        help="findings file to update",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path("fixtures/policy"),
        help="directory that receives the saved robots.txt body",
    )
    parser.add_argument(
        "--evidence-stem",
        default=None,
        help="filename stem for the saved body (defaults to the origin with dots replaced)",
    )
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--from-file",
        type=Path,
        default=None,
        help=(
            "re-render the finding from a saved robots.txt body instead of fetching; "
            "combine with --checked-at to keep the original capture timestamp"
        ),
    )
    parser.add_argument(
        "--checked-at",
        default=None,
        help="ISO 8601 capture timestamp to record with --from-file (defaults to now)",
    )
    parser.add_argument(
        "--content-type",
        default="text/plain",
        help="content type to record when replaying with --from-file",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="print the decision without touching findings or fixtures",
    )
    args = parser.parse_args(argv)

    if args.from_file is not None:
        body = args.from_file.read_text(encoding="utf-8")
        checked_at = _parse_checked_at(args.checked_at)

        def replay(url: str) -> RobotsResource:
            return RobotsResource(
                url=url,
                status_code=200,
                content_type=args.content_type,
                body=body,
                fetched_at=checked_at,
            )

        gate = RobotsGate(user_agent=args.user_agent, fetcher=replay, clock=lambda: checked_at)
    else:
        gate = RobotsGate(user_agent=args.user_agent, timeout_seconds=args.timeout)
    decisions = [gate.is_allowed(url, args.user_agent) for url in args.urls]
    document = gate.fetch(args.urls[0])

    stem = args.evidence_stem or re.sub(r"[^a-z0-9]+", "_", origin_of(args.urls[0]).lower())

    if not args.no_save:
        evidence = _evidence_path(args.fixtures, stem)
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(document.body, encoding="utf-8")
        finding = build_finding(
            args.seller,
            decisions,
            evidence_file=str(evidence),
            notes=f"robots.txt content-type: {document.content_type or 'unknown'}.",
        )
        record_finding(args.findings, finding)

    for url, decision in zip(args.urls, decisions, strict=True):
        verdict = "ALLOWED" if decision.allowed else "DENIED"
        print(f"{verdict} {url} (robots: {decision.robots_url})")
        print(f"  status={decision.status} crawl_delay={decision.crawl_delay_seconds}")
        print(f"  reason: {decision.reason}")
    return 0 if all(decision.allowed for decision in decisions) else 2


if __name__ == "__main__":  # pragma: no cover
    # ``python -m scripts.collectors.policy`` warns that the module is already in
    # sys.modules because the package re-exports it. That warning is benign; the
    # command is the supported way to record a finding.
    sys.exit(main())
