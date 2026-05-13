from __future__ import annotations

import html
import urllib.parse
from dataclasses import dataclass
from decimal import Decimal

from .airlines import airline_label
from .airports import airport_continent_label, airport_country_label, airport_label
from .http import JsonHttpClient
from .models import Cabin, DealScore, Quote, Segment
from .scoring import as_percent, as_price_ratio, display_reference_price


CABIN_LABELS = {
    Cabin.ECONOMY: "經濟艙",
    Cabin.PREMIUM_ECONOMY: "豪華經濟艙",
    Cabin.BUSINESS: "商務艙",
    Cabin.FIRST: "頭等艙",
}

TRIP_CLASS = {
    Cabin.ECONOMY: 0,
    Cabin.PREMIUM_ECONOMY: 0,
    Cabin.BUSINESS: 1,
    Cabin.FIRST: 2,
}


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
                "disable_web_page_preview": True,
            },
        )


def format_deal_message(score: DealScore) -> str:
    quote = score.quote
    route = format_route(quote)
    cabin = cabin_label(quote.cabin)
    continent = airport_continent_label(quote.destination)
    destination_country = airport_country_label(quote.destination) or "未知"
    lines = [
        f"<b>低價機票提醒</b>",
        "",
        f"航線：{html.escape(route)}",
        f"目的地國家：{html.escape(destination_country)}",
        f"洲別分類：{html.escape(continent)}",
        "",
        f"艙等：{html.escape(cabin)}",
        f"價格：{html.escape(format_money(quote.price, quote.currency))}",
        f"原價/基準：{html.escape(format_money(score.baseline.price, quote.currency))}，"
        f"目前約為原價 {html.escape(as_price_ratio(quote.price, score.baseline.price))}，"
        f"低 {html.escape(as_percent(score.discount))}",
        "",
        f"日期：{quote.departure_date.isoformat()} -> {quote.return_date.isoformat() if quote.return_date else 'one-way'}"
        + (f"（{quote.stay_nights} 晚）" if quote.stay_nights is not None else ""),
        f"來源：{html.escape(quote.source)}（{html.escape(source_status_label(quote))}）",
    ]
    airline = format_airline_summary(quote)
    lines.append(f"航空公司：{html.escape(airline or '資料源未提供')}")
    flight_details = format_flight_details(quote)
    if flight_details:
        lines.extend(f"航班/機型：{html.escape(line)}" for line in flight_details)
    elif not quote.verified:
        lines.append("航班/機型：快取候選通常不提供，請點連結重新搜尋確認")
    if quote.stops is not None:
        lines.append(f"轉機：{quote.stops} 次")
    if quote.self_transfer:
        lines.append("注意：可能包含自轉機，請確認行李與保障條款")
    if quote.mixed_cabin:
        lines.append(f"混艙：是，最長航段為 {cabin_label(quote.longest_segment_cabin)}")
    if quote.notes:
        lines.append("備註：" + html.escape(format_notes(quote.notes)))
    lines.append("")
    lines.append(f"<a href=\"{html.escape(quote_link(quote))}\">{html.escape(link_label(quote))}</a>")
    return "\n".join(lines)


def format_money(value: Decimal, currency: str) -> str:
    amount = value.quantize(Decimal("1"))
    return f"{currency} {amount:,}"


def format_route(quote: Quote) -> str:
    return f"{airport_label(quote.origin)} -> {airport_label(quote.destination)}"


def format_no_deals_message(
    discovered_count: int,
    verified_count: int,
    scored_count: int,
    enabled_sources: list[str],
    alertable_count: int | None = None,
    suppressed_count: int = 0,
    cooldown_hours: int = 24,
    candidates: list[Quote] | None = None,
    cooldown_scores: list[DealScore] | None = None,
    source_stats: dict[str, dict[str, int]] | None = None,
    source_notes: list[str] | None = None,
    source_errors: dict[str, str] | None = None,
) -> str:
    status = "目前沒有找到符合低價門檻的機票。"
    if scored_count > 0 and alertable_count == 0 and suppressed_count > 0:
        status = f"有找到符合低價門檻的機票，但都在 {cooldown_hours} 小時冷卻期內，這次不重複推播正式提醒。"
    elif scored_count > 0 and alertable_count == 0:
        status = "有找到符合低價門檻的機票，但這次沒有新的可推播正式提醒。"

    lines = [
        "<b>機票價格查詢完成</b>",
        "",
        status,
        "",
        "<b>本次統計</b>",
        f"候選票：{discovered_count}",
        f"驗價票：{verified_count}",
        f"符合門檻：{scored_count}",
    ]
    if alertable_count is not None:
        lines.append(f"本次可推播：{alertable_count}")
    if suppressed_count:
        lines.append(f"冷卻略過：{suppressed_count}")
    lines.append("資料源：" + html.escape(", ".join(enabled_sources) if enabled_sources else "none"))
    if source_stats:
        stat_lines = format_source_stats(source_stats)
        if stat_lines:
            lines.append("")
            lines.append("<b>來源統計</b>")
            lines.extend(stat_lines)
    if cooldown_scores:
        lines.append("")
        lines.append("<b>冷卻中的低價票</b>")
        lines.append("這些票已符合低價門檻，但冷卻期內不重複發正式提醒。")
        for index, score in enumerate(cooldown_scores, start=1):
            if index > 1:
                lines.append("")
            lines.extend(format_score_preview_lines(index, score))
    if candidates:
        lines.append("")
        lines.append("<b>候選票前幾筆</b>")
        index = 1
        for continent, quotes in group_candidates_by_continent(candidates):
            lines.append(f"<b>{html.escape(continent)}</b>")
            for quote in quotes:
                if index > 1:
                    lines.append("")
                lines.extend(format_candidate_preview_lines(index, quote))
                index += 1
    if source_notes:
        compact_notes = "; ".join(source_notes)
        lines.append("診斷：" + html.escape(compact_notes[:700]))
    if source_errors:
        compact = "; ".join(f"{key}: {value}" for key, value in source_errors.items())
        lines.append("錯誤：" + html.escape(compact[:500]))
    return "\n".join(lines)


