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

__all__ = [
    "DEFAULT_USER_AGENT",
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
