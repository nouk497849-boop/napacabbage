from __future__ import annotations

from flight_deals_bot.notifier import TelegramNotifier


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
