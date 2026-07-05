from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DATETIME_FORMATS = (
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d",
    "%Y-%m-%d",
)

COUNTRY_TIMEZONES = {
    "australia": "Australia/Sydney",
    "au": "Australia/Sydney",
    "belgium": "Europe/Brussels",
    "be": "Europe/Brussels",
    "canada": "America/Toronto",
    "ca": "America/Toronto",
    "china": "Asia/Shanghai",
    "cn": "Asia/Shanghai",
    "denmark": "Europe/Copenhagen",
    "dk": "Europe/Copenhagen",
    "france": "Europe/Paris",
    "fr": "Europe/Paris",
    "germany": "Europe/Berlin",
    "de": "Europe/Berlin",
    "hong kong": "Asia/Hong_Kong",
    "hk": "Asia/Hong_Kong",
    "ireland": "Europe/Dublin",
    "ie": "Europe/Dublin",
    "italy": "Europe/Rome",
    "it": "Europe/Rome",
    "japan": "Asia/Tokyo",
    "jp": "Asia/Tokyo",
    "macau": "Asia/Macau",
    "mo": "Asia/Macau",
    "malaysia": "Asia/Kuala_Lumpur",
    "my": "Asia/Kuala_Lumpur",
    "netherlands": "Europe/Amsterdam",
    "nl": "Europe/Amsterdam",
    "new zealand": "Pacific/Auckland",
    "nz": "Pacific/Auckland",
    "norway": "Europe/Oslo",
    "no": "Europe/Oslo",
    "portugal": "Europe/Lisbon",
    "pt": "Europe/Lisbon",
    "singapore": "Asia/Singapore",
    "sg": "Asia/Singapore",
    "south korea": "Asia/Seoul",
    "korea": "Asia/Seoul",
    "kr": "Asia/Seoul",
    "spain": "Europe/Madrid",
    "es": "Europe/Madrid",
    "sweden": "Europe/Stockholm",
    "se": "Europe/Stockholm",
    "switzerland": "Europe/Zurich",
    "ch": "Europe/Zurich",
    "taiwan": "Asia/Taipei",
    "tw": "Asia/Taipei",
    "thailand": "Asia/Bangkok",
    "th": "Asia/Bangkok",
    "united arab emirates": "Asia/Dubai",
    "uae": "Asia/Dubai",
    "ae": "Asia/Dubai",
    "united kingdom": "Europe/London",
    "great britain": "Europe/London",
    "uk": "Europe/London",
    "gb": "Europe/London",
    "england": "Europe/London",
    "scotland": "Europe/London",
    "wales": "Europe/London",
    "northern ireland": "Europe/London",
    "united states": "America/New_York",
    "united states of america": "America/New_York",
    "usa": "America/New_York",
    "us": "America/New_York",
}

FALLBACK_TIMEZONE_OFFSETS = {
    "America/New_York": -5,
    "America/Los_Angeles": -8,
    "America/Toronto": -5,
    "Asia/Bangkok": 7,
    "Asia/Dubai": 4,
    "Asia/Hong_Kong": 8,
    "Asia/Kuala_Lumpur": 8,
    "Asia/Macau": 8,
    "Asia/Seoul": 9,
    "Asia/Shanghai": 8,
    "Asia/Singapore": 8,
    "Asia/Taipei": 8,
    "Asia/Tokyo": 9,
    "Australia/Sydney": 10,
    "Europe/Amsterdam": 1,
    "Europe/Berlin": 1,
    "Europe/Brussels": 1,
    "Europe/Copenhagen": 1,
    "Europe/Dublin": 0,
    "Europe/Lisbon": 0,
    "Europe/London": 0,
    "Europe/Madrid": 1,
    "Europe/Oslo": 1,
    "Europe/Paris": 1,
    "Europe/Rome": 1,
    "Europe/Stockholm": 1,
    "Europe/Zurich": 1,
    "Pacific/Auckland": 12,
}

DYNAMIC_FALLBACK_TIMEZONES = {
    "Europe/London": lambda: EuropeLondonFallback(),
}


class EuropeLondonFallback(tzinfo):
    key = "Europe/London"

    def utcoffset(self, dt):
        return timedelta(hours=1) if self.dst(dt) else timedelta(0)

    def dst(self, dt):
        if dt is None:
            return timedelta(0)
        naive = dt.replace(tzinfo=None)
        start = datetime(naive.year, 3, _last_sunday(naive.year, 3).day, 2, 0)
        end = datetime(naive.year, 10, _last_sunday(naive.year, 10).day, 2, 0)
        return timedelta(hours=1) if start <= naive < end else timedelta(0)

    def tzname(self, dt):
        return "BST" if self.dst(dt) else "GMT"

    def __str__(self) -> str:
        return self.key

    def fromutc(self, dt):
        naive_utc = dt.replace(tzinfo=None)
        start_utc = datetime(naive_utc.year, 3, _last_sunday(naive_utc.year, 3).day, 1, 0)
        end_utc = datetime(naive_utc.year, 10, _last_sunday(naive_utc.year, 10).day, 1, 0)
        offset = timedelta(hours=1) if start_utc <= naive_utc < end_utc else timedelta(0)
        return (naive_utc + offset).replace(tzinfo=self)


def get_timezone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        dynamic_factory = DYNAMIC_FALLBACK_TIMEZONES.get(name)
        if dynamic_factory:
            return dynamic_factory()
        if name in FALLBACK_TIMEZONE_OFFSETS:
            return timezone(timedelta(hours=FALLBACK_TIMEZONE_OFFSETS[name]), name)
        raise


def resolve_order_timezone(
    *,
    country: str,
    default_tz,
    country_code: str = "",
    timezone_name: str = "",
):
    explicit_timezone = timezone_name.strip()
    if explicit_timezone:
        try:
            return get_timezone(explicit_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unsupported timezone: {explicit_timezone}") from exc

    for value in (country_code, country):
        timezone_for_country = COUNTRY_TIMEZONES.get(_normalize_country_key(value))
        if timezone_for_country:
            return get_timezone(timezone_for_country)
    return default_tz


def timezone_label(tz) -> str:
    return getattr(tz, "key", None) or str(tz)


def parse_datetime(value: str, tz) -> datetime | None:
    text = value.strip()
    if not text:
        return None

    normalized = text.replace("T", " ")
    for fmt in DATETIME_FORMATS:
        try:
            parsed = datetime.strptime(normalized, fmt)
            return _attach_timezone(parsed, tz)
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(text)
        return _attach_timezone(parsed, tz)
    except ValueError as exc:
        raise ValueError(f"Unsupported datetime format: {value!r}") from exc


def parse_date(value: str) -> date:
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unsupported date format: {value!r}")


def parse_clock(value: str) -> time:
    text = value.strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            pass
    raise ValueError(f"Unsupported time format: {value!r}")


def _attach_timezone(value: datetime, tz) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _normalize_country_key(value: str) -> str:
    return " ".join(
        "".join(character.casefold() if character.isalnum() else " " for character in (value or "")).split()
    )


def _last_sunday(year: int, month: int) -> date:
    candidate = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while candidate.weekday() != 6:
        candidate -= timedelta(days=1)
    return candidate
