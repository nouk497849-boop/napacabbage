from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .airports import airport_country_code
from .models import Baseline, Cabin, DealScore, Quote
from .storage import Store


@dataclass(frozen=True)
class AbsolutePriceRule:
    region: str
    max_price: Decimal
    reference_price: Decimal


NORTHEAST_ASIA_CODES = {"JP", "KR", "HK", "MO", "CN"}
SOUTHEAST_ASIA_CODES = {"BN", "ID", "KH", "LA", "MM", "MY", "PH", "SG", "TH", "VN"}
LONG_HAUL_CODES = {
    "US",
    "CA",
    "MX",
    "AU",
    "NZ",
    "AT",
    "BE",
    "CH",
    "CZ",
    "DE",
    "DK",
    "ES",
    "FI",
    "FR",
    "GB",
    "GR",
    "HU",
    "IE",
    "IS",
    "IT",
    "NL",
    "NO",
    "PL",
    "PT",
    "SE",
    "TR",
}

ABSOLUTE_PRICE_RULES: dict[str, dict[Cabin, AbsolutePriceRule]] = {
    "northeast_asia": {
        Cabin.ECONOMY: AbsolutePriceRule("northeast_asia", Decimal("6000"), Decimal("12000")),
        Cabin.BUSINESS: AbsolutePriceRule("northeast_asia", Decimal("35000"), Decimal("70000")),
        Cabin.FIRST: AbsolutePriceRule("northeast_asia", Decimal("75000"), Decimal("150000")),
    },
    "southeast_asia": {
        Cabin.ECONOMY: AbsolutePriceRule("southeast_asia", Decimal("7000"), Decimal("14000")),
        Cabin.BUSINESS: AbsolutePriceRule("southeast_asia", Decimal("40000"), Decimal("80000")),
        Cabin.FIRST: AbsolutePriceRule("southeast_asia", Decimal("90000"), Decimal("180000")),
    },
    "long_haul": {
        Cabin.ECONOMY: AbsolutePriceRule("long_haul", Decimal("20000"), Decimal("35000")),
        Cabin.BUSINESS: AbsolutePriceRule("long_haul", Decimal("80000"), Decimal("160000")),
        Cabin.FIRST: AbsolutePriceRule("long_haul", Decimal("160000"), Decimal("300000")),
    },
    "other": {
        Cabin.ECONOMY: AbsolutePriceRule("other", Decimal("18000"), Decimal("30000")),
        Cabin.BUSINESS: AbsolutePriceRule("other", Decimal("70000"), Decimal("140000")),
        Cabin.FIRST: AbsolutePriceRule("other", Decimal("150000"), Decimal("280000")),
    },
}


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

    absolute = absolute_price_baseline(quote)
    if absolute:
        return absolute

    return None


def score_quote(quote: Quote, baseline: Baseline) -> DealScore | None:
    if quote.cabin in {Cabin.BUSINESS, Cabin.FIRST} and quote.longest_segment_cabin != quote.cabin:
        return None

    absolute = absolute_price_baseline(quote)
    effective_baseline = baseline
    if absolute and absolute.price > baseline.price:
        effective_baseline = absolute

    if quote.price >= effective_baseline.price:
        return None

    discount = (effective_baseline.price - quote.price) / effective_baseline.price
    threshold = quote.cabin.alert_discount_threshold
    if discount < threshold and not absolute:
        return None

    cabin_boost = {
        Cabin.ECONOMY: Decimal("0"),
        Cabin.PREMIUM_ECONOMY: Decimal("8"),
        Cabin.BUSINESS: Decimal("15"),
        Cabin.FIRST: Decimal("25"),
    }[quote.cabin]
    absolute_boost = Decimal("8") if absolute else Decimal("0")
    score = (discount * Decimal("100")) + cabin_boost + absolute_boost
    reason = f"{quote.cabin.value} fare is {as_percent(discount)} below {effective_baseline.source}"
    return DealScore(quote=quote, baseline=effective_baseline, discount=discount, score=score, reason=reason)


def absolute_price_baseline(quote: Quote) -> Baseline | None:
    rule = absolute_price_rule(quote)
    if not rule:
        return None
    return Baseline(price=rule.reference_price, sample_size=0, source=f"absolute:{rule.region}")


def absolute_price_rule(quote: Quote) -> AbsolutePriceRule | None:
    if quote.currency != "TWD":
        return None
    rule = absolute_reference_rule(quote)
    if rule and quote.price <= rule.max_price:
        return rule
    return None


def absolute_reference_rule(quote: Quote) -> AbsolutePriceRule | None:
    if quote.currency != "TWD":
        return None
    if quote.cabin == Cabin.PREMIUM_ECONOMY:
        return None
    region = destination_region(quote.destination)
    return ABSOLUTE_PRICE_RULES[region].get(quote.cabin)


def display_reference_price(quote: Quote, baseline: Baseline | None = None) -> Decimal | None:
    if baseline and baseline.price > 0:
        return baseline.price
    if quote.baseline_price_hint and quote.baseline_price_hint > quote.price:
        return quote.baseline_price_hint
    rule = absolute_reference_rule(quote)
    if rule and rule.reference_price > quote.price:
        return rule.reference_price
    return None


def destination_region(destination: str) -> str:
    country = airport_country_code(destination)
    if country in NORTHEAST_ASIA_CODES:
        return "northeast_asia"
    if country in SOUTHEAST_ASIA_CODES:
        return "southeast_asia"
    if country in LONG_HAUL_CODES:
        return "long_haul"
    return "other"


def as_percent(value: Decimal) -> str:
    return f"{(value * Decimal('100')).quantize(Decimal('1'))}%"


def as_price_ratio(price: Decimal, reference: Decimal) -> str:
    if reference <= 0:
        return "unknown"
    return f"{((price / reference) * Decimal('100')).quantize(Decimal('1'))}%"


def should_suppress_alert(store: Store, score: DealScore, cooldown_hours: int, price_drop_pct: int) -> bool:
    previous = store.recent_alert(score.quote, cooldown_hours)
    if not previous:
        return False
    trigger_price = previous.price * (Decimal("1") - (Decimal(price_drop_pct) / Decimal("100")))
    return score.quote.price > trigger_price
