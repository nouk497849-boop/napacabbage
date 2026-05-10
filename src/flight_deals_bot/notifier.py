from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal

from .http import JsonHttpClient
from .models import DealScore, Quote
from .scoring import as_percent


@dataclass
class TelegramNotifier:
    bot_token: str | None
    chat_id: str | None
    http: JsonHttpClient

    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, score: DealScore, dry_run: bool = False) -> str:
        message = format_deal_message(score)
        if dry_run:
            return message
        self.send_text(message)
        return message

    def send_text(self, message: str) -> None:
        if not self.enabled():
            raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required to send alerts")
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        self.http.post_json(
            url,
            {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )


def format_deal_message(score: DealScore) -> str:
    quote = score.quote
    route = f"{quote.origin} -> {quote.destination}"
    cabin = quote.cabin.value.replace("_", " ").title()
    lines = [
        f"<b>低價機票提醒</b> {html.escape(route)}",
        f"艙等：{html.escape(cabin)}",
        f"價格：{html.escape(format_money(quote.price, quote.currency))}",
        f"基準：{html.escape(format_money(score.baseline.price, quote.currency))}，低 {html.escape(as_percent(score.discount))}",
        f"日期：{quote.departure_date.isoformat()} -> {quote.return_date.isoformat() if quote.return_date else 'one-way'}"
        + (f"（{quote.stay_nights} 晚）" if quote.stay_nights is not None else ""),
        f"來源：{html.escape(quote.source)}" + ("（已驗價）" if quote.verified else "（快取/候選）"),
    ]
    if quote.airline:
        lines.append(f"航司：{html.escape(quote.airline)}")
    if quote.stops is not None:
        lines.append(f"轉機：{quote.stops} 次")
    if quote.self_transfer:
        lines.append("注意：可能包含自轉機，請確認行李與保障條款")
    if quote.mixed_cabin:
        lines.append(f"混艙：是，最長航段為 {quote.longest_segment_cabin.value.replace('_', ' ').title()}")
    if quote.notes:
        lines.append("備註：" + html.escape("; ".join(quote.notes)))
    if quote.booking_url:
        lines.append(f"<a href=\"{html.escape(quote.booking_url)}\">查看票價</a>")
    return "\n".join(lines)


def format_money(value: Decimal, currency: str) -> str:
    amount = value.quantize(Decimal("1"))
    return f"{currency} {amount:,}"


def format_no_deals_message(
    discovered_count: int,
    verified_count: int,
    scored_count: int,
    enabled_sources: list[str],
    source_errors: dict[str, str] | None = None,
) -> str:
    lines = [
        "<b>機票價格查詢完成</b>",
        "目前沒有找到符合低價門檻的機票。",
        f"候選票：{discovered_count}",
        f"驗價票：{verified_count}",
        f"符合門檻：{scored_count}",
        "資料源：" + html.escape(", ".join(enabled_sources) if enabled_sources else "none"),
    ]
    if source_errors:
        compact = "; ".join(f"{key}: {value}" for key, value in source_errors.items())
        lines.append("錯誤：" + html.escape(compact[:500]))
    return "\n".join(lines)
