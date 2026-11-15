"""Collection framework: adapters, the robots policy gate and the HTTP client.

Nothing in this package may fetch a retailer without a policy decision. The
public surface is the small set exported here; adapters should import from the
package rather than from individual modules.

    from scripts.collectors import BaseAdapter, RobotsGate, HttpClient

    class ExampleAdapter(BaseAdapter):
        seller_id = "example"
        capabilities = Capabilities(product_page=True, stock=True)

        def policy_urls(self) -> Sequence[str]:
            return ("https://www.example.com.au/au/shop/product/thing",)

        def _collect(self, targets: list[Product]) -> CollectionResult:
            ...
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import (
    BaseAdapter,
    BlockedSource,
    Capabilities,
    CollectionError,
    CollectionResult,
    ConcurrencyGate,
    PolicyResult,
    RateLimiter,
    SellerNotCollectableError,
    SellerPolicy,
    SourceAdapter,
    load_seller_policies,
    shared_concurrency_gate,
)
from .http import (
    DiskCache,
    HttpClient,
    HttpError,
    HttpResponse,
    HttpSettings,
    NetworkError,
    RateLimitError,
    UpstreamError,
    load_settings,
)
from .policy import (
    DEFAULT_USER_AGENT,
    PolicyDecision,
    PolicyDenied,
    RobotsDenied,
    RobotsDocument,
    RobotsGate,
    RobotsGroup,
    RobotsResource,
    RobotsRule,
    http_robots_fetcher,
    parse_robots,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .apple_au import AppleAuAdapter

#: Adapters are resolved on first access rather than imported eagerly. A collector
#: that imports itself through this package would otherwise be found in
#: ``sys.modules`` before ``python -m`` executes it, and runpy warns about that
#: double import on every run.
_LAZY_EXPORTS = {"AppleAuAdapter": ".apple_au"}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(target, __package__), name)


__all__ = [
    "DEFAULT_USER_AGENT",
    "AppleAuAdapter",
    "BaseAdapter",
    "BlockedSource",
    "Capabilities",
    "CollectionError",
    "CollectionResult",
    "ConcurrencyGate",
    "DiskCache",
    "HttpClient",
    "HttpError",
    "HttpResponse",
    "HttpSettings",
    "NetworkError",
    "PolicyDecision",
    "PolicyDenied",
    "PolicyResult",
    "RateLimitError",
    "RateLimiter",
    "RobotsDenied",
    "RobotsDocument",
    "RobotsGate",
    "RobotsGroup",
    "RobotsResource",
    "RobotsRule",
    "SellerNotCollectableError",
    "SellerPolicy",
    "SourceAdapter",
    "UpstreamError",
    "http_robots_fetcher",
    "load_seller_policies",
    "load_settings",
    "parse_robots",
    "shared_concurrency_gate",
]
