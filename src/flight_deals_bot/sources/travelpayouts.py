from __future__ import annotations

import urllib.parse
from datetime import date, timedelta

from ..config import AppConfig
from ..dates import parse_date
from ..models import Cabin, Quote, Segment
from .base import BaseAdapter, SourceContext, safe_decimal


TRIP_CLASS_TO_CABIN = {
    0: Cabin.ECONOMY,
    1: Cabin.BUSINESS,
    2: Cabin.FIRST,
}

CABIN_TO_TRIP_CLASS = {value: key for key, value in TRIP_CLASS_TO_CABIN.items()}


class TravelpayoutsAdapter(BaseAdapter):
    name = "travelpayouts"
    endpoint = "https://api.travelpayouts.com/v2/prices/latest"
    prices_for_dates_endpoint = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

    def enabled(self, config: AppConfig) -> bool:
        return bool(config.api.travelpayouts_token)

    def discover(self, ctx: SourceContext) -> list[Quote]:
        token = ctx.config.api.travelpayouts_token
        if not token:
            return []
        if Cabin.ECONOMY not in ctx.config.search.cabins:
            ctx.note(self.name, "Travelpayouts 快取票價目前只支援經濟艙；因為未啟用 economy，所以略過")
            return []
        skipped_cabins = [cabin.value for cabin in ctx.config.search.cabins if cabin is not Cabin.ECONOMY]
        if skipped_cabins:
            ctx.note(
                self.name,
                f"Travelpayouts 快取票價目前只支援經濟艙；已略過艙等：{', '.join(skipped_cabins)}",
            )
        quotes: list[Quote] = []
        for origin in ctx.config.search.origins:
            payload = ctx.get_json(
                self.name,
                self.endpoint,
                params={
                    "currency": ctx.config.search.currency.lower(),
                    "period_type": "year",
                    "page": 1,
                    "limit": 100,
                    "show_to_affiliates": "true",
                    "sorting": "price",
                    "origin": origin,
                    "trip_class": CABIN_TO_TRIP_CLASS[Cabin.ECONOMY],
                },
                headers={"x-access-token": token},
            )
            if payload:
                quotes.extend(
                    self.parse_latest(
                        payload,
                        default_cabin=Cabin.ECONOMY,
                        currency=ctx.config.search.currency,
                        marker=ctx.config.api.travelpayouts_marker,
                        adults=ctx.config.search.adults,
                    )
                )
        return [quote for quote in quotes if self._within_search_window(ctx, quote)]

    def verify(self, ctx: SourceContext, candidates: list[Quote]) -> list[Quote]:
        token = ctx.config.api.travelpayouts_token
        if not token:
            return []
        quotes: list[Quote] = []
        seen: set[tuple[str, str, str, str]] = set()
        for candidate in candidates:
            if candidate.return_date is None or candidate.cabin is not Cabin.ECONOMY:
                continue
            signature = (
                candidate.origin,
                candidate.destination,
                candidate.departure_date.isoformat(),
                candidate.return_date.isoformat(),
            )
            if signature in seen:
                continue
            seen.add(signature)
            payload = ctx.get_json(
                self.name,
                self.prices_for_dates_endpoint,
                params={
                    "origin": candidate.origin,
                    "destination": candidate.destination,
                    "departure_at": candidate.departure_date.isoformat(),
                    "return_at": candidate.return_date.isoformat(),
                    "one_way": "false",
                    "direct": "false",
                    "currency": ctx.config.search.currency.lower(),
                    "market": "tw",
                    "sorting": "price",
                    "unique": "false",
                    "limit": 10,
                    "page": 1,
                    "token": token,
                },
                headers={"x-access-token": token},
            )
            if payload:
                quotes.extend(
                    self.parse_prices_for_dates(
                        payload,
                        fallback=candidate,
                        currency=ctx.config.search.currency,
                        marker=ctx.config.api.travelpayouts_marker,
                        adults=ctx.config.search.adults,
                    )
                )
        return [quote for quote in quotes if self._within_search_window(ctx, quote)]

    def parse_latest(
        self,
        payload: dict,
        default_cabin: Cabin,
        currency: str,
        marker: str | None = None,
        adults: int = 1,
    ) -> list[Quote]:
        data = payload.get("data") or []
        if isinstance(data, dict):
            data = list(data.values())
        quotes: list[Quote] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            price = safe_decimal(item.get("value", item.get("price")))
            origin = item.get("origin")
            destination = item.get("destination")
            departure = parse_date(item.get("depart_date") or item.get("departure_at"))
            return_date = parse_date(item.get("return_date") or item.get("return_at"))
            if not (price and origin and destination and departure):
                continue
            trip_class = item.get("trip_class")
            cabin = TRIP_CLASS_TO_CABIN.get(int(trip_class), default_cabin) if trip_class is not None else default_cabin
            flight_number = item.get("flight_number")
            segments = ()
            if flight_number:
                segments = (
                    Segment(
                        origin=str(item.get("origin_airport") or origin),
                        destination=str(item.get("destination_airport") or destination),
                        marketing_carrier=item.get("airline"),
                        flight_number=str(flight_number),
                        cabin=cabin,
                    ),
                )
            quotes.append(
                Quote(
                    source=self.name,
                    origin=str(origin),
                    destination=str(destination),
                    departure_date=departure,
                    return_date=return_date,
                    cabin=cabin,
                    price=price,
                    currency=str(item.get("currency") or currency).upper(),
                    airline=item.get("airline"),
                    stops=_int_or_none(item.get("number_of_changes", item.get("transfers"))),
                    booking_url=_booking_url(item, origin, destination, departure, return_date, cabin, currency, marker, adults),
                    segments=segments,
                    raw=item,
                    verified=False,
                    notes=("Travelpayouts 快取票價，實際票價與座位請點連結重新確認。",),
                )
            )
        return quotes

    def parse_prices_for_dates(
        self,
        payload: dict,
        fallback: Quote,
        currency: str,
        marker: str | None = None,
        adults: int = 1,
    ) -> list[Quote]:
        data = payload.get("data") or []
        if isinstance(data, dict):
            data = list(data.values())
        quotes: list[Quote] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            price = safe_decimal(item.get("price", item.get("value")))
            departure = parse_date(item.get("departure_at") or item.get("depart_date")) or fallback.departure_date
            return_date = parse_date(item.get("return_at") or item.get("return_date")) or fallback.return_date
            if not price:
                continue
            origin = str(item.get("origin") or item.get("origin_code") or fallback.origin)
            destination = str(item.get("destination") or item.get("destination_code") or fallback.destination)
            airline = item.get("airline") or fallback.airline
            flight_number = item.get("flight_number")
            segments = ()
            if airline or flight_number or item.get("origin_airport") or item.get("destination_airport"):
                segments = (
                    Segment(
                        origin=str(item.get("origin_airport") or origin),
                        destination=str(item.get("destination_airport") or destination),
                        marketing_carrier=airline,
                        flight_number=str(flight_number) if flight_number else None,
                        cabin=fallback.cabin,
                        duration_minutes=_int_or_none(item.get("duration_to") or item.get("duration")),
                    ),
                )
            quotes.append(
                Quote(
                    source=self.name,
                    origin=origin,
                    destination=destination,
                    departure_date=departure,
                    return_date=return_date,
                    cabin=fallback.cabin,
                    price=price,
                    currency=str(item.get("currency") or currency).upper(),
                    airline=airline,
                    stops=_int_or_none(item.get("transfers", item.get("number_of_changes"))),
                    booking_url=_booking_url(item, origin, destination, departure, return_date, fallback.cabin, currency, marker, adults),
                    segments=segments,
                    raw=item,
                    verified=True,
                    baseline_price_hint=fallback.baseline_price_hint,
                    notes=("Travelpayouts prices_for_dates 補到較完整的快取票價與 Aviasales 搜尋結果，實際票價仍請點進去確認。",),
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


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _booking_url(
    item: dict,
    origin: object,
    destination: object,
    departure: date,
    return_date: date | None,
    cabin: Cabin,
    currency: str,
    marker: str | None,
    adults: int,
) -> str:
    link = item.get("link") or item.get("url")
    if isinstance(link, str) and link.strip():
        url = _normalize_aviasales_link(link.strip())
        if marker:
            return _add_query_params(url, {"marker": marker})
        return url
    return _prefilled_aviasales_search_link(
        origin=str(origin),
        destination=str(destination),
        departure=departure,
        return_date=return_date,
        cabin=cabin,
        currency=currency,
        marker=marker,
        adults=adults,
    )


def _normalize_aviasales_link(link: str) -> str:
    if link.startswith(("https://", "http://")):
        return link
    if link.startswith("/search/"):
        return "https://www.aviasales.com" + link
    if link.startswith("/"):
        return "https://www.aviasales.com/search" + link
    return "https://www.aviasales.com/search/" + link


def _prefilled_aviasales_search_link(
    origin: str,
    destination: str,
    departure: date,
    return_date: date | None,
    cabin: Cabin,
    currency: str,
    marker: str | None,
    adults: int,
) -> str:
    search_code = _aviasales_search_code(origin, destination, departure, return_date, adults)
    params = {
        "trip_class": CABIN_TO_TRIP_CLASS.get(cabin, 0),
        "currency": currency.upper(),
        "locale": "zh",
    }
    if marker:
        params["marker"] = marker
    return f"https://www.aviasales.com/search/{search_code}?" + urllib.parse.urlencode(params)


def _add_query_params(url: str, params: dict[str, str]) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    existing = {key for key, _value in query}
    query.extend((key, value) for key, value in params.items() if key not in existing)
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))


def _aviasales_search_code(origin: str, destination: str, departure: date, return_date: date | None, adults: int) -> str:
    passenger_count = max(int(adults or 1), 1)
    code = f"{origin.upper()}{departure:%d%m}{destination.upper()}"
    if return_date:
        code += f"{return_date:%d%m}"
    return f"{code}{passenger_count}"
