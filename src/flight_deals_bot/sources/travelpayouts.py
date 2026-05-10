from __future__ import annotations

from datetime import date, timedelta

from ..config import AppConfig
from ..dates import parse_date
from ..models import Cabin, Quote
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

    def enabled(self, config: AppConfig) -> bool:
        return bool(config.api.travelpayouts_token)

    def discover(self, ctx: SourceContext) -> list[Quote]:
        token = ctx.config.api.travelpayouts_token
        if not token:
            return []
        quotes: list[Quote] = []
        for origin in ctx.config.search.origins:
            for cabin in ctx.config.search.cabins:
                trip_class = CABIN_TO_TRIP_CLASS.get(cabin)
                if trip_class is None:
                    continue
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
                        "trip_class": trip_class,
                    },
                    headers={"x-access-token": token},
                )
                if payload:
                    quotes.extend(self.parse_latest(payload, default_cabin=cabin, currency=ctx.config.search.currency))
        return [quote for quote in quotes if self._within_search_window(ctx, quote)]

    def parse_latest(self, payload: dict, default_cabin: Cabin, currency: str) -> list[Quote]:
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
                    booking_url=item.get("link") or item.get("url"),
                    raw=item,
                    verified=False,
                    notes=("Travelpayouts latest prices are cached and should be rechecked before booking.",),
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
