from __future__ import annotations

from datetime import date
from decimal import Decimal

from flight_deals_bot.models import Cabin, Quote
from flight_deals_bot.sources.amadeus import AmadeusAdapter
from flight_deals_bot.sources.searchapi import SearchApiAdapter
from flight_deals_bot.sources.travelpayouts import TravelpayoutsAdapter


def test_travelpayouts_latest_parser_handles_business_quote() -> None:
    payload = {
        "success": True,
        "data": [
            {
                "origin": "TPE",
                "destination": "NRT",
                "depart_date": "2026-10-01",
                "return_date": "2026-10-08",
                "value": "18000",
                "trip_class": 1,
                "airline": "CI",
                "number_of_changes": 0,
            }
        ],
    }

    quotes = TravelpayoutsAdapter().parse_latest(payload, default_cabin=Cabin.BUSINESS, currency="TWD")

    assert len(quotes) == 1
    assert quotes[0].origin == "TPE"
    assert quotes[0].destination == "NRT"
    assert quotes[0].cabin == Cabin.BUSINESS
    assert quotes[0].price == Decimal("18000")


def test_amadeus_offer_parser_preserves_mixed_cabin_longest_segment() -> None:
    fallback = Quote(
        source="travelpayouts",
        origin="TPE",
        destination="LAX",
        departure_date=date(2026, 10, 1),
        return_date=date(2026, 10, 8),
        cabin=Cabin.BUSINESS,
        price=Decimal("60000"),
    )
    payload = {
        "data": [
            {
                "price": {"currency": "TWD", "grandTotal": "52000"},
                "validatingAirlineCodes": ["CI"],
                "itineraries": [
                    {
                        "segments": [
                            {
                                "id": "1",
                                "carrierCode": "CI",
                                "number": "8",
                                "duration": "PT12H",
                                "departure": {"iataCode": "TPE", "at": "2026-10-01T23:50:00"},
                                "arrival": {"iataCode": "LAX", "at": "2026-10-01T20:30:00"},
                            },
                            {
                                "id": "2",
                                "carrierCode": "AA",
                                "number": "100",
                                "duration": "PT1H",
                                "departure": {"iataCode": "LAX", "at": "2026-10-02T22:00:00"},
                                "arrival": {"iataCode": "SFO", "at": "2026-10-02T23:00:00"},
                            },
                        ]
                    }
                ],
                "travelerPricings": [
                    {
                        "fareDetailsBySegment": [
                            {"segmentId": "1", "cabin": "BUSINESS"},
                            {"segmentId": "2", "cabin": "ECONOMY"},
                        ]
                    }
                ],
            }
        ]
    }

    quotes = AmadeusAdapter().parse_offers(payload, fallback=fallback, currency="TWD")

    assert len(quotes) == 1
    assert quotes[0].verified is True
    assert quotes[0].mixed_cabin is True
    assert quotes[0].longest_segment_cabin == Cabin.BUSINESS


def test_searchapi_parser_uses_price_insight_hint() -> None:
    fallback = Quote(
        source="travelpayouts",
        origin="TPE",
        destination="SIN",
        departure_date=date(2026, 9, 1),
        return_date=date(2026, 9, 8),
        cabin=Cabin.ECONOMY,
        price=Decimal("6000"),
    )
    payload = {
        "search_metadata": {"google_url": "https://www.google.com/travel/flights/search"},
        "price_insights": {"typical_price_range": [10000, 12000]},
        "best_flights": [
            {
                "price": "7,000",
                "flights": [
                    {
                        "departure_airport": {"id": "TPE"},
                        "arrival_airport": {"id": "SIN"},
                        "airline": "BR",
                        "flight_number": "BR215",
                        "duration": 280,
                        "travel_class": "Economy",
                    }
                ],
            }
        ],
    }

    quotes = SearchApiAdapter().parse_flights(payload, fallback=fallback, currency="TWD")

    assert len(quotes) == 1
    assert quotes[0].price == Decimal("7000")
    assert quotes[0].baseline_price_hint == Decimal("11000")
    assert quotes[0].booking_url == "https://www.google.com/travel/flights/search"


def test_searchapi_explore_parser_creates_discovery_candidates() -> None:
    payload = {
        "search_metadata": {"google_url": "https://www.google.com/travel/explore"},
        "search_parameters": {"currency": "TWD"},
        "destinations": [
            {
                "name": "Tokyo",
                "primary_airport": "NRT",
                "outbound_date": "2026-10-01",
                "return_date": "2026-10-08",
                "flight": {
                    "airport_code": "NRT",
                    "price": 32000,
                    "stops": 1,
                    "airline_code": "CI",
                    "airline_name": "China Airlines",
                },
            }
        ],
    }

    quotes = SearchApiAdapter().parse_explore(payload, origin="TPE", cabin=Cabin.BUSINESS, currency="TWD")

    assert len(quotes) == 1
    assert quotes[0].source == "searchapi"
    assert quotes[0].origin == "TPE"
    assert quotes[0].destination == "NRT"
    assert quotes[0].cabin == Cabin.BUSINESS
    assert quotes[0].verified is False


def test_searchapi_parser_does_not_use_searchapi_json_urls_as_booking_links() -> None:
    fallback = Quote(
        source="travelpayouts",
        origin="TPE",
        destination="SIN",
        departure_date=date(2026, 9, 1),
        return_date=date(2026, 9, 8),
        cabin=Cabin.ECONOMY,
        price=Decimal("6000"),
    )
    payload = {
        "search_metadata": {
            "html_url": "https://www.searchapi.io/api/v1/search?engine=google_flights",
            "request_url": "https://www.searchapi.io/api/v1/search?engine=google_flights",
        },
        "best_flights": [{"price": "7,000", "booking_url": "https://www.google.com/travel/clk/f"}],
    }

    quotes = SearchApiAdapter().parse_flights(payload, fallback=fallback, currency="TWD")

    assert len(quotes) == 1
    assert quotes[0].booking_url is None


def test_searchapi_calendar_parser_filters_stays_and_sets_baseline_hint() -> None:
    payload = {
        "search_metadata": {"google_url": "https://example.test/calendar"},
        "search_parameters": {"currency": "TWD"},
        "calendar": [
            {"departure": "2026-10-01", "return": "2026-10-08", "price": 7000, "is_lowest_price": True},
            {"departure": "2026-10-01", "return": "2026-10-09", "price": 9000},
            {"departure": "2026-10-02", "return": "2026-10-09", "price": 11000},
            {"departure": "2026-10-03", "return": "2026-10-10", "has_no_flights": True},
        ],
    }

    quotes = SearchApiAdapter().parse_calendar(
        payload,
        origin="TPE",
        destination="NRT",
        cabin=Cabin.ECONOMY,
        currency="TWD",
        stay_lengths=(7,),
    )

    assert len(quotes) == 2
    assert quotes[0].price == Decimal("7000")
    assert quotes[0].return_date == date(2026, 10, 8)
    assert quotes[0].baseline_price_hint == Decimal("9000")
    assert quotes[0].booking_url == "https://example.test/calendar"
    assert quotes[0].verified is False
