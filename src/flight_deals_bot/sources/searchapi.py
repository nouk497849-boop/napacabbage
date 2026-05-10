from __future__ import annotations

from decimal import Decimal

from ..config import AppConfig
from ..dates import parse_datetime
from ..models import Cabin, Quote, Segment
from .base import BaseAdapter, SourceContext, safe_decimal


SEARCHAPI_CLASS = {
    Cabin.ECONOMY: "economy",
    Cabin.PREMIUM_ECONOMY: "premium_economy",
    Cabin.BUSINESS: "business",
    Cabin.FIRST: "first_class",
}


class SearchApiAdapter(BaseAdapter):
    name = "searchapi"
    endpoint = "https://www.searchapi.io/api/v1/search"

    def enabled(self, config: AppConfig) -> bool:
        return bool(config.api.searchapi_key)

    def verify(self, ctx: SourceContext, candidates: list[Quote]) -> list[Quote]:
        key = ctx.config.api.searchapi_key
        if not key:
            return []
        quotes: list[Quote] = []
        seen: set[tuple[str, str, str, str, Cabin]] = set()
        for candidate in candidates:
            if candidate.return_date is None:
                continue
            signature = (
                candidate.origin,
                candidate.destination,
                candidate.departure_date.isoformat(),
                candidate.return_date.isoformat(),
                candidate.cabin,
            )
            if signature in seen:
                continue
            seen.add(signature)
            payload = ctx.get_json(
                self.name,
                self.endpoint,
                params={
                    "api_key": key,
                    "engine": "google_flights",
                    "departure_id": candidate.origin,
                    "arrival_id": candidate.destination,
                    "outbound_date": candidate.departure_date.isoformat(),
                    "return_date": candidate.return_date.isoformat(),
                    "flight_type": "round_trip",
                    "travel_class": SEARCHAPI_CLASS[candidate.cabin],
                    "currency": ctx.config.search.currency,
                    "gl": "tw",
                    "hl": "zh-tw",
                    "adults": ctx.config.search.adults,
                },
            )
            if payload:
                quotes.extend(self.parse_flights(payload, fallback=candidate, currency=ctx.config.search.currency))
        return quotes

    def parse_flights(self, payload: dict, fallback: Quote, currency: str) -> list[Quote]:
        results = []
        for key in ("best_flights", "flights", "other_flights"):
            value = payload.get(key)
            if isinstance(value, list):
                results.extend(value)
        metadata = payload.get("search_metadata") or {}
        booking_url = metadata.get("google_url") or metadata.get("html_url") or metadata.get("request_url")
        baseline_hint = _price_insight_hint(payload.get("price_insights") or {})
        quotes: list[Quote] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            price = safe_decimal(item.get("price"))
            if not price:
                continue
            segments = _parse_segments(item, fallback)
            airline = item.get("airline") or _first_airline(segments)
            stops = item.get("stops")
            if stops is None and segments:
                stops = max(len(segments) - 2, 0)
            quotes.append(
                Quote(
                    source=self.name,
                    origin=fallback.origin,
                    destination=fallback.destination,
                    departure_date=fallback.departure_date,
                    return_date=fallback.return_date,
                    cabin=fallback.cabin,
                    price=price,
                    currency=currency,
                    airline=airline,
                    stops=_int_or_none(stops),
                    booking_url=item.get("booking_url") or booking_url,
                    segments=tuple(segments),
                    raw=item,
                    verified=True,
                    baseline_price_hint=baseline_hint,
                )
            )
        return quotes


def _parse_segments(item: dict, fallback: Quote) -> list[Segment]:
    raw_segments = []
    for key in ("flights", "return_flights"):
        value = item.get(key)
        if isinstance(value, list):
            raw_segments.extend(value)
    segments: list[Segment] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue
        dep = raw.get("departure_airport") or {}
        arr = raw.get("arrival_airport") or {}
        travel_class = raw.get("travel_class")
        try:
            cabin = Cabin.parse(travel_class) if travel_class else fallback.cabin
        except ValueError:
            cabin = fallback.cabin
        segments.append(
            Segment(
                origin=dep.get("id") or dep.get("code") or fallback.origin,
                destination=arr.get("id") or arr.get("code") or fallback.destination,
                departure_at=parse_datetime(dep.get("time")),
                arrival_at=parse_datetime(arr.get("time")),
                marketing_carrier=raw.get("airline"),
                flight_number=raw.get("flight_number"),
                cabin=cabin,
                duration_minutes=_int_or_none(raw.get("duration")),
            )
        )
    return segments


def _price_insight_hint(price_insights: dict) -> Decimal | None:
    for key in ("typical_price", "usual_price", "median_price"):
        value = safe_decimal(price_insights.get(key))
        if value:
            return value
    price_range = price_insights.get("typical_price_range")
    if isinstance(price_range, list) and price_range:
        values = [safe_decimal(item) for item in price_range]
        values = [item for item in values if item]
        if values:
            return sum(values, Decimal("0")) / Decimal(len(values))
    return None


def _first_airline(segments: list[Segment]) -> str | None:
    for segment in segments:
        if segment.marketing_carrier:
            return segment.marketing_carrier
    return None


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
