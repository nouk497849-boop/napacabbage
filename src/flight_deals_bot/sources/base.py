from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import AppConfig
from ..http import HttpError, JsonHttpClient
from ..models import Quote, SourceLimits
from ..storage import Store


class SourceAdapter(Protocol):
    name: str

    def enabled(self, config: AppConfig) -> bool: ...
    def discover(self, ctx: "SourceContext") -> list[Quote]: ...
    def verify(self, ctx: "SourceContext", candidates: list[Quote]) -> list[Quote]: ...


@dataclass
class SourceContext:
    config: AppConfig
    http: JsonHttpClient
    store: Store
    diagnostics: dict[str, list[str]] = field(default_factory=dict)
    provider_request_counts: dict[str, int] = field(default_factory=dict)
    quota_blocked_sources: set[str] = field(default_factory=set)

    def note(self, source: str, message: str) -> None:
        entries = self.diagnostics.setdefault(source, [])
        if message not in entries:
            entries.append(message)

    def provider_request_count(self, source: str) -> int:
        return self.provider_request_counts.get(source, 0)

    def was_quota_blocked(self, source: str) -> bool:
        return source in self.quota_blocked_sources

    def get_json(
        self,
        source: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        if not self._quota_available(source):
            return None
        self._mark_provider_request(source)
        payload = self.http.get_json(url, params=params, headers=headers)
        self._record_successful_request(source)
        return payload

    def post_json(
        self,
        source: str,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        if not self._quota_available(source):
            return None
        self._mark_provider_request(source)
        response = self.http.post_json(url, payload=payload, headers=headers)
        self._record_successful_request(source)
        return response

    def post_form(
        self,
        source: str,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        if not self._quota_available(source):
            return None
        self._mark_provider_request(source)
        response = self.http.post_form(url, payload=payload, headers=headers)
        self._record_successful_request(source)
        return response

    def _quota_available(self, source: str) -> bool:
        limits = self.config.source_limits.get(source, SourceLimits(0, 0))
        allowed = self.store.quota_available(source, limits)
        if not allowed:
            self.quota_blocked_sources.add(source)
            self.note(source, "quota limit reached; skipped provider request")
        return allowed

    def _mark_provider_request(self, source: str) -> None:
        self.provider_request_counts[source] = self.provider_request_counts.get(source, 0) + 1

    def _record_successful_request(self, source: str) -> None:
        self.store.record_quota_usage(source)


class BaseAdapter:
    name = "base"

    def enabled(self, config: AppConfig) -> bool:
        return False

    def discover(self, ctx: SourceContext) -> list[Quote]:
        return []

    def verify(self, ctx: SourceContext, candidates: list[Quote]) -> list[Quote]:
        return []


def safe_decimal(value: object):
    from decimal import Decimal, InvalidOperation

    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        for prefix in ("TWD", "USD", "EUR", "NT$", "$", "€", "£"):
            value = value.replace(prefix, "")
        value = value.strip()
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def source_note_for_error(exc: Exception) -> str:
    if isinstance(exc, HttpError) and exc.status == 429:
        return "rate limited"
    return str(exc)
