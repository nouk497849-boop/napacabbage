from __future__ import annotations

import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, TextIO

from .airports import airport_country_code
from .config import AppConfig
from .http import HttpError, JsonHttpClient
from .models import Cabin, DealScore, Quote
from .notifier import TelegramNotifier, format_no_deals_message
from .scoring import find_baseline, score_quote, should_suppress_alert
from .sources import AmadeusAdapter, KiwiAdapter, SearchApiAdapter, SkyscannerAdapter, TravelpayoutsAdapter
from .sources.base import SourceAdapter, SourceContext
from .storage import Store, build_store


@dataclass
class RunResult:
    discovered_count: int
    verified_count: int
    scored_count: int
    alerted_count: int
    messages: list[str]
    source_errors: dict[str, str]


def build_adapters() -> list[SourceAdapter]:
    return [
        TravelpayoutsAdapter(),
        AmadeusAdapter(),
        SearchApiAdapter(),
        KiwiAdapter(),
        SkyscannerAdapter(),
    ]


def run_pipeline(
    config: AppConfig,
    dry_run: bool = False,
    store: Store | None = None,
    http: JsonHttpClient | None = None,
    adapters: list[SourceAdapter] | None = None,
    output: TextIO | None = None,
) -> RunResult:
    output = output or sys.stdout
    http = http or JsonHttpClient()
    store = store or build_store(config.database_url)
    store.setup()
    ctx = SourceContext(config=config, http=http, store=store)
    adapters = adapters or build_adapters()

    enabled_adapters = [adapter for adapter in adapters if adapter.enabled(config)]
    print(f"Enabled sources: {', '.join(adapter.name for adapter in enabled_adapters) or 'none'}", file=output)
    if not config.database_url:
        print("DATABASE_URL is not set; using in-memory storage for this run only.", file=output)

    discovered: list[Quote] = []
    verified: list[Quote] = []
    source_errors: dict[str, str] = {}
    printed_diagnostics: dict[str, int] = {}

    for adapter in enabled_adapters:
        try:
            quotes = adapter.discover(ctx)
            discovered.extend(quotes)
            print(f"{adapter.name}: discovered {len(quotes)} candidates", file=output)
            _print_new_diagnostics(ctx, adapter.name, printed_diagnostics, output)
        except Exception as exc:  # noqa: BLE001 - source failures should not stop the whole scan
            source_errors[adapter.name] = _error_text(exc)
            print(f"{adapter.name}: discovery failed: {source_errors[adapter.name]}", file=output)
            _print_new_diagnostics(ctx, adapter.name, printed_diagnostics, output)

    store.save_quotes(discovered)
    candidates = select_verification_candidates(discovered, config.search.top_verify_limit)
    print(f"Selected {len(candidates)} candidates for verification", file=output)

    for adapter in enabled_adapters:
        try:
            quotes = adapter.verify(ctx, candidates)
            verified.extend(quotes)
            if quotes:
                print(f"{adapter.name}: verified {len(quotes)} quotes", file=output)
            _print_new_diagnostics(ctx, adapter.name, printed_diagnostics, output)
        except Exception as exc:  # noqa: BLE001
            source_errors[f"{adapter.name}:verify"] = _error_text(exc)
            print(f"{adapter.name}: verification failed: {source_errors[f'{adapter.name}:verify']}", file=output)
            _print_new_diagnostics(ctx, adapter.name, printed_diagnostics, output)

    store.save_quotes(verified)
    scores = score_quotes(store, discovered + verified, require_verified=config.search.require_verified_alerts)
    scores = dedupe_scores(scores)
    scores = [
        score
        for score in scores
        if not should_suppress_alert(
            store,
            score,
            cooldown_hours=int(config.search.alert_cooldown.total_seconds() // 3600),
            price_drop_pct=config.search.alert_price_drop_pct,
        )
    ]
    scores = sorted(scores, key=lambda item: item.score, reverse=True)[: config.search.max_alerts_per_run]

    notifier = TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id, http=http)
    messages: list[str] = []
    for score in scores:
        message = notifier.send(score, dry_run=dry_run)
        messages.append(message)
        if not dry_run:
            store.record_alert(score.quote, score.score, score.baseline, message)

    if not scores and config.search.notify_no_deals:
        no_deals_message = format_no_deals_message(
            discovered_count=len(discovered),
            verified_count=len(verified),
            scored_count=0,
            enabled_sources=[adapter.name for adapter in enabled_adapters],
            candidates=select_summary_candidates(discovered + verified, config.search.no_deal_candidate_limit),
            source_notes=_source_notes(ctx),
            source_errors=source_errors,
        )
        messages.append(no_deals_message)
        if dry_run:
            print("\n--- DRY RUN NO-DEAL NOTICE ---", file=output)
            print(no_deals_message, file=output)
        else:
            notifier.send_text(no_deals_message)

    if dry_run and scores:
        for message in messages:
            print("\n--- DRY RUN ALERT ---", file=output)
            print(message, file=output)
    print(f"Alerted {len(messages)} deals", file=output)

    return RunResult(
        discovered_count=len(discovered),
        verified_count=len(verified),
        scored_count=len(scores),
        alerted_count=len(messages),
        messages=messages,
        source_errors=source_errors,
    )


