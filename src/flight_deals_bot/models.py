from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Cabin(StrEnum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"

    @classmethod
    def parse(cls, value: str | None) -> "Cabin":
        if not value:
            return cls.ECONOMY
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "m": cls.ECONOMY,
            "y": cls.ECONOMY,
            "economy_class": cls.ECONOMY,
            "w": cls.PREMIUM_ECONOMY,
            "premium": cls.PREMIUM_ECONOMY,
            "premiumeconomy": cls.PREMIUM_ECONOMY,
            "premium_economy": cls.PREMIUM_ECONOMY,
            "c": cls.BUSINESS,
            "j": cls.BUSINESS,
            "business_class": cls.BUSINESS,
            "f": cls.FIRST,
            "first_class": cls.FIRST,
        }
        return aliases.get(normalized, cls(normalized))

    @property
    def alert_discount_threshold(self) -> Decimal:
        return {
            Cabin.ECONOMY: Decimal("0.30"),
            Cabin.PREMIUM_ECONOMY: Decimal("0.40"),
            Cabin.BUSINESS: Decimal("0.45"),
            Cabin.FIRST: Decimal("0.55"),
        }[self]

    @property
    def premium_multiplier_from_economy(self) -> Decimal:
        return {
            Cabin.ECONOMY: Decimal("1.0"),
            Cabin.PREMIUM_ECONOMY: Decimal("1.8"),
            Cabin.BUSINESS: Decimal("3.2"),
            Cabin.FIRST: Decimal("5.0"),
        }[self]


@dataclass(frozen=True)
class Segment:
    origin: str
    destination: str
    departure_at: datetime | None = None
    arrival_at: datetime | None = None
    marketing_carrier: str | None = None
    flight_number: str | None = None
    cabin: Cabin | None = None
    duration_minutes: int | None = None


@dataclass(frozen=True)
class Quote:
    source: str
    origin: str
    destination: str
    departure_date: date
    return_date: date | None
    cabin: Cabin
    price: Decimal
    currency: str = "TWD"
    airline: str | None = None
    stops: int | None = None
    self_transfer: bool = False
    booking_url: str | None = None
    found_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    segments: tuple[Segment, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    baseline_price_hint: Decimal | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin", self.origin.upper())
        object.__setattr__(self, "destination", self.destination.upper())
        object.__setattr__(self, "currency", self.currency.upper())
        if self.price <= 0:
            raise ValueError("quote price must be positive")

    @property
    def stay_nights(self) -> int | None:
        if self.return_date is None:
            return None
        return (self.return_date - self.departure_date).days

    @property
    def stay_bucket(self) -> int:
        nights = self.stay_nights
        if nights is None:
            return 0
        if nights <= 3:
            return 3
        if nights <= 5:
            return 5
        if nights <= 7:
            return 7
        if nights <= 10:
            return 10
        if nights <= 14:
            return 14
        return 21

    @property
    def travel_month(self) -> date:
        return date(self.departure_date.year, self.departure_date.month, 1)

    @property
    def itinerary_signature(self) -> str:
        if self.segments:
            parts = []
            for segment in self.segments:
                carrier = segment.marketing_carrier or ""
                flight = segment.flight_number or ""
                parts.append(f"{segment.origin}-{segment.destination}-{carrier}{flight}")
            return "/".join(parts)
        return f"{self.airline or 'unknown'}-{self.stops if self.stops is not None else 'x'}"

    @property
    def deal_key(self) -> str:
        return "|".join(
            [
                self.origin,
                self.destination,
                self.departure_date.isoformat(),
                self.return_date.isoformat() if self.return_date else "",
                self.cabin.value,
                self.itinerary_signature,
            ]
        )

    @property
    def longest_segment_cabin(self) -> Cabin:
        if not self.segments:
            return self.cabin
        with_duration = [s for s in self.segments if s.duration_minutes and s.cabin]
        if with_duration:
            return max(with_duration, key=lambda s: s.duration_minutes or 0).cabin or self.cabin
        cabins = [s.cabin for s in self.segments if s.cabin]
        return cabins[0] if cabins else self.cabin

    @property
    def mixed_cabin(self) -> bool:
        cabins = {s.cabin for s in self.segments if s.cabin}
        return len(cabins) > 1


@dataclass(frozen=True)
class Baseline:
    price: Decimal
    sample_size: int
    source: str


@dataclass(frozen=True)
class DealScore:
    quote: Quote
    baseline: Baseline
    discount: Decimal
    score: Decimal
    reason: str


@dataclass(frozen=True)
class SourceLimits:
    daily: int
    monthly: int


@dataclass(frozen=True)
class AlertRecord:
    deal_key: str
    price: Decimal
    sent_at: datetime
