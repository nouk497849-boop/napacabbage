from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from flight_deals_bot.config import ApiConfig, AppConfig, SearchConfig
from flight_deals_bot.http import HttpError
from flight_deals_bot.models import Cabin, Quote, SourceLimits
from flight_deals_bot.pipeline import run_pipeline, select_summary_candidates
from flight_deals_bot.sources.base import BaseAdapter, SourceContext
from flight_deals_bot.storage import InMemoryStore


def test_in_memory_quota_enforces_daily_limit() -> None:
    store = InMemoryStore()
    limits = SourceLimits(daily=2, monthly=10)
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)

    assert store.try_consume_quota("x", limits, now=now) is True
    assert store.try_consume_quota("x", limits, now=now) is True
    assert store.try_consume_quota("x", limits, now=now) is False


def test_source_context_records_quota_only_after_successful_http_response() -> None:
    config = _config()
    config.source_limits["fixture"] = SourceLimits(daily=1, monthly=1)
    store = InMemoryStore()

    failing = SourceContext(config=config, http=FailingHttp(), store=store)
    with pytest.raises(HttpError):
        failing.get_json("fixture", "https://example.test/fail")

    assert store.quota_usage.get("fixture", []) == []
    assert failing.provider_request_count("fixture") == 1

    successful = SourceContext(config=config, http=SuccessHttp(), store=store)
    assert successful.get_json("fixture", "https://example.test/ok") == {"ok": True}
    assert len(store.quota_usage["fixture"]) == 1
    assert successful.get_json("fixture", "https://example.test/skipped") is None
    assert successful.was_quota_blocked("fixture") is True


def test_quota_reset_clears_local_usage() -> None:
    store = InMemoryStore()
    limits = SourceLimits(daily=1, monthly=1)
    assert store.try_consume_quota("searchapi", limits) is True
    assert store.try_consume_quota("searchapi", limits) is False

    store.reset_quota("searchapi")

    assert store.try_consume_quota("searchapi", limits) is True


def test_pipeline_dry_run_scores_and_formats_alert() -> None:
    config = _config()
    store = InMemoryStore()
    output = io.StringIO()

    result = run_pipeline(
        config=config,
        dry_run=True,
        store=store,
        adapters=[FixtureAdapter()],
        output=output,
    )

    assert result.discovered_count == 2
    assert result.alerted_count == 1
    assert "低價機票提醒" in result.messages[0]
    assert "Taiwan Taoyuan International Airport" in result.messages[0]
    assert "Narita International Airport" in result.messages[0]
    assert "日本" in result.messages[0]


def test_summary_candidates_keep_one_lowest_per_destination_cabin_and_rotate_countries() -> None:
    quotes = [
        _summary_quote("HND", Cabin.FIRST, "70000"),
        _summary_quote("HND", Cabin.FIRST, "64000"),
        _summary_quote("KIX", Cabin.FIRST, "65000"),
        _summary_quote("ICN", Cabin.BUSINESS, "30000"),
        _summary_quote("HKG", Cabin.BUSINESS, "28000"),
        _summary_quote("LAX", Cabin.BUSINESS, "78000"),
    ]

    selected = select_summary_candidates(quotes, limit=4)

    assert len(selected) == 4
    assert selected[0].destination == "HND"
    assert selected[0].price == Decimal("64000")
    assert len([quote for quote in selected if quote.destination == "HND" and quote.cabin == Cabin.FIRST]) == 1
    assert {quote.destination for quote in selected} >= {"HND", "ICN", "HKG"}


class FixtureAdapter(BaseAdapter):
    name = "fixture"

    def enabled(self, config: AppConfig) -> bool:
        return True

    def discover(self, ctx: SourceContext) -> list[Quote]:
        return [
            Quote(
                source=self.name,
                origin="TPE",
                destination="NRT",
                departure_date=date(2026, 10, 1),
                return_date=date(2026, 10, 8),
                cabin=Cabin.ECONOMY,
                price=Decimal("30000"),
                airline="A",
            ),
            Quote(
                source=self.name,
                origin="TPE",
                destination="NRT",
                departure_date=date(2026, 10, 1),
                return_date=date(2026, 10, 8),
                cabin=Cabin.BUSINESS,
                price=Decimal("50000"),
                airline="B",
            ),
        ]


def _summary_quote(destination: str, cabin: Cabin, price: str) -> Quote:
    return Quote(
        source="fixture",
        origin="TPE",
        destination=destination,
        departure_date=date(2026, 7, 15),
        return_date=date(2026, 7, 22),
        cabin=cabin,
        price=Decimal(price),
    )


class FailingHttp:
    def get_json(self, url, params=None, headers=None):  # noqa: ANN001
        raise HttpError(500, url, "boom")


class SuccessHttp:
    def get_json(self, url, params=None, headers=None):  # noqa: ANN001
        return {"ok": True}


def _config() -> AppConfig:
    return AppConfig(
        search=SearchConfig(
            origins=("TPE",),
            currency="TWD",
            adults=1,
            min_days_ahead=14,
            max_days_ahead=365,
            stay_lengths=(7,),
            cabins=(Cabin.ECONOMY, Cabin.BUSINESS),
            top_verify_limit=5,
            max_alerts_per_run=3,
            searchapi_explore_limit=3,
            searchapi_calendar_limit=6,
            searchapi_calendar_destinations=("NRT", "ICN"),
            searchapi_calendar_outbound_window_days=7,
            searchapi_calendar_window_step_days=45,
            require_verified_alerts=False,
            notify_no_deals=True,
            no_deal_candidate_limit=8,
            alert_cooldown=timedelta(hours=24),
            alert_price_drop_pct=5,
        ),
        api=ApiConfig(
            travelpayouts_token=None,
            travelpayouts_marker=None,
            amadeus_client_id=None,
            amadeus_client_secret=None,
            searchapi_key=None,
            kiwi_api_key=None,
            skyscanner_api_key=None,
            skyscanner_enabled=False,
        ),
        database_url=None,
        telegram_bot_token=None,
        telegram_chat_id=None,
        source_limits={"fixture": SourceLimits(daily=100, monthly=1000)},
    )
