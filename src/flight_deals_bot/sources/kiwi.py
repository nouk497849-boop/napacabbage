from __future__ import annotations

from datetime import date, timedelta

from ..config import AppConfig
from ..dates import format_kiwi_date, parse_date
from ..models import Cabin, Quote, Segment
from .base import BaseAdapter, SourceContext, safe_decimal


KIWI_CABINS = {
    Cabin.ECONOMY: "M",
    Cabin.PREMIUM_ECONOMY: "W",
    Cabin.BUSINESS: "C",
    Cabin.FIRST: "F",
}


class KiwiAdapter(BaseAdapter):
    name = "kiwi"
    endpoint = "https://tequila-api.kiwi.com/v2/search"

    def enabled(self, config: AppConfig) -> bool:
        return bool(config.api.kiwi_api_key)

    def discover(self, ctx: SourceContext) -> list[Quote]:
        key = ctx.config.api.kiwi_api_key
        if not key:
            return []
        today = date.today()
        date_from = today + timedelta(days=ctx.config.search.min_days_ahead)
        date_to = today + timedelta(days=ctx.config.search.max_days_ahead)
        quotes: list[Quote] = []
        for origin in ctx.config.search.origins:
            for cabin in ctx.config.search.cabins:
                payload = ctx.get_json(
                    self.name,
                    self.endpoint,
                    params={
                        "fly_from": origin,
                        "fly_to": "anywhere",
                        "dateFrom": format_kiwi_date(date_from),
                        "dateTo": format_kiwi_date(date_to),
                        "nights_in_dst_from": min(ctx.config.search.stay_lengths),
                        "nights_in_dst_to": max(ctx.config.search.stay_lengths),
                        "flight_type": "round",
                        "adults": ctx.config.search.adults,
                        "curr": ctx.config.search.currency,
                        "selected_cabins": KIWI_CABINS[cabin],
                        "max_stopovers": 4,
                        "limit": 50,
                        "sort": "price",
                    },
                    headers={"apikey": key},
                )
                if payload:
                    quotes.extend(self.parse_search(payload, fallback_cabin=cabin, currency=ctx.config.search.currency))
        return [quote for quote in quotes if quote.stay_nights in ctx.config.search.stay_lengths]

    def verify(self, ctx: SourceContext, candidates: list[Quote]) -> list[Quote]:
        key = ctx.config.api.kiwi_api_key
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
                    "fly_from": candidate.origin,
                    "fly_to": candidate.destination,
                    "dateFrom": format_kiwi_date(candidate.departure_date),
                    "dateTo": format_kiwi_date(candidate.departure_date),
                    "return_from": format_kiwi_date(candidate.return_date),
                    "return_to": format_kiwi_date(candidate.return_date),
                    "flight_type": "round",
                    "adults": ctx.config.search.adults,
                    "curr": ctx.config.search.currency,
                    "selected_cabins": KIWI_CABINS[candidate.cabin],
                    "max_stopovers": 4,
                    "limit": 10,
                    "sort": "price",
                },
                headers={"apikey": key},
            )
            if payload:
                quotes.extend(self.parse_search(payload, fallback_cabin=candidate.cabin, currency=ctx.config.search.currency))
        return quotes

    def parse_search(self, payload: dict, fallback_cabin: Cabin, currency: str) -> list[Quote]:
        quotes: list[Quote] = []
        for item in payload.get("data") or []:
            price = safe_decimal(item.get("price"))
            origin = item.get("flyFrom")
            destination = item.get("flyTo")
            departure = parse_date(item.get("local_departure") or item.get("utc_departure"))
            return_date = parse_date(item.get("route", [{}])[-1].get("local_departure") if item.get("route") else None)
            if item.get("return_date"):
                return_date = parse_date(item.get("return_date"))
            if not (price and origin and destination and departure):
                continue
            segments = _segments(item.get("route") or [], fallback_cabin)
            quotes.append(
                Quote(
                    source=self.name,
                    origin=origin,
                    destination=destination,
                    departure_date=departure,
                    return_date=return_date,
                    cabin=fallback_cabin,
                    price=price,
                    currency=str(item.get("currency") or currency).upper(),
                    airline=item.get("airlines", [None])[0] if item.get("airlines") else None,
                    stops=_int_or_none(item.get("pnr_count")),
                    self_transfer=bool(item.get("has_airport_change") or item.get("throw_away_ticketing") or item.get("hidden_city_ticketing")),
                    booking_url=item.get("deep_link"),
                    segments=tuple(segments),
                    raw=item,
                    verified=True,
                    notes=("Kiwi 可能包含自轉機或 virtual interlining，請確認行李、轉機保障與退改規則。",),
                )
            )
        return quotes


def _segments(route: list[dict], fallback_cabin: Cabin) -> list[Segment]:
    segments: list[Segment] = []
    for raw in route:
        cabin = fallback_cabin
        raw_cabin = raw.get("fare_classes") or raw.get("cabin_class")
        if raw_cabin:
            try:
                cabin = Cabin.parse(str(raw_cabin)[0])
            except ValueError:
                cabin = fallback_cabin
        segments.append(
            Segment(
                origin=raw.get("flyFrom", ""),
                destination=raw.get("flyTo", ""),
                marketing_carrier=raw.get("airline"),
                flight_number=raw.get("flight_no"),
                cabin=cabin,
                aircraft=raw.get("aircraft") or raw.get("vehicle_type"),
            )
        )
    return segments


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
