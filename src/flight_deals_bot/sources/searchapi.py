from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from ..config import AppConfig
from ..dates import parse_date, parse_datetime
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

    def discover(self, ctx: SourceContext) -> list[Quote]:
        key = ctx.config.api.searchapi_key
        if not key:
            return []
        quotes: list[Quote] = []
        # SearchApi free tiers are small, so prioritize premium cabins on the
        # strongest Taiwan gateway first; quota limits stop the loop naturally.
        cabins = sorted(ctx.config.search.cabins, key=_cabin_priority)
        for origin in ctx.config.search.origins:
            for cabin in cabins:
                payload = ctx.get_json(
                    self.name,
                    self.endpoint,
                    params={
                        "api_key": key,
                        "engine": "google_travel_explore",
                        "departure_id": origin,
                        "time_period": "one_week_trip_in_the_next_six_months",
                        "travel_mode": "flights_only",
                        "travel_class": SEARCHAPI_CLASS[cabin],
                        "stops": "any",
                        "currency": ctx.config.search.currency,
                        "gl": "tw",
                        "hl": "zh-tw",
                        "adults": ctx.config.search.adults,
                    },
                )
                if payload:
                    quotes.extend(self.parse_explore(payload, origin=origin, cabin=cabin, currency=ctx.config.search.currency))
        return [quote for quote in quotes if self._within_search_window(ctx, quote)]

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

    def parse_explore(self, payload: dict, origin: str, cabin: Cabin, currency: str) -> list[Quote]:
        metadata = payload.get("search_metadata") or {}
        booking_url = metadata.get("html_url") or metadata.get("request_url")
        search_parameters = payload.get("search_parameters") or {}
        result_currency = str(search_parameters.get("currency") or currency).upper()
        quotes: list[Quote] = []
        for item in payload.get("destinations") or []:
            if not isinstance(item, dict):
                continue
            flight = item.get("flight") or {}
            price = safe_decimal(flight.get("price"))
            destination = flight.get("airport_code") or item.get("primary_airport")
            departure = parse_date(item.get("outbound_date") or item.get("alternative_outbound_date"))
            return_date = parse_date(item.get("return_date"))
            if not (price and destination and departure and return_date):
                continue
            airline = flight.get("airline_code") or flight.get("airline_name")
            if flight.get("airline_name") and flight.get("airline_code"):
                airline = f"{flight['airline_code']} {flight['airline_name']}"
            quotes.append(
                Quote(
                    source=self.name,
                    origin=origin,
                    destination=destination,
                    departure_date=departure,
                    return_date=return_date,
                    cabin=cabin,
                    price=price,
                    currency=result_currency,
                    airline=airline,
                    stops=_int_or_none(flight.get("stops")),
                    booking_url=booking_url,
                    raw=item,
                    verified=False,
                    notes=("SearchApi explore is a broad Google Travel candidate; verify before booking.",),
                )
            )
        return quotes

    def _within_search_window(self, ctx: SourceContext, quote: Quote) -> bool:
        today = date.today()
        min_date = today + timedelta(days=ctx.config.search.min_days_ahead)
        max_date = today + timedelta(days=ctx.config.search.max_days_ahead)
        if not (min_date <= quote.departure_date <= max_date):
            return False
        if quote.return_date is None:
            return False
        return quote.stay_nights in ctx.config.search.stay_lengths


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


def _cabin_priority(cabin: Cabin) -> int:
    return {
        Cabin.BUSINESS: 0,
        Cabin.FIRST: 1,
        Cabin.PREMIUM_ECONOMY: 2,
        Cabin.ECONOMY: 3,
    }[cabin]


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