def format_source_stats(source_stats: dict[str, dict[str, int]]) -> list[str]:
    lines: list[str] = []
    labels = {
        "discovered": "候選",
        "verified": "驗價",
        "scored": "符合",
        "suppressed": "冷卻",
        "selected": "本次推播",
    }
    for source, stats in sorted(source_stats.items()):
        parts = [f"{label} {stats[key]}" for key, label in labels.items() if stats.get(key)]
        if not parts:
            parts = ["無結果"]
        lines.append(f"{html.escape(source)}：{html.escape(' / '.join(parts))}")
    return lines


def format_candidate_line(index: int, quote: Quote) -> str:
    return " / ".join(format_candidate_preview_lines(index, quote))


def format_candidate_preview_lines(index: int, quote: Quote) -> list[str]:
    cabin = cabin_label(quote.cabin)
    return_date = quote.return_date.isoformat() if quote.return_date else "one-way"
    stay = f"（{quote.stay_nights} 晚）" if quote.stay_nights is not None else ""
    status = "已驗價" if quote.verified else "候選"
    stops = f"轉機 {quote.stops} 次" if quote.stops is not None else "轉機資訊未提供"
    airline = format_airline_summary(quote)
    airline_text = airline or "航空公司未提供"
    reference = display_reference_price(quote)
    price_ratio = f"（約原價 {as_price_ratio(quote.price, reference)}）" if reference else ""
    continent = airport_continent_label(quote.destination)
    country = airport_country_label(quote.destination) or "未知"
    route = format_route(quote)
    link = quote_link(quote)
    return [
        f'{index}. <a href="{html.escape(link)}">{html.escape(route)}</a>',
        f"日期：{quote.departure_date.isoformat()} -> {return_date}{stay}",
        f"分類：{html.escape(continent)}・{html.escape(country)} / 艙等：{html.escape(cabin)}",
        f"價格：{html.escape(format_money(quote.price, quote.currency))} {html.escape(price_ratio)}",
        f"來源：{html.escape(quote.source)}（{status}） / {html.escape(airline_text)} / {html.escape(stops)}",
    ]


def format_score_preview_lines(index: int, score: DealScore) -> list[str]:
    quote = score.quote
    cabin = cabin_label(quote.cabin)
    return_date = quote.return_date.isoformat() if quote.return_date else "one-way"
    stay = f"（{quote.stay_nights} 晚）" if quote.stay_nights is not None else ""
    continent = airport_continent_label(quote.destination)
    country = airport_country_label(quote.destination) or "未知"
    route = format_route(quote)
    airline = format_airline_summary(quote) or "航空公司未提供"
    stops = f"轉機 {quote.stops} 次" if quote.stops is not None else "轉機資訊未提供"
    status = "已驗價" if quote.verified else "候選"
    return [
        f'{index}. <a href="{html.escape(quote_link(quote))}">{html.escape(route)}</a>',
        f"日期：{quote.departure_date.isoformat()} -> {return_date}{stay}",
        f"分類：{html.escape(continent)}・{html.escape(country)} / 艙等：{html.escape(cabin)}",
        f"價格：{html.escape(format_money(quote.price, quote.currency))}（約原價 {html.escape(as_price_ratio(quote.price, score.baseline.price))}，低 {html.escape(as_percent(score.discount))}）",
        f"來源：{html.escape(quote.source)}（{status}） / {html.escape(airline)} / {html.escape(stops)}",
    ]


def group_candidates_by_continent(candidates: list[Quote]) -> list[tuple[str, list[Quote]]]:
    order = ["亞洲", "歐洲", "非洲", "美洲", "大洋洲", "其他"]
    grouped: dict[str, list[Quote]] = {}
    for quote in candidates:
        grouped.setdefault(airport_continent_label(quote.destination), []).append(quote)
    return [(continent, grouped[continent]) for continent in order if continent in grouped]


