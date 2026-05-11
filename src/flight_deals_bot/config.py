from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from .models import Cabin, SourceLimits


def _csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SearchConfig:
    origins: tuple[str, ...]
    currency: str
    adults: int
    min_days_ahead: int
    max_days_ahead: int
    stay_lengths: tuple[int, ...]
    cabins: tuple[Cabin, ...]
    top_verify_limit: int
    max_alerts_per_run: int
    searchapi_explore_limit: int
    searchapi_calendar_limit: int
    searchapi_calendar_destinations: tuple[str, ...]
    searchapi_calendar_outbound_window_days: int
    searchapi_calendar_window_step_days: int
    require_verified_alerts: bool
    notify_no_deals: bool
    no_deal_candidate_limit: int
    alert_cooldown: timedelta
    alert_price_drop_pct: int


@dataclass(frozen=True)
class ApiConfig:
    travelpayouts_token: str | None
    travelpayouts_marker: str | None
    amadeus_client_id: str | None
    amadeus_client_secret: str | None
    searchapi_key: str | None
    kiwi_api_key: str | None
    skyscanner_api_key: str | None
    skyscanner_enabled: bool


@dataclass(frozen=True)
class AppConfig:
    search: SearchConfig
    api: ApiConfig
    database_url: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    source_limits: dict[str, SourceLimits]


def load_config(env_file: str | None = ".env") -> AppConfig:
    if env_file:
        load_env_file(Path(env_file))
    cabins = tuple(Cabin.parse(item) for item in _csv(os.getenv("CABINS"), ("economy", "business", "first")))
    stay_lengths = tuple(int(item) for item in _csv(os.getenv("STAY_LENGTHS"), ("3", "5", "7", "10", "14", "21")))
    calendar_destinations = tuple(
        code.upper()
        for code in _csv(
            os.getenv("SEARCHAPI_CALENDAR_DESTINATIONS"),
            (
                "NRT",
                "HND",
                "KIX",
                "ICN",
                "HKG",
                "BKK",
                "SIN",
                "KUL",
                "MNL",
                "SGN",
                "DAD",
                "HAN",
                "DPS",
                "CGK",
                "SYD",
                "MEL",
                "LAX",
                "SFO",
                "YVR",
                "SEA",
                "HNL",
                "LHR",
                "CDG",
                "FRA",
                "AMS",
                "IST",
                "DXB",
            ),
        )
    )
    search = SearchConfig(
        origins=tuple(origin.upper() for origin in _csv(os.getenv("ORIGINS"), ("TPE", "TSA", "KHH", "RMQ", "TNN"))),
        currency=os.getenv("CURRENCY", "TWD").upper(),
        adults=_int("ADULTS", 1),
        min_days_ahead=_int("MIN_DAYS_AHEAD", 14),
        max_days_ahead=_int("MAX_DAYS_AHEAD", 365),
        stay_lengths=stay_lengths,
        cabins=cabins,
        top_verify_limit=_int("TOP_VERIFY_LIMIT", 18),
        max_alerts_per_run=_int("MAX_ALERTS_PER_RUN", 8),
        searchapi_explore_limit=_int("SEARCHAPI_EXPLORE_LIMIT", 3),
        searchapi_calendar_limit=_int("SEARCHAPI_CALENDAR_LIMIT", 6),
        searchapi_calendar_destinations=calendar_destinations,
        searchapi_calendar_outbound_window_days=_int("SEARCHAPI_CALENDAR_OUTBOUND_WINDOW_DAYS", 7),
        searchapi_calendar_window_step_days=_int("SEARCHAPI_CALENDAR_WINDOW_STEP_DAYS", 45),
        require_verified_alerts=_bool("REQUIRE_VERIFIED_ALERTS", False),
        notify_no_deals=_bool("NOTIFY_NO_DEALS", True),
        no_deal_candidate_limit=_int("NO_DEAL_CANDIDATE_LIMIT", 8),
        alert_cooldown=timedelta(hours=_int("ALERT_COOLDOWN_HOURS", 24)),
        alert_price_drop_pct=_int("ALERT_PRICE_DROP_PCT", 5),
    )
    api = ApiConfig(
        travelpayouts_token=os.getenv("TRAVELPAYOUTS_TOKEN") or None,
        travelpayouts_marker=os.getenv("TRAVELPAYOUTS_MARKER") or None,
        amadeus_client_id=os.getenv("AMADEUS_CLIENT_ID") or None,
        amadeus_client_secret=os.getenv("AMADEUS_CLIENT_SECRET") or None,
        searchapi_key=os.getenv("SEARCHAPI_KEY") or None,
        kiwi_api_key=os.getenv("KIWI_API_KEY") or None,
        skyscanner_api_key=os.getenv("SKYSCANNER_API_KEY") or None,
        skyscanner_enabled=_bool("SKYSCANNER_ENABLED", False),
    )
    source_limits = {
        "travelpayouts": SourceLimits(_int("TRAVELPAYOUTS_DAILY_LIMIT", 240), _int("TRAVELPAYOUTS_MONTHLY_LIMIT", 3000)),
        "amadeus": SourceLimits(_int("AMADEUS_DAILY_LIMIT", 80), _int("AMADEUS_MONTHLY_LIMIT", 1000)),
        "searchapi": SourceLimits(_int("SEARCHAPI_DAILY_LIMIT", 3), _int("SEARCHAPI_MONTHLY_LIMIT", 100)),
        "kiwi": SourceLimits(_int("KIWI_DAILY_LIMIT", 60), _int("KIWI_MONTHLY_LIMIT", 1000)),
        "skyscanner": SourceLimits(_int("SKYSCANNER_DAILY_LIMIT", 0), _int("SKYSCANNER_MONTHLY_LIMIT", 0)),
    }
    return AppConfig(
        search=search,
        api=api,
        database_url=os.getenv("DATABASE_URL") or None,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        source_limits=source_limits,
    )


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