def select_verification_candidates(quotes: Iterable[Quote], limit: int) -> list[Quote]:
    cabin_rank = {
        Cabin.FIRST: 0,
        Cabin.BUSINESS: 1,
        Cabin.PREMIUM_ECONOMY: 2,
        Cabin.ECONOMY: 3,
    }
    unique: dict[tuple[str, str, str, str, Cabin], Quote] = {}
    for quote in quotes:
        if quote.return_date is None:
            continue
        key = (
            quote.origin,
            quote.destination,
            quote.departure_date.isoformat(),
            quote.return_date.isoformat(),
            quote.cabin,
        )
        existing = unique.get(key)
        if existing is None or quote.price < existing.price:
            unique[key] = quote
    return sorted(unique.values(), key=lambda q: (cabin_rank[q.cabin], q.price))[:limit]


def select_summary_candidates(quotes: Iterable[Quote], limit: int) -> list[Quote]:
    if limit <= 0:
        return []
    cabin_rank = {
        Cabin.FIRST: 0,
        Cabin.BUSINESS: 1,
        Cabin.PREMIUM_ECONOMY: 2,
        Cabin.ECONOMY: 3,
    }
    unique: dict[tuple[str, Cabin], Quote] = {}
    for quote in quotes:
        key = (quote.destination, quote.cabin)
        existing = unique.get(key)
        if existing is None:
            unique[key] = quote
            continue
        if quote.price < existing.price or (quote.price == existing.price and quote.verified and not existing.verified):
            unique[key] = quote
    buckets: dict[str, list[Quote]] = {}
    for quote in unique.values():
        bucket = airport_country_code(quote.destination) or quote.destination
        buckets.setdefault(bucket, []).append(quote)
    for bucket_quotes in buckets.values():
        bucket_quotes.sort(key=lambda q: (cabin_rank[q.cabin], q.price))

    ordered_buckets = sorted(buckets.values(), key=lambda items: (cabin_rank[items[0].cabin], items[0].price))
    selected: list[Quote] = []
    while ordered_buckets and len(selected) < limit:
        next_round: list[list[Quote]] = []
        for bucket_quotes in ordered_buckets:
            if len(selected) >= limit:
                break
            selected.append(bucket_quotes.pop(0))
            if bucket_quotes:
                next_round.append(bucket_quotes)
        ordered_buckets = sorted(next_round, key=lambda items: (cabin_rank[items[0].cabin], items[0].price))
    return selected


def score_quotes(store: Store, quotes: Iterable[Quote], require_verified: bool) -> list[DealScore]:
    scores: list[DealScore] = []
    for quote in quotes:
        if require_verified and not quote.verified:
            continue
        baseline = find_baseline(store, quote)
        if not baseline:
            continue
        scored = score_quote(quote, baseline)
        if scored:
            scores.append(scored)
    return scores


def dedupe_scores(scores: Iterable[DealScore]) -> list[DealScore]:
    best: dict[tuple[str, str, str, str, Cabin], DealScore] = {}
    for score in scores:
        quote = score.quote
        key = (
            quote.origin,
            quote.destination,
            quote.departure_date.isoformat(),
            quote.return_date.isoformat() if quote.return_date else "",
            quote.cabin,
        )
        existing = best.get(key)
        if existing is None:
            best[key] = score
            continue
        if (score.quote.verified and not existing.quote.verified) or score.score > existing.score:
            best[key] = score
    return list(best.values())


def _print_new_diagnostics(ctx: SourceContext, source: str, printed: dict[str, int], output: TextIO) -> None:
    notes = ctx.diagnostics.get(source, [])
    start = printed.get(source, 0)
    for note in notes[start:]:
        print(f"{source}: note: {note}", file=output)
    printed[source] = len(notes)


def _source_notes(ctx: SourceContext) -> list[str]:
    notes: list[str] = []
    for source, entries in ctx.diagnostics.items():
        for entry in entries:
            notes.append(f"{source}: {entry}")
    return notes


def _error_text(exc: Exception) -> str:
    if isinstance(exc, HttpError):
        if exc.status == 429:
            return "rate limited by provider"
        return f"HTTP {exc.status}: {exc.body[:160]}"
    return str(exc)[:200]
