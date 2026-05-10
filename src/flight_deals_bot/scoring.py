from __future__ import annotations

from decimal import Decimal

from .models import Baseline, Cabin, DealScore, Quote
from .storage import Store


def find_baseline(store: Store, quote: Quote) -> Baseline | None:
    rolling = store.rolling_baseline(quote, min_samples=3)
    if rolling:
        return rolling

    if quote.baseline_price_hint and quote.baseline_price_hint > quote.price:
        return Baseline(price=quote.baseline_price_hint, sample_size=1, source="source_hint")

    if quote.cabin != Cabin.ECONOMY:
        economy = store.rolling_baseline(quote, cabin=Cabin.ECONOMY, min_samples=1)
        if economy:
            return Baseline(
                price=economy.price * quote.cabin.premium_multiplier_from_economy,
                sample_size=economy.sample_size,
                source=f"economy_multiplier:{economy.source}",
            )

    return None


def score_quote(quote: Quote, baseline: Baseline) -> DealScore | None:
    if quote.price >= baseline.price:
        return None

    if quote.cabin in {Cabin.BUSINESS, Cabin.FIRST} and quote.longest_segment_cabin != quote.cabin:
        return None

    discount = (baseline.price - quote.price) / baseline.price
    threshold = quote.cabin.alert_discount_threshold
    if discount < threshold:
        return None

    cabin_boost = {
        Cabin.ECONOMY: Decimal("0"),
        Cabin.PREMIUM_ECONOMY: Decimal("8"),
        Cabin.BUSINESS: Decimal("15"),
        Cabin.FIRST: Decimal("25"),
    }[quote.cabin]
    score = (discount * Decimal("100")) + cabin_boost
    reason = f"{quote.cabin.value} fare is {as_percent(discount)} below {baseline.source}"
    return DealScore(quote=quote, baseline=baseline, discount=discount, score=score, reason=reason)


def as_percent(value: Decimal) -> str:
    return f"{(value * Decimal('100')).quantize(Decimal('1'))}%"


def should_suppress_alert(store: Store, score: DealScore, cooldown_hours: int, price_drop_pct: int) -> bool:
    previous = store.recent_alert(score.quote, cooldown_hours)
    if not previous:
        return False
    trigger_price = previous.price * (Decimal("1") - (Decimal(price_drop_pct) / Decimal("100")))
    return score.quote.price > trigger_price
