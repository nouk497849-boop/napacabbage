from __future__ import annotations

from ..config import AppConfig
from ..models import Quote
from .base import BaseAdapter, SourceContext


class SkyscannerAdapter(BaseAdapter):
    """Optional partner-only adapter.

    Skyscanner access and use cases depend on partner approval and booking deeplink
    requirements, so this adapter is deliberately disabled unless SKYSCANNER_ENABLED
    is true and SKYSCANNER_API_KEY is present. It is wired into the source registry
    so credentials can be added later without changing the pipeline.
    """

    name = "skyscanner"

    def enabled(self, config: AppConfig) -> bool:
        return bool(config.api.skyscanner_enabled and config.api.skyscanner_api_key)

    def discover(self, ctx: SourceContext) -> list[Quote]:
        return []

    def verify(self, ctx: SourceContext, candidates: list[Quote]) -> list[Quote]:
        return []
