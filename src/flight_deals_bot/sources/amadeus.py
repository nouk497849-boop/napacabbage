from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from ..config import AppConfig
from ..dates import parse_date, parse_datetime
from ..models import Cabin, Quote, Segment
from .base import BaseAdapter, SourceContext, safe_decimal


AMADEUS_CLASS = {
    Cabin.ECONOMY: "ECONOMY",
    Cabin.PREMIUM_ECONOMY: "PREMIUM_ECONOMY",
    Cabin.BUSINESS: "BUSINESS",
    Cabin.FIRST: "FIRST",
}


class AmadeusAdapter(BaseAdapter):
    name = "amadeus"
    token_endpoint = "https://api.amadeus.com/v1/security/oauth2/token"
    inspiration_endpoint = "https://api.amadeus.com/v1/shopping/flight-destinations"
    offers_endpoint = "https://api.amadeus.com/v2/shopping/flight-offers"

    def __init__(self) -> None:
        self._access_token: str | None = None

    def enabled(self, config: AppConfig) -> bool:
        return bool(config.api.amadeus_client_id and config.api.amadeus_client_secret)

    def discover(self, ctx: SourceContext) -> list[Quote]:
        token = self._token(ctx)
        if not token:
            return []
        quotes: list[Quote] = []
        for origin in ctx.config.search.origins:
            payload = ctx.get_json(
                self.name,
                self.inspiration_endpoint,
                params={
                    "origin": origin,
                    "oneWay": "false",
                    "nonStop": "false",
                    "currency": ctx.config.search.currency,
                    "viewBy": "DESTINATION",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            if payload:
                quotes.extend(self.parse_inspiration(payload, currency=ctx.config.search.currency))
        return [quote for quote in quotes if self._within_search_window(ctx, quote)]

    def verify(self, ctx: SourceContext, candidates: list[Quote]) -> list[Quote]:
        token = self._token(ctx)
        if not token:
            return []
        quotes: list[Quote] = []
        seen: set[tuple[str, str, str, str, Cabin]] = set()
        for candidate in candidates:
            if candidate.return_date is None:
                continue
            key = (
                candidate.origin,
                candidate.destination,
                candidate.departure_date.isoformat(),
                candidate.return_date.isoformat(),
                candidate.cabin,
            )
            if key in seen:
                continue
            seen.add(key)
            payload = ctx.get_json(
                self.name,
                self.offers_endpoint,
                params={
                    "originLocationCode": candidate.origin,
                    "destinationLocationCode": candidate.destination,
                    "departureDate": candidate.departure_date.isoformat(),
                    "returnDate": candidate.return_date.isoformat(),
                    "adults": ctx.config.search.adults,
                    "travelClass": AMADEUS_CLASS[candidate.cabin],
                    "currencyCode": ctx.config.search.currency,
                    "nonStop": "false",
                    "max": 10,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            if payload:
                quotes.extend(self.parse_offers(payload, fallback=candidate, currency=ctx.config.search.currency))
        return quotes

    def parse_inspiration(self, payload: dict, currency: str) -> list[Quote]:
        quotes: list[Quote] = []
        for item in payload.get("data") or []:
            price = safe_decimal((item.get("price") or {}).get("total"))
            departure = parse_date(item.get("departureDate"))
            return_date = parse_date(item.get("returnDate"))
            if not (price and item.get("origin") and item.get("destination") and departure):
                continue
            links = item.get("links") or {}
            quotes.append(
                Quote(
                    source=self.name,
                    origin=item["origin"],
                    destination=item["destination"],
                    departure_date=departure,
                    return_date=return_date,
                    cabin=Cabin.ECONOMY,
                    price=price,
                    currency=currency,
                    booking_url=links.get("flightOffers") or links.get("flightDates"),
                    raw=item,
                    verified=False,
                    notes=("Amadeus inspiration prices are cached; live offers are used for verification.",),
                )
            )
        return quotes

    def parse_offers(self, payload: dict, fallback: Quote, currency: str) -> list[Quote]:
        quotes: list[Quote] = []
        for offer in payload.get("data") or []:
            price_data = offer.get("price") or {}
            price = safe_decimal(price_data.get("grandTotal") or price_data.get("total"))
            if not price:
                continue
            segment_cabins = _segment_cabins(offer)
            segments: list[Segment] = []
            for itinerary in offer.get("itineraries") or []:
                for segment in itinerary.get("segments") or []:
                    segment_id = str(segment.get("id") or "")
                    dep = segment.get("departure") or {}
                    arr = segment.get("arrival") or {}
                    cabin = segment_cabins.get(segment_id, fallback.cabin)
                    segments.append(
                        Segment(
                            origin=dep.get("iataCode") or fallback.origin,
                            destination=arr.get("iataCode") or fallback.destination,
                            departure_at=parse_datetime(dep.get("at")),
                            arrival_at=parse_datetime(arr.get("at")),
                            marketing_carrier=segment.get("carrierCode"),
                            flight_number=segment.get("number"),
                            cabin=cabin,
                            duration_minutes=_parse_iso_duration(segment.get("duration")),
                        )
                    )
            airline = None
            validating = offer.get("validatingAirlineCodes") or []
            if validating:
                airline = ",".join(validating)
            elif segments:
                airline = segments[0].marketing_carrier
            stops = max(len(segments) - 2, 0) if fallback.return_date else max(len(segments) - 1, 0)
            quotes.append(
                Quote(
                    source=self.name,
                    origin=fallback.origin,
                    destination=fallback.destination,
                    departure_date=fallback.departure_date,
                    return_date=fallback.return_date,
                    cabin=fallback.cabin,
                    price=price,
                    currency=str(price_data.get("currency") or currency).upper(),
                    airline=airline,
                    stops=stops,
                    segments=tuple(segments),
                    raw=offer,
                    verified=True,
                    notes=("Amadeus self-service does not include low-cost carriers, American, Delta, or British Airways.",),
                )
            )
        return quotes

    def _token(self, ctx: SourceContext) -> str | None:
        if self._access_token:
            return self._access_token
        payload = ctx.post_form(
            self.name,
            self.token_endpoint,
            {
                "grant_type": "client_credentials",
                "client_id": ctx.config.api.amadeus_client_id,
                "client_secret": ctx.config.api.amadeus_client_secret,
            },
        )
        if not payload:
            return None
        self._access_token = payload.get("access_token")
        return self._access_token

    def _within_search_window(self, ctx: SourceContext, quote: Quote) -> bool:
        today = date.today()
        min_date = today + timedelta(days=ctx.config.search.min_days_ahead)
        max_date = today + timedelta(days=ctx.config.search.max_days_ahead)
        if not (min_date <= quote.departure_date <= max_date):
            return False
        if quote.return_date is None:
            return False
        return quote.stay_nights in ctx.config.search.stay_lengths


def _segment_cabins(offer: dict) -> dict[str, Cabin]:
    cabins: dict[str, Cabin] = {}
    for traveler in offer.get("travelerPricings") or []:
        for detail in traveler.get("fareDetailsBySegment") or []:
            segment_id = str(detail.get("segmentId") or "")
            cabin = detail.get("cabin")
            if segment_id and cabin:
                try:
                    cabins[segment_id] = Cabin.parse(cabin)
                except ValueError:
                    cabins[segment_id] = Cabin.ECONOMY
    return cabins


def _parse_iso_duration(value: object) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?", str(value))
    if not match:
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    return (days * 24 * 60) + (hours * 60) + minutes
