from __future__ import annotations

from datetime import date
from decimal import Decimal

from flight_deals_bot.models import Baseline, Cabin, Quote, Segment
from flight_deals_bot.scoring import as_price_ratio, find_baseline, score_quote
from flight_deals_bot.storage import InMemoryStore


def _quote(price: str, cabin: Cabin = Cabin.ECONOMY, airline: str = "CI") -> Quote:
    return Quote(
        source="fixture",
        origin="TPE",
        destination="NRT",
        departure_date=date(2026, 10, 1),
        return_date=date(2026, 10, 8),
        cabin=cabin,
        price=Decimal(price),
        airline=airline,
    )


def test_economy_quote_scores_when_below_rolling_median_threshold() -> None:
    store = InMemoryStore()
    store.save_quotes([_quote("10000", airline="A"), _quote("11000", airline="B"), _quote("12000", airline="C")])
    deal = _quote("7000", airline="D")

    baseline = find_baseline(store, deal)
    assert baseline is not None
    scored = score_quote(deal, baseline)

    assert scored is not None
    assert scored.discount >= Cabin.ECONOMY.alert_discount_threshold


def test_business_quote_bootstraps_from_economy_baseline() -> None:
    store = InMemoryStore()
    store.save_quotes([_quote("30000", airline="A")])
    deal = _quote("50000", cabin=Cabin.BUSINESS, airline="B")

    baseline = find_baseline(store, deal)

    assert baseline is not None
    assert baseline.price == Decimal("96000.0")
    assert score_quote(deal, baseline) is not None


def test_premium_quote_rejects_when_longest_segment_is_not_target_cabin() -> None:
    deal = Quote(
        source="fixture",
        origin="TPE",
        destination="LAX",
        departure_date=date(2026, 10, 1),
        return_date=date(2026, 10, 8),
        cabin=Cabin.BUSINESS,
        price=Decimal("50000"),
        segments=(
            Segment("TPE", "NRT", cabin=Cabin.BUSINESS, duration_minutes=180),
            Segment("NRT", "LAX", cabin=Cabin.ECONOMY, duration_minutes=600),
        ),
    )
    baseline = Baseline(price=Decimal("120000"), sample_size=5, source="fixture")

    assert score_quote(deal, baseline) is None


def test_absolute_low_price_rule_scores_regional_first_without_history() -> None:
    deal = Quote(
        source="fixture",
        origin="TPE",
        destination="HND",
        departure_date=date(2026, 7, 15),
        return_date=date(2026, 7, 18),
        cabin=Cabin.FIRST,
        price=Decimal("64046"),
    )
    store = InMemoryStore()

    baseline = find_baseline(store, deal)
    assert baseline is not None
    scored = score_quote(deal, baseline)

    assert baseline.source == "absolute:northeast_asia"
    assert baseline.price == Decimal("150000")
    assert scored is not None
    assert as_price_ratio(scored.quote.price, scored.baseline.price) == "43%"
