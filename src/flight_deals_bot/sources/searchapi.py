from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from statistics import median

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
        explore_quotes = self._discover_explore(ctx, key)
        calendar_quotes = self._discover_calendar(ctx, key, explore_quotes)
        quotes = explore_quotes + calendar_quotes
        return [quote for quote in quotes if self._within_search_window(ctx, quote)]

    def _discover_explore(self, ctx: SourceContext, key: str) -> list[Quote]:
        if ctx.config.search.searchapi_explore_limit <= 0:
            ctx.note(self.name, "SearchApi Explore disabled by SEARCHAPI_EXPLORE_LIMIT=0")
            return []
        quotes: list[Quote] = []
        attempts = 0
        start_count = ctx.provider_request_count(self.name)
        cabins = sorted(ctx.config.search.cabins, key=_cabin_priority)
        for origin in ctx.config.search.origins:
            for cabin in cabins:
                if attempts >= ctx.config.search.searchapi_explore_limit:
                    sent = ctx.provider_request_count(self.name) - start_count
                    ctx.note(self.name, f"SearchApi Explore attempted {attempts} request(s), sent {sent}, parsed {len(quotes)} candidate(s)")
                    return quotes
                attempts += 1
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
                if ctx.was_quota_blocked(self.name):
                    sent = ctx.provider_request_count(self.name) - start_count
                    ctx.note(self.name, f"SearchApi Explore stopped by local quota after sending {sent} provider request(s)")
                    return quotes
                if payload:
                    quotes.extend(self.parse_explore(payload, origin=origin, cabin=cabin, currency=ctx.config.search.currency))
        sent = ctx.provider_request_count(self.name) - start_count
        ctx.note(self.name, f"SearchApi Explore attempted {attempts} request(s), sent {sent}, parsed {len(quotes)} candidate(s)")
        return quotes

    def _discover_calendar(self, ctx: SourceContext, key: str, explore_quotes: list[Quote]) -> list[Quote]:
        if ctx.config.search.searchapi_calendar_limit <= 0:
            ctx.note(self.name, "SearchApi Calendar disabled by SEARCHAPI_CALENDAR_LIMIT=0")
            return []
        if ctx.was_quota_blocked(self.name):
            ctx.note(self.name, "SearchApi Calendar skipped because local quota was already reached")
            return []
        quotes: list[Quote] = []
        attempts = 0
        rows_seen = 0
        start_count = ctx.provider_request_count(self.name)
        destinations = _calendar_destinations(ctx, explore_quotes)
        windows = _calendar_windows(ctx)
        cabins = sorted(ctx.config.search.cabins, key=_cabin_priority)
        min_stay = min(ctx.config.search.stay_lengths)
        max_stay = max(ctx.config.search.stay_lengths)
        for origin, destination, cabin, outbound_start, outbound_end in _calendar_plans(
            ctx.config.search.origins,
            destinations,
            cabins,
            windows,
        ):
            if attempts >= ctx.config.search.searchapi_calendar_limit:
                sent = ctx.provider_request_count(self.name) - start_count
                ctx.note(
                    self.name,
                    f"SearchApi Calendar attempted {attempts} request(s), sent {sent}, saw {rows_seen} row(s), parsed {len(quotes)} candidate(s)",
                )
                return quotes
            if origin == destination:
                continue
            return_start = outbound_start + timedelta(days=min_stay)
            return_end = outbound_end + timedelta(days=max_stay)
            attempts += 1
            payload = ctx.get_json(
                self.name,
                self.endpoint,
                params={
                    "api_key": key,
                    "engine": "google_flights_calendar",
                    "departure_id": origin,
                    "arrival_id": destination,
                    "outbound_date": outbound_start.isoformat(),
                    "return_date": return_start.isoformat(),
                    "outbound_date_start": outbound_start.isoformat(),
                    "outbound_date_end": outbound_end.isoformat(),
                    "return_date_start": return_start.isoformat(),
                    "return_date_end": return_end.isoformat(),
                    "flight_type": "round_trip",
                    "travel_class": SEARCHAPI_CLASS[cabin],
                    "stops": "any",
                    "separate_tickets": 0,
                    "currency": ctx.config.search.currency,
                    "gl": "tw",
                    "hl": "zh-tw",
                    "adults": ctx.config.search.adults,
                },
            )
            if ctx.was_quota_blocked(self.name):
                sent = ctx.provider_request_count(self.name) - start_count
                ctx.note(self.name, f"SearchApi Calendar stopped by local quota after sending {sent} provider request(s)")
                return quotes
            if payload:
                calendar_rows = payload.get("calendar") or []
                row_count = len(calendar_rows) if isinstance(calendar_rows, list) else 0
                rows_seen += row_count
                before = len(quotes)
                quotes.extend(
                    self.parse_calendar(
                        payload,
                        origin=origin,
                        destination=destination,
                        cabin=cabin,
                        currency=ctx.config.search.currency,
                        stay_lengths=ctx.config.search.stay_lengths,
                    )
                )
                parsed_count = len(quotes) - before
                if row_count == 0:
                    ctx.note(self.name, f"Calendar {origin}-{destination} {cabin.value} returned 0 row(s)")
                elif parsed_count == 0:
                    ctx.note(
                        self.name,
                        f"Calendar {origin}-{destination} {cabin.value} returned {row_count} row(s), but none matched STAY_LENGTHS",
                    )
            else:
                ctx.note(self.name, f"Calendar {origin}-{destination} {cabin.value} returned an empty response")
        sent = ctx.provider_request_count(self.name) - start_count
        ctx.note(
            self.name,
            f"SearchApi Calendar attempted {attempts} request(s), sent {sent}, saw {rows_seen} row(s), parsed {len(quotes)} candidate(s)",
        )
        return quotes

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

    def parse_calendar(
        self,
        payload: dict,
        origin: str,
        destination: str,
        cabin: Cabin,
        currency: str,
        stay_lengths: tuple[int, ...],
    ) -> list[Quote]:
        metadata = payload.get("search_metadata") or {}
        booking_url = metadata.get("google_url") or metadata.get("html_url") or metadata.get("request_url")
        search_parameters = payload.get("search_parameters") or {}
        result_currency = str(search_parameters.get("currency") or currency).upper()
        allowed_stays = set(stay_lengths)
        calendar_items = [item for item in payload.get("calendar") or [] if isinstance(item, dict)]
        prices = [
            price
            for price in (safe_decimal(item.get("price")) for item in calendar_items if not item.get("has_no_flights"))
            if price
        ]
        baseline_hint = Decimal(str(median(prices))) if prices else None
        quotes: list[Quote] = []
        for item in calendar_items:
            if item.get("has_no_flights"):
                continue
            price = safe_decimal(item.get("price"))
            departure = parse_date(item.get("departure"))
            return_date = parse_date(item.get("return"))
            if not (price and departure and return_date):
                continue
            stay_nights = (return_date - departure).days
            if allowed_stays and stay_nights not in allowed_stays:
                continue
            notes = ["SearchApi Google Flights Calendar candidate; verify before booking."]
            if item.get("is_lowest_price"):
                notes.append("Calendar marked this as a lowest-price date.")
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
                    booking_url=booking_url,
                    raw=item,
                    verified=False,
                    baseline_price_hint=baseline_hint,
                    notes=tuple(notes),
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


