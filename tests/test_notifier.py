from __future__ import annotations

from datetime import date
from decimal import Decimal

from flight_deals_bot.models import Baseline, Cabin, DealScore, Quote, Segment
from flight_deals_bot.notifier import TelegramNotifier, format_deal_message, format_no_deals_message, quote_link


class FakeHttp:
    def __init__(self) -> None:
        self.calls = []

    def post_json(self, url, payload, headers=None):  # noqa: ANN001
        self.calls.append((url, payload, headers))
        return {"ok": True}


def test_telegram_send_text_posts_message() -> None:
    http = FakeHttp()
    notifier = TelegramNotifier("token", "chat", http)

    notifier.send_text("hello")

    assert http.calls
    url, payload, _headers = http.calls[0]
    assert url == "https://api.telegram.org/bottoken/sendMessage"
    assert payload["chat_id"] == "chat"
    assert payload["text"] == "hello"


def test_no_deals_message_includes_city_country_continent_and_link() -> None:
    message = format_no_deals_message(
        discovered_count=58,
        verified_count=18,
        scored_count=0,
        enabled_sources=["travelpayouts", "searchapi"],
        candidates=[
            Quote(
                source="travelpayouts",
                origin="TPE",
                destination="SEL",
                departure_date=date(2026, 10, 1),
                return_date=date(2026, 10, 8),
                cabin=Cabin.ECONOMY,
                price=Decimal("5764"),
                airline="BR",
                stops=0,
            )
        ],
        source_errors={},
    )

    assert "目前沒有找到符合低價門檻的機票" in message
    assert "候選票：58" in message
    assert "資料源：travelpayouts, searchapi" in message
    assert "<b>亞洲</b>" in message
    assert "SEL 首爾, 南韓" in message
    assert "亞洲・南韓" in message
    assert "長榮航空 (BR)" in message
    assert "https://www.aviasales.com/search/TPE0110SEL08101" in message


def test_deal_message_includes_localized_route_airline_flight_and_aircraft() -> None:
    quote = Quote(
        source="travelpayouts",
        origin="TPE",
        destination="SEL",
        departure_date=date(2026, 8, 20),
        return_date=date(2026, 8, 27),
        cabin=Cabin.ECONOMY,
        price=Decimal("5764"),
        airline="BR",
        stops=0,
        booking_url="https://search.aviasales.com/flights/?origin_iata=TPE&destination_iata=SEL",
        segments=(
            Segment(
                origin="TPE",
                destination="ICN",
                marketing_carrier="BR",
                flight_number="160",
                cabin=Cabin.ECONOMY,
                aircraft="Airbus A321",
            ),
        ),
        notes=("Travelpayouts latest prices are cached and should be rechecked before booking.",),
    )
    score = DealScore(
        quote=quote,
        baseline=Baseline(price=Decimal("30000"), sample_size=1, source="absolute"),
        discount=Decimal("0.81"),
        score=Decimal("81"),
        reason="absolute",
    )

    message = format_deal_message(score)

    assert "航線：TPE Taiwan Taoyuan International Airport, 台灣 -&gt; SEL 首爾, 南韓" in message
    assert "目的地國家：南韓" in message
    assert "洲別分類：亞洲" in message
    assert "艙等：經濟艙" in message
    assert "航空公司：長榮航空 (BR)" in message
    assert "航班/機型：TPE-&gt;ICN 長榮航空 (BR) BR160 機型 Airbus A321" in message
    assert "Travelpayouts 快取票價" in message
    assert "查看 Aviasales 該航線日期結果" in message


def test_quote_link_rejects_searchapi_json_urls_and_uses_aviasales_fallback() -> None:
    quote = Quote(
        source="searchapi",
        origin="TPE",
        destination="HND",
        departure_date=date(2026, 7, 15),
        return_date=date(2026, 7, 18),
        cabin=Cabin.FIRST,
        price=Decimal("64046"),
        booking_url="https://www.searchapi.io/api/v1/search?engine=google_flights",
    )

    link = quote_link(quote)

    assert link.startswith("https://www.aviasales.com/search/TPE1507HND18071?")
    assert "searchapi.io" not in link