def quote_link(quote: Quote) -> str:
    if quote.booking_url and _is_public_booking_url(quote.booking_url):
        return quote.booking_url
    return aviasales_search_link(quote)


def aviasales_search_link(quote: Quote) -> str:
    search_code = aviasales_search_code(quote)
    params = {
        "trip_class": TRIP_CLASS.get(quote.cabin, 0),
        "currency": quote.currency,
        "locale": "zh",
    }
    return f"https://www.aviasales.com/search/{search_code}?" + urllib.parse.urlencode(params)


def google_flights_search_link(quote: Quote) -> str:
    params = {
        "q": f"{quote.origin} to {quote.destination} {quote.departure_date.isoformat()} {quote.return_date.isoformat() if quote.return_date else ''} {quote.cabin.value.replace('_', ' ')}",
        "hl": "zh-TW",
        "curr": quote.currency,
    }
    return "https://www.google.com/travel/flights/search?" + urllib.parse.urlencode(params)


def _is_public_booking_url(url: str) -> bool:
    blocked_fragments = ("searchapi.io", "/api/v1/search", "google.com/travel/clk/f", "search.aviasales.com/flights")
    return url.startswith(("https://", "http://")) and not any(fragment in url for fragment in blocked_fragments)


def cabin_label(cabin: Cabin) -> str:
    return CABIN_LABELS.get(cabin, cabin.value.replace("_", " ").title())


def format_airline_summary(quote: Quote) -> str | None:
    if quote.airline:
        return airline_label(quote.airline)
    carriers = []
    for segment in quote.segments:
        if segment.marketing_carrier:
            label = airline_label(segment.marketing_carrier)
            if label and label not in carriers:
                carriers.append(label)
    return "、".join(carriers) if carriers else None


def format_flight_details(quote: Quote) -> list[str]:
    details = [_segment_detail(segment) for segment in quote.segments]
    details = [detail for detail in details if detail]
    if details:
        return details[:4]
    flight_number = quote.raw.get("flight_number")
    if flight_number:
        carrier = airline_label(quote.airline) or quote.airline or ""
        return [f"{carrier} {flight_number}".strip()]
    return []


def _segment_detail(segment: Segment) -> str | None:
    parts = [f"{segment.origin}->{segment.destination}"]
    carrier = airline_label(segment.marketing_carrier)
    flight_number = _flight_number(segment)
    if carrier:
        parts.append(carrier)
    if flight_number:
        parts.append(flight_number)
    if segment.aircraft:
        parts.append(f"機型 {segment.aircraft}")
    if len(parts) == 1:
        return None
    return " ".join(str(part) for part in parts if part)


def _flight_number(segment: Segment) -> str | None:
    if not segment.flight_number:
        return None
    value = str(segment.flight_number).strip()
    if not value:
        return None
    carrier = str(segment.marketing_carrier or "").strip().upper()
    if carrier and not value.upper().startswith(carrier):
        return f"{carrier}{value}"
    return value


def format_notes(notes: tuple[str, ...]) -> str:
    return "；".join(_translate_note(note) for note in notes)


def _translate_note(note: str) -> str:
    translations = {
        "Travelpayouts latest prices are cached and should be rechecked before booking.": "Travelpayouts 快取票價，實際票價與座位請點連結重新確認。",
        "Travelpayouts prices_for_dates 補到較完整的快取票價與 Aviasales 搜尋結果，實際票價仍請點進去確認。": "Travelpayouts 已補到較完整的快取票價與 Aviasales 搜尋結果，實際票價仍請點進去確認。",
        "SearchApi Google Flights Calendar candidate; verify before booking.": "SearchApi Google Flights Calendar 候選票，訂票前請重新確認票價與座位。",
        "Calendar marked this as a lowest-price date.": "Google Flights Calendar 標記這組日期為低價。",
        "SearchApi explore is a broad Google Travel candidate; verify before booking.": "SearchApi Explore 廣域候選票，訂票前請重新確認票價與座位。",
        "Amadeus inspiration prices are cached; live offers are used for verification.": "Amadeus 靈感票價為快取候選，會再用即時 offers 驗價。",
        "Kiwi may include virtual interlining/self-transfer itineraries; confirm protection and baggage rules.": "Kiwi 可能包含自轉機或 virtual interlining，請確認行李、轉機保障與退改規則。",
    }
    return translations.get(note, note)


def link_label(quote: Quote) -> str:
    if quote.booking_url and _is_public_booking_url(quote.booking_url):
        if "aviasales" in quote.booking_url:
            return "查看 Aviasales 該航線日期結果"
        return "前往訂票或驗價頁"
    return "查看 Aviasales 該航線日期結果"


def source_status_label(quote: Quote) -> str:
    if quote.source == "travelpayouts" and quote.verified:
        return "已補航班/連結，仍需確認"
    return "已驗價" if quote.verified else "快取/候選"


def aviasales_search_code(quote: Quote) -> str:
    code = f"{quote.origin}{quote.departure_date:%d%m}{quote.destination}"
    if quote.return_date:
        code += f"{quote.return_date:%d%m}"
    return f"{code}1"
