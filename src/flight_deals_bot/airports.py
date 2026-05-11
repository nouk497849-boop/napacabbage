from __future__ import annotations

from functools import lru_cache
from typing import Any


COUNTRY_NAME_OVERRIDES = {
    "TW": "Taiwan",
}


@lru_cache(maxsize=1)
def _airport_data() -> dict[str, dict[str, Any]]:
    try:
        import airportsdata
    except ImportError:
        return {}
    return airportsdata.load("IATA")


@lru_cache(maxsize=256)
def country_name(alpha_2: str | None) -> str | None:
    if not alpha_2:
        return None
    override = COUNTRY_NAME_OVERRIDES.get(alpha_2.upper())
    if override:
        return override
    try:
        import pycountry
    except ImportError:
        return alpha_2
    country = pycountry.countries.get(alpha_2=alpha_2.upper())
    return country.name if country else alpha_2


def airport_country_code(iata_code: str) -> str | None:
    airport = _airport_data().get(iata_code.upper())
    if not airport:
        return None
    country = airport.get("country")
    return str(country).upper() if country else None


def airport_label(iata_code: str) -> str:
    code = iata_code.upper()
    airport = _airport_data().get(code)
    if not airport:
        return code
    name = airport.get("name") or code
    country = country_name(airport.get("country"))
    if country:
        return f"{code} {name}, {country}"
    return f"{code} {name}"
