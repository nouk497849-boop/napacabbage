from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Protocol

from .models import AlertRecord, Baseline, Cabin, Quote, SourceLimits


class Store(Protocol):
    def setup(self) -> None: ...
    def quota_available(self, source: str, limits: SourceLimits, now: datetime | None = None) -> bool: ...
    def record_quota_usage(self, source: str, now: datetime | None = None) -> None: ...
    def try_consume_quota(self, source: str, limits: SourceLimits, now: datetime | None = None) -> bool: ...
    def reset_quota(self, source: str) -> None: ...
    def save_quotes(self, quotes: Iterable[Quote]) -> None: ...
    def rolling_baseline(self, quote: Quote, cabin: Cabin | None = None, min_samples: int = 3) -> Baseline | None: ...
    def recent_alert(self, quote: Quote, cooldown_hours: int) -> AlertRecord | None: ...
    def record_alert(self, quote: Quote, score: Decimal, baseline: Baseline, message: str) -> None: ...


class InMemoryStore:
    def __init__(self) -> None:
        self.quotes: list[Quote] = []
        self.quota_usage: dict[str, list[datetime]] = defaultdict(list)
        self.alerts: list[AlertRecord] = []

    def setup(self) -> None:
        return None

    def quota_available(self, source: str, limits: SourceLimits, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if limits.daily <= 0 or limits.monthly <= 0:
            return False
        day_count = sum(1 for item in self.quota_usage[source] if item.date() == now.date())
        month_count = sum(1 for item in self.quota_usage[source] if item.year == now.year and item.month == now.month)
        if day_count >= limits.daily or month_count >= limits.monthly:
            return False
        return True

    def record_quota_usage(self, source: str, now: datetime | None = None) -> None:
        self.quota_usage[source].append(now or datetime.now(timezone.utc))

    def try_consume_quota(self, source: str, limits: SourceLimits, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if not self.quota_available(source, limits, now=now):
            return False
        self.record_quota_usage(source, now=now)
        return True

    def reset_quota(self, source: str) -> None:
        self.quota_usage.pop(source, None)

    def save_quotes(self, quotes: Iterable[Quote]) -> None:
        self.quotes.extend(quotes)

    def rolling_baseline(self, quote: Quote, cabin: Cabin | None = None, min_samples: int = 3) -> Baseline | None:
        target_cabin = cabin or quote.cabin
        prices = [
            item.price
            for item in self.quotes
            if item.origin == quote.origin
            and item.destination == quote.destination
            and item.cabin == target_cabin
            and item.travel_month == quote.travel_month
            and item.stay_bucket == quote.stay_bucket
            and item.currency == quote.currency
            and item.deal_key != quote.deal_key
        ]
        if len(prices) < min_samples:
            return None
        prices = sorted(prices)
        midpoint = len(prices) // 2
        if len(prices) % 2:
            median = prices[midpoint]
        else:
            median = (prices[midpoint - 1] + prices[midpoint]) / Decimal("2")
        return Baseline(price=median, sample_size=len(prices), source=f"rolling:{target_cabin.value}")

    def recent_alert(self, quote: Quote, cooldown_hours: int) -> AlertRecord | None:
        now = datetime.now(timezone.utc)
        for alert in reversed(self.alerts):
            age_hours = (now - alert.sent_at).total_seconds() / 3600
            if alert.deal_key == quote.deal_key and age_hours <= cooldown_hours:
                return alert
        return None

    def record_alert(self, quote: Quote, score: Decimal, baseline: Baseline, message: str) -> None:
        self.alerts.append(AlertRecord(deal_key=quote.deal_key, price=quote.price, sent_at=datetime.now(timezone.utc)))


class PostgresStore:
    def __init__(self, database_url: str):
        try:
            import psycopg
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("psycopg is required when DATABASE_URL is set") from exc
        self._psycopg = psycopg
        self._jsonb = Jsonb
        self.database_url = database_url

    def setup(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS quotes (
                id BIGSERIAL PRIMARY KEY,
                deal_key TEXT NOT NULL,
                source TEXT NOT NULL,
                origin TEXT NOT NULL,
                destination TEXT NOT NULL,
                cabin TEXT NOT NULL,
                departure_date DATE NOT NULL,
                return_date DATE,
                stay_bucket INTEGER NOT NULL,
                travel_month DATE NOT NULL,
                price NUMERIC NOT NULL,
                currency TEXT NOT NULL,
                airline TEXT,
                stops INTEGER,
                self_transfer BOOLEAN NOT NULL DEFAULT FALSE,
                booking_url TEXT,
                found_at TIMESTAMPTZ NOT NULL,
                verified BOOLEAN NOT NULL DEFAULT FALSE,
                baseline_price_hint NUMERIC,
                raw JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """,
            "CREATE INDEX IF NOT EXISTS quotes_baseline_idx ON quotes (origin, destination, cabin, travel_month, stay_bucket, currency, found_at)",
            "CREATE INDEX IF NOT EXISTS quotes_deal_key_idx ON quotes (deal_key, found_at)",
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id BIGSERIAL PRIMARY KEY,
                deal_key TEXT NOT NULL,
                source TEXT NOT NULL,
                origin TEXT NOT NULL,
                destination TEXT NOT NULL,
                cabin TEXT NOT NULL,
                departure_date DATE NOT NULL,
                return_date DATE,
                price NUMERIC NOT NULL,
                currency TEXT NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL,
                score NUMERIC NOT NULL,
                baseline_price NUMERIC NOT NULL,
                baseline_source TEXT NOT NULL,
                message TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS alerts_deal_key_idx ON alerts (deal_key, sent_at)",
            """
            CREATE TABLE IF NOT EXISTS quota_usage (
                id BIGSERIAL PRIMARY KEY,
                source TEXT NOT NULL,
                used_at TIMESTAMPTZ NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS quota_usage_source_used_at_idx ON quota_usage (source, used_at)",
        ]
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)
            conn.commit()

    def quota_available(self, source: str, limits: SourceLimits, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if limits.daily <= 0 or limits.monthly <= 0:
            return False
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      COUNT(*) FILTER (WHERE used_at >= %(day_start)s) AS day_count,
                      COUNT(*) FILTER (WHERE used_at >= %(month_start)s) AS month_count
                    FROM quota_usage
                    WHERE source = %(source)s
                    """,
                    {"source": source, "day_start": day_start, "month_start": month_start},
                )
                day_count, month_count = cur.fetchone()
                if day_count >= limits.daily or month_count >= limits.monthly:
                    return False
        return True

    def record_quota_usage(self, source: str, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO quota_usage (source, used_at) VALUES (%s, %s)", (source, now))
            conn.commit()

    def try_consume_quota(self, source: str, limits: SourceLimits, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if not self.quota_available(source, limits, now=now):
            return False
        self.record_quota_usage(source, now=now)
        return True

    def reset_quota(self, source: str) -> None:
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM quota_usage WHERE source = %s", (source,))
            conn.commit()

    def save_quotes(self, quotes: Iterable[Quote]) -> None:
        rows = list(quotes)
        if not rows:
            return
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                for quote in rows:
                    cur.execute(
                        """
                        INSERT INTO quotes (
                            deal_key, source, origin, destination, cabin, departure_date, return_date,
                            stay_bucket, travel_month, price, currency, airline, stops, self_transfer,
                            booking_url, found_at, verified, baseline_price_hint, raw
                        )
                        VALUES (
                            %(deal_key)s, %(source)s, %(origin)s, %(destination)s, %(cabin)s,
                            %(departure_date)s, %(return_date)s, %(stay_bucket)s, %(travel_month)s,
                            %(price)s, %(currency)s, %(airline)s, %(stops)s, %(self_transfer)s,
                            %(booking_url)s, %(found_at)s, %(verified)s, %(baseline_price_hint)s,
                            %(raw)s
                        )
                        """,
                        {
                            "deal_key": quote.deal_key,
                            "source": quote.source,
                            "origin": quote.origin,
                            "destination": quote.destination,
                            "cabin": quote.cabin.value,
                            "departure_date": quote.departure_date,
                            "return_date": quote.return_date,
                            "stay_bucket": quote.stay_bucket,
                            "travel_month": quote.travel_month,
                            "price": quote.price,
                            "currency": quote.currency,
                            "airline": quote.airline,
                            "stops": quote.stops,
                            "self_transfer": quote.self_transfer,
                            "booking_url": quote.booking_url,
                            "found_at": quote.found_at,
                            "verified": quote.verified,
                            "baseline_price_hint": quote.baseline_price_hint,
                            "raw": self._jsonb(_jsonable_raw(quote.raw)),
                        },
                    )
            conn.commit()

    def rolling_baseline(self, quote: Quote, cabin: Cabin | None = None, min_samples: int = 3) -> Baseline | None:
        target_cabin = cabin or quote.cabin
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*), percentile_cont(0.5) WITHIN GROUP (ORDER BY price)
                    FROM quotes
                    WHERE origin = %(origin)s
                      AND destination = %(destination)s
                      AND cabin = %(cabin)s
                      AND travel_month = %(travel_month)s
                      AND stay_bucket = %(stay_bucket)s
                      AND currency = %(currency)s
                      AND deal_key <> %(deal_key)s
                      AND found_at >= now() - interval '180 days'
                    """,
                    {
                        "origin": quote.origin,
                        "destination": quote.destination,
                        "cabin": target_cabin.value,
                        "travel_month": quote.travel_month,
                        "stay_bucket": quote.stay_bucket,
                        "currency": quote.currency,
                        "deal_key": quote.deal_key,
                    },
                )
                count, median = cur.fetchone()
        if count < min_samples or median is None:
            return None
        return Baseline(price=Decimal(str(median)), sample_size=count, source=f"rolling:{target_cabin.value}")

    def recent_alert(self, quote: Quote, cooldown_hours: int) -> AlertRecord | None:
        since = datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT deal_key, price, sent_at
                    FROM alerts
                    WHERE deal_key = %s
                      AND sent_at >= %s
                    ORDER BY sent_at DESC
                    LIMIT 1
                    """,
                    (quote.deal_key, since),
                )
                row = cur.fetchone()
        if not row:
            return None
        return AlertRecord(deal_key=row[0], price=Decimal(str(row[1])), sent_at=row[2])

    def record_alert(self, quote: Quote, score: Decimal, baseline: Baseline, message: str) -> None:
        with self._psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alerts (
                        deal_key, source, origin, destination, cabin, departure_date, return_date,
                        price, currency, sent_at, score, baseline_price, baseline_source, message
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        quote.deal_key,
                        quote.source,
                        quote.origin,
                        quote.destination,
                        quote.cabin.value,
                        quote.departure_date,
                        quote.return_date,
                        quote.price,
                        quote.currency,
                        datetime.now(timezone.utc),
                        score,
                        baseline.price,
                        baseline.source,
                        message,
                    ),
                )
            conn.commit()


def build_store(database_url: str | None) -> Store:
    if database_url:
        return PostgresStore(database_url)
    return InMemoryStore()


def _jsonable_raw(raw: dict) -> dict:
    def convert(value):
        if isinstance(value, Decimal):
            return str(value)
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(v) for v in value]
        return value

    return {k: convert(v) for k, v in raw.items()}
