from __future__ import annotations

from datetime import date
from decimal import Decimal

from flight_deals_bot.models import Cabin, Quote
from flight_deals_bot.notifier import TelegramNotifier, format_deal_message, format_no_deals_message, quote_link


class FakeHttp:
    def __init__(self) -> None:
        self.calls = []

    def post_json(self, url, payload, headers=None):
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


def test_no_deals_message_includes_counts() -> None:
    message = format_no_deals_message(
        discovered_count=58,
        verified_count=18,
        scored_count=0,
        enabled_sources=["searchapi"],
        candidates=[
            Quote(
                source="searchapi",
                origin="TPE",
                destination="NRT",
                departure_date=date(2026, 10, 1),
                return_date=date(2026, 10, 8),
                cabin=Cabin.BUSINESS,
                price=Decimal("32000"),
                airline="CI",
                stops=1,
            )
        ],
        source_errors={},
    )

    assert "目前沒有找到符合低價門檻的機票" in message
    assert "候選票：58" in message
    assert "資料源：searchapi" in message
    assert "候選票前幾筆" in message
    assert "Taiwan Taoyuan International Airport" in message
    assert "Narita International Airport" in message
    assert "Taiwan" in message
    assert "Japan" in message
    assert "TWD 32,000" in message
    assert "約原價" in message
    assert '<a href="' in message


def test_deal_message_includes_route_country_and_fallback_link() -> None:
    from flight_deals_bot.models import Baseline, DealScore

    quote = Quote(
        source="fixture",
        origin="TPE",
        destination="NRT",
        departure_date=date(2026, 10, 1),
        return_date=date(2026, 10, 8),
        cabin=Cabin.ECONOMY,
        price=Decimal("7000"),
    )
    score = DealScore(
        quote=quote,
        baseline=Baseline(price=Decimal("12000"), sample_size=4, source="fixture"),
        discount=Decimal("0.416"),
        score=Decimal("41.6"),
        reason="fixture",
    )

    message = format_deal_message(score)

    assert "Taiwan Taoyuan International Airport" in message
    assert "Japan" in message
    assert "目前約為原價 58%" in message
    assert "查看票價 / 搜尋此航線" in message
    assert "https://www.google.com/travel/flights/search?" in message


def test_quote_link_rejects_searchapi_json_urls() -> None:
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

    assert link.startswith("https://www.google.com/travel/flights/search?")
    assert "searchapi.io" not in link
