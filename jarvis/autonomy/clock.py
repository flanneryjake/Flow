"""When the daily auto-approval cap resets: 8 PM US Eastern (Jake's day, not UTC).

The US daylight-saving rule is written out here instead of using zoneinfo, because Windows Python often has
no tz database (no tzdata package) and the Workers run on Windows.

    from clock import last_reset, next_reset
    since = last_reset()          # aware UTC datetime of the most recent 8 PM ET
"""
import datetime as dt
import os

UTC = dt.timezone.utc


def _reset_hour():
    try:
        return int(os.environ.get('JARVIS_CAP_RESET_HOUR', 20))
    except ValueError:
        return 20


def _nth_sunday(year, month, n):
    d = dt.datetime(year, month, 1, tzinfo=UTC)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def eastern_offset(utc):
    """UTC offset of US Eastern at the UTC instant `utc` (EDT -4 from 2nd Sunday of March 2 AM, EST -5 from
    1st Sunday of November 2 AM)."""
    start = _nth_sunday(utc.year, 3, 2).replace(hour=7)   # 2 AM EST = 07:00 UTC
    end = _nth_sunday(utc.year, 11, 1).replace(hour=6)    # 2 AM EDT = 06:00 UTC
    return dt.timedelta(hours=-4 if start <= utc < end else -5)


def now_utc():
    return dt.datetime.now(UTC)


def last_reset(now=None):
    """The most recent cap reset at or before `now`, as an aware UTC datetime."""
    now = now or now_utc()
    off = eastern_offset(now)
    local = (now + off).replace(tzinfo=None)
    reset = local.replace(hour=_reset_hour(), minute=0, second=0, microsecond=0)
    if local < reset:
        reset -= dt.timedelta(days=1)
    return (reset - off).replace(tzinfo=UTC)


def next_reset(now=None):
    return last_reset(now) + dt.timedelta(days=1)


def iso(t):
    return t.astimezone(UTC).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse(s):
    """GitHub timestamp -> aware datetime (None stays None)."""
    if not s:
        return None
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
