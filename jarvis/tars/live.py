"""Live facts Jarvis can answer without a web search: sunrise/sunset (computed) and the weather (National Weather Service).

facts(text) returns a short LIVE block for the model's context, or '' when the question isn't about these. The NWS API
is free and keyless (it only wants a User-Agent). Home is Plymouth, MA unless JARVIS_LAT / JARVIS_LON say otherwise.
Standard library only.
"""
import datetime as dt
import json
import math
import os
import re
import time
import urllib.request

LAT = float(os.environ.get('JARVIS_LAT', '41.9584'))
LON = float(os.environ.get('JARVIS_LON', '-70.6673'))
PLACE = os.environ.get('JARVIS_PLACE', 'Plymouth, MA')
UA = 'jarvis-tars (github.com/flanneryjake)'

SUN_RE = re.compile(r'\b(sun\s*(?:rise|set)|dawn|dusk|daylight|get(?:s|ting)?\s+dark|get(?:s|ting)?\s+light)\b', re.I)
WEATHER_RE = re.compile(
    r"\b(weather|forecast|temp(?:erature)?|degrees|rain\w*|snow\w*|storm\w*|wind\w*|humid\w*|cold|hot|warm|chilly|"
    r"freez\w*|jacket|coat|umbrella|sunny|cloudy|outside)\b", re.I)

_cache = {}


# ----------------------------------------------------------------------------- sun (NOAA solar calculator)

def sun_times(day=None, lat=LAT, lon=LON, zenith=90.833):
    """(sunrise, sunset) as local-time datetimes for `day` (a date; default today). None when the sun doesn't cross.
    NOAA solar calculator equations (good to about a minute)."""
    day = day or dt.date.today()
    noon = dt.datetime(day.year, day.month, day.day, 12, tzinfo=dt.timezone.utc) - dt.timedelta(hours=lon / 15)
    jc = ((noon - dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)).total_seconds() / 86400) / 36525
    l0 = (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360
    m = 357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    e = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)
    c = (math.sin(math.radians(m)) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
         + math.sin(math.radians(2 * m)) * (0.019993 - 0.000101 * jc) + math.sin(math.radians(3 * m)) * 0.000289)
    app = l0 + c - 0.00569 - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * jc))
    eps0 = 23 + (26 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60) / 60
    eps = eps0 + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * jc))
    dec = math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(app)))
    y = math.tan(math.radians(eps / 2)) ** 2
    l0r, mr = math.radians(l0), math.radians(m)
    eqt = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(mr) + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
                           - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    cos_h = (math.cos(math.radians(zenith)) / (math.cos(math.radians(lat)) * math.cos(dec))
             - math.tan(math.radians(lat)) * math.tan(dec))
    if not -1 <= cos_h <= 1:
        return None
    ha = math.degrees(math.acos(cos_h))
    solar_noon = 720 - 4 * lon - eqt            # minutes after 00:00 UTC
    midnight = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
    return tuple((midnight + dt.timedelta(minutes=solar_noon + sign * 4 * ha)).astimezone() for sign in (-1, 1))


def _clock(t):
    return t.strftime('%I:%M %p').lstrip('0')


def sun_fact(text, now=None):
    now = now or dt.datetime.now().astimezone()
    day = now.date() + dt.timedelta(days=1 if re.search(r'\btomorrow\b', text, re.I) else 0)
    times = sun_times(day)
    if not times:
        return ''
    which = 'tomorrow' if day != now.date() else 'today'
    out = f'Sun in {PLACE} {which} ({day:%a %b %d}): sunrise {_clock(times[0])}, sunset {_clock(times[1])} (computed).'
    civil = sun_times(day, zenith=96)
    if civil:
        out += f' Dark (end of twilight) at {_clock(civil[1])}.'
    if which == 'today':
        asks_rise = re.search(r'rise|dawn|light', text, re.I)
        name, t = ('sunrise', times[0]) if asks_rise else ('sunset', times[1])
        mins = round((t - now).total_seconds() / 60)
        out += f' {name.title()} is {_span(mins)} from now.' if mins >= 0 else f' {name.title()} was {_span(-mins)} ago.'
    return out


def _span(mins):
    if mins < 60:
        return f'{mins} minute' + ('' if mins == 1 else 's')
    h, m = divmod(mins, 60)
    return f'{h} hour' + ('' if h == 1 else 's') + (f' {m} minutes' if m else '')


# ----------------------------------------------------------------------------- weather (api.weather.gov)

def _get(url, timeout=10):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/geo+json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def weather_fact(text):
    point = _cached('point', 86400 * 7, lambda: _get(f'https://api.weather.gov/points/{LAT:.4f},{LON:.4f}'))
    props = point['properties']
    periods = _cached('forecast', 1800, lambda: _get(props['forecast']))['properties']['periods']
    want = 4 if re.search(r'\b(week|weekend|days|saturday|sunday|monday|tuesday|wednesday|thursday|friday)\b',
                          text, re.I) else 2
    if re.search(r'\b(week|days)\b', text, re.I):
        want = 8
    lines = [f'{p["name"]}: {p["detailedForecast"]}' if i < 2 else
             f'{p["name"]}: {p["shortForecast"]}, {p["temperature"]}°{p["temperatureUnit"]}'
             for i, p in enumerate(periods[:want])]
    try:   # current conditions from the nearest station; optional
        stations = _cached('stations', 86400 * 7, lambda: _get(props['observationStations']))['features']
        obs = _cached('obs', 600, lambda: _get(stations[0]['id'] + '/observations/latest'))['properties']
        c = obs.get('temperature', {}).get('value')
        if c is not None:
            lines.insert(0, f'Now: {round(c * 9 / 5 + 32)}°F, {obs.get("textDescription") or "conditions n/a"}.')
    except Exception:  # noqa: BLE001
        pass
    return f'Weather for {PLACE} (National Weather Service):\n' + '\n'.join(lines)


def facts(text, log=print):
    out = []
    if SUN_RE.search(text or ''):
        out.append(sun_fact(text))
    if WEATHER_RE.search(text or '') and not re.search(r'\b(printer|bed|nozzle|cpu|gpu|pc|laptop|rig)\b', text, re.I):
        try:
            out.append(weather_fact(text))
        except Exception as e:  # noqa: BLE001
            log(f'weather failed: {type(e).__name__}: {str(e)[:160]}')
    return '\n'.join(o for o in out if o)
