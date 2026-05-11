from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flight_deals_bot.config import ApiConfig, AppConfig, SearchConfig
from flight_deals_bot.models import Cabin, Quote, SourceLimits
from flight_deals_bot.pipeline import run_pipeline
from flight_deals_bot.sources.base import BaseAdapter, SourceContext
from flight_deals_bot.storage import InMemoryStore


def test_in_memory_quota_enforces_daily_limit() -> None:
    store = InMemoryStore()
    limits = SourceLimits(daily=2, monthly=10)
    now = datetime(2026, 5, 10, tzinfo=timezone.utc)

    assert store.try_consume_quota("x", limits, now=now) is True
    assert store.try_consume_quota("x", limits, now=now) is True
    assert store.try_consume_quota("x", limits, now=now) is False


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
    assert "TPE -&gt; NRT" in result.messages[0]


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
