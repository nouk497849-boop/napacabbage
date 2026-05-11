from __future__ import annotations

from functools import lru_cache
from typing import Any


COUNTRY_NAME_OVERRIDES = {
    "TW": "台灣",
    "HK": "香港",
    "MO": "澳門",
    "CN": "中國",
    "KR": "南韓",
    "US": "美國",
    "GB": "英國",
}

CONTINENT_LABELS = {
    "asia": "亞洲",
    "europe": "歐洲",
    "africa": "非洲",
    "americas": "美洲",
    "oceania": "大洋洲",
    "other": "其他",
}

ASIA_CODES = {
    "AE",
    "AF",
    "AM",
    "AZ",
    "BD",
    "BH",
    "BN",
    "BT",
    "CC",
    "CN",
    "CX",
    "CY",
    "GE",
    "HK",
    "ID",
    "IL",
    "IN",
    "IO",
    "IQ",
    "IR",
    "JO",
    "JP",
    "KG",
    "KH",
    "KP",
    "KR",
    "KW",
    "KZ",
    "LA",
    "LB",
    "LK",
    "MM",
    "MN",
    "MO",
    "MV",
    "MY",
    "NP",
    "OM",
    "PH",
    "PK",
    "PS",
    "QA",
    "SA",
    "SG",
    "SY",
    "TH",
    "TJ",
    "TM",
    "TR",
    "TW",
    "UZ",
    "VN",
    "YE",
}

EUROPE_CODES = {
    "AD",
    "AL",
    "AT",
    "AX",
    "BA",
    "BE",
    "BG",
    "BY",
    "CH",
    "CZ",
    "DE",
    "DK",
    "EE",
    "ES",
    "FI",
    "FO",
    "FR",
    "GB",
    "GG",
    "GI",
    "GR",
    "HR",
    "HU",
    "IE",
    "IM",
    "IS",
    "IT",
    "JE",
    "LI",
    "LT",
    "LU",
    "LV",
    "MC",
    "MD",
    "ME",
    "MK",
    "MT",
    "NL",
    "NO",
    "PL",
    "PT",
    "RO",
    "RS",
    "RU",
    "SE",
    "SI",
    "SJ",
    "SK",
    "SM",
    "UA",
    "VA",
    "XK",
}

AFRICA_CODES = {
    "AO",
    "BF",
    "BI",
    "BJ",
    "BW",
    "CD",
    "CF",
    "CG",
    "CI",
    "CM",
    "CV",
    "DJ",
    "DZ",
    "EG",
    "EH",
    "ER",
    "ET",
    "GA",
    "GH",
    "GM",
    "GN",
    "GQ",
    "GW",
    "KE",
    "KM",
    "LR",
    "LS",
    "LY",
    "MA",
    "MG",
    "ML",
    "MR",
    "MU",
    "MW",
    "MZ",
    "NA",
    "NE",
    "NG",
    "RE",
    "RW",
    "SC",
    "SD",
    "SH",
    "SL",
    "SN",
    "SO",
    "SS",
    "ST",
    "SZ",
    "TD",
    "TG",
    "TN",
    "TZ",
    "UG",
    "YT",
    "ZA",
    "ZM",
    "ZW",
}

AMERICAS_CODES = {
    "AG",
    "AI",
    "AR",
    "AW",
    "BB",
    "BL",
    "BM",
    "BO",
    "BQ",
    "BR",
    "BS",
    "BZ",
    "CA",
    "CL",
    "CO",
    "CR",
    "CU",
    "CW",
    "DM",
    "DO",
    "EC",
    "FK",
    "GD",
    "GF",
    "GL",
    "GP",
    "GS",
    "GT",
    "GY",
    "HN",
    "HT",
    "JM",
    "KN",
    "KY",
    "LC",
    "MF",
    "MQ",
    "MS",
    "MX",
    "NI",
    "PA",
    "PE",
    "PM",
    "PR",
    "PY",
    "SR",
    "SV",
    "SX",
    "TC",
    "TT",
    "US",
    "UY",
    "VC",
    "VE",
    "VG",
    "VI",
}

OCEANIA_CODES = {
    "AS",
    "AU",
    "CK",
    "FJ",
    "FM",
    "GU",
    "KI",
    "MH",
    "MP",
    "NC",
    "NF",
    "NR",
    "NU",
    "NZ",
    "PF",
    "PG",
    "PN",
    "PW",
    "SB",
    "TK",
    "TO",
    "TV",
    "UM",
    "VU",
    "WF",
    "WS",
}


@lru_cache(maxsize=1)
def _airport_data() -> dict[str, dict[str, Any]]:
    try:
        import airportsdata
    except ImportError:
        return {}
    return airportsdata.load("IATA")


@lru_cache(maxsize=1)
def _zh_locale():
    try:
        from babel import Locale
    except ImportError:
        return None
    return Locale.parse("zh_Hant_TW")


@lru_cache(maxsize=256)
def country_name(alpha_2: str | None) -> str | None:
    if not alpha_2:
        return None
    code = alpha_2.upper()
    override = COUNTRY_NAME_OVERRIDES.get(code)
    if override:
        return override
    locale = _zh_locale()
    if locale:
        territory = locale.territories.get(code)
        if territory:
            return str(territory)
    try:
        import pycountry
    except ImportError:
        return code
    country = pycountry.countries.get(alpha_2=code)
    return country.name if country else code


def continent_key_for_country(alpha_2: str | None) -> str:
    if not alpha_2:
        return "other"
    code = alpha_2.upper()
    if code in ASIA_CODES:
        return "asia"
    if code in EUROPE_CODES:
        return "europe"
    if code in AFRICA_CODES:
        return "africa"
    if code in AMERICAS_CODES:
        return "americas"
    if code in OCEANIA_CODES:
        return "oceania"
    return "other"


def continent_label_for_country(alpha_2: str | None) -> str:
    return CONTINENT_LABELS[continent_key_for_country(alpha_2)]


def airport_country_code(iata_code: str) -> str | None:
    airport = _airport_data().get(iata_code.upper())
    if not airport:
        return None
    country = airport.get("country")
    return str(country).upper() if country else None


def airport_continent_key(iata_code: str) -> str:
    return continent_key_for_country(airport_country_code(iata_code))


def airport_continent_label(iata_code: str) -> str:
    return CONTINENT_LABELS[airport_continent_key(iata_code)]


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