def _calendar_destinations(ctx: SourceContext, explore_quotes: list[Quote]) -> tuple[str, ...]:
    destinations: list[str] = []
    for quote in explore_quotes:
        if quote.destination not in destinations:
            destinations.append(quote.destination)
    for destination in ctx.config.search.searchapi_calendar_destinations:
        destination = destination.upper()
        if destination not in destinations:
            destinations.append(destination)
    return tuple(destinations)


def _calendar_windows(ctx: SourceContext) -> tuple[tuple[date, date], ...]:
    today = date.today()
    min_date = today + timedelta(days=ctx.config.search.min_days_ahead)
    max_date = today + timedelta(days=ctx.config.search.max_days_ahead)
    window_days = _safe_calendar_window_days(
        ctx.config.search.searchapi_calendar_outbound_window_days,
        ctx.config.search.stay_lengths,
    )
    step_days = max(window_days, ctx.config.search.searchapi_calendar_window_step_days)
    windows: list[tuple[date, date]] = []
    start = min_date
    while start <= max_date:
        end = min(start + timedelta(days=window_days - 1), max_date)
        windows.append((start, end))
        start += timedelta(days=step_days)
    return tuple(windows)


def _safe_calendar_window_days(requested_days: int, stay_lengths: tuple[int, ...]) -> int:
    requested_days = max(1, requested_days)
    spread = max(stay_lengths) - min(stay_lengths)
    safe_days = 1
    while (safe_days + 1) * ((safe_days + 1) + spread) <= 200:
        safe_days += 1
    return min(requested_days, safe_days)


def _calendar_plans(
    origins: tuple[str, ...],
    destinations: tuple[str, ...],
    cabins: list[Cabin],
    windows: tuple[tuple[date, date], ...],
):
    if not origins or not destinations or not cabins or not windows:
        return
    origin_pool = _weighted_origins(origins)
    total_unique = len(origins) * len(destinations) * len(cabins) * len(windows)
    seen: set[tuple[str, str, Cabin, date, date]] = set()
    max_iterations = max(total_unique * max(len(origin_pool), len(destinations), len(cabins), len(windows)), total_unique)
    index = 0
    while len(seen) < total_unique and index < max_iterations:
        origin = origin_pool[index % len(origin_pool)]
        destination = destinations[index % len(destinations)]
        cabin = cabins[index % len(cabins)]
        outbound_start, outbound_end = windows[index % len(windows)]
        index += 1
        key = (origin, destination, cabin, outbound_start, outbound_end)
        if key in seen:
            continue
        seen.add(key)
        yield key

    for origin in origins:
        for destination in destinations:
            for cabin in cabins:
                for outbound_start, outbound_end in windows:
                    key = (origin, destination, cabin, outbound_start, outbound_end)
                    if key in seen:
                        continue
                    seen.add(key)
                    yield key


def _weighted_origins(origins: tuple[str, ...]) -> tuple[str, ...]:
    preferred = ("TPE", "TPE", "TPE", "KHH", "TSA", "TPE", "RMQ", "TNN")
    origin_set = set(origins)
    weighted = [origin for origin in preferred if origin in origin_set]
    weighted.extend(origin for origin in origins if origin not in weighted)
    return tuple(weighted or origins)


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
