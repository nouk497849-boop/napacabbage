from __future__ import annotations

from datetime import date
from decimal import Decimal

from flight_deals_bot.models import Cabin, Quote
from flight_deals_bot.notifier import TelegramNotifier, format_no_deals_message


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
    assert "TPE-NRT" in message
    assert "TWD 32,000" in message
