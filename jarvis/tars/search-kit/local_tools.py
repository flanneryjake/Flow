"""Quick local lookups for Jarvis/TARS (card #924): no model, no key, answered before the first model call.

  time / date      -> already in the chat context ("Now: ...")
  sunrise / sunset -> computed here (NOAA formula), no network
  weather          -> Open-Meteo (free, no key, no account), 5 s timeout

facts_for(text) returns a short FACTS string for the context, or '' when nothing applies. Home is Plymouth, MA
unless JARVIS_LAT / JARVIS_LON / JARVIS_PLACE are set. Standard library only.
"""
import datetime as dt
import json
import math
import os
import re
import urllib.parse
import urllib.request

LAT = float(os.environ.get('JARVIS_LAT', '41.9584'))
LON = float(os.environ.get('JARVIS_LON', '-70.6673'))
PLACE = os.environ.get('JARVIS_PLACE', 'Plymouth, MA')
TIMEOUT = 5

SUN_RE = re.compile(r'\b(sunset|sunrise|sun\s*(?:set|rise|up|down)|dusk|dawn|daylight|get(?:s)?\s+dark)\b', re.I)
WEATHER_RE = re.compile(r'\b(weather|temperature|temp|forecast|rain(?:ing)?|snow(?:ing)?|cold|hot|warm|windy|wind|'
                        r'umbrella|jacket|outside)\b', re.I)
OTHER_PLACE_RE = re.compile(r'\b(?:in|at|for)\s+(?!(?i:plymouth|here|home|the\b|my\b))([A-Z][a-zA-Z]+(?:[ ,]+[A-Z][a-zA-Z]+)*)')

WMO = {0: 'clear', 1: 'mostly clear', 2: 'partly cloudy', 3: 'overcast', 45: 'fog', 48: 'freezing fog',
       51: 'light drizzle', 53: 'drizzle', 55: 'heavy drizzle', 61: 'light rain', 63: 'rain', 65: 'heavy rain',
       66: 'freezing rain', 67: 'heavy freezing rain', 71: 'light snow', 73: 'snow', 75: 'heavy snow',
       77: 'snow grains', 80: 'rain showers', 81: 'rain showers', 82: 'violent rain showers',
       85: 'snow showers', 86: 'heavy snow showers', 95: 'thunderstorms', 96: 'thunderstorms with hail',
       99: 'thunderstorms with hail'}


def _sun_utc(day, lat, lon, rising):
    """NOAA sunrise/sunset (zenith 90.833) as a UTC datetime, or None at polar day/night."""
    n = day.timetuple().tm_yday
    lng_hour = lon / 15
    t = n + ((6 if rising else 18) - lng_hour) / 24
    m = 0.9856 * t - 3.289
    L = (m + 1.916 * math.sin(math.radians(m)) + 0.020 * math.sin(math.radians(2 * m)) + 282.634) % 360
    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(L)))) % 360
    ra = (ra + (math.floor(L / 90) * 90 - math.floor(ra / 90) * 90)) / 15
    sin_dec = 0.39782 * math.sin(math.radians(L))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(math.radians(90.833)) - sin_dec * math.sin(math.radians(lat))) / (cos_dec * math.cos(math.radians(lat)))
    if not -1 <= cos_h <= 1:
        return None
    h = (360 - math.degrees(math.acos(cos_h))) if rising else math.degrees(math.acos(cos_h))
    ut = (h / 15 + ra - 0.06571 * t - 6.622 - lng_hour) % 24
    return dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc) + dt.timedelta(hours=ut)


def sun_times(day=None, lat=LAT, lon=LON):
    """(sunrise, sunset) as local-time datetimes for `day` (default today)."""
    day = day or dt.date.today()
    return tuple((_sun_utc(day, lat, lon, r) or dt.datetime.now(dt.timezone.utc)).astimezone() for r in (True, False))


def _clock(t):
    return t.strftime('%I:%M %p').lstrip('0')


def sun_facts(text):
    day = dt.date.today() + dt.timedelta(days=1 if re.search(r'\btomorrow\b', text, re.I) else 0)
    rise, sset = sun_times(day)
    which = 'tomorrow' if day != dt.date.today() else 'today'
    return (f'Sun in {PLACE} {which} ({day:%a %m/%d}), computed locally: sunrise {_clock(rise)}, sunset {_clock(sset)}, '
            f'daylight {int((sset - rise).seconds // 3600)} h {int((sset - rise).seconds % 3600 // 60)} min.')


def weather_facts(timeout=TIMEOUT):
    q = urllib.parse.urlencode({
        'latitude': LAT, 'longitude': LON, 'timezone': 'auto', 'forecast_days': 2,
        'temperature_unit': 'fahrenheit', 'wind_speed_unit': 'mph', 'precipitation_unit': 'inch',
        'current': 'temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation',
        'daily': 'temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code'})
    req = urllib.request.Request('https://api.open-meteo.com/v1/forecast?' + q, headers={'User-Agent': 'jarvis-tars'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode('utf-8'))
    c, dl = d['current'], d['daily']
    days = []
    for i, label in enumerate(('Today', 'Tomorrow')):
        days.append(f'{label}: {WMO.get(dl["weather_code"][i], "mixed")}, high {round(dl["temperature_2m_max"][i])}F, '
                    f'low {round(dl["temperature_2m_min"][i])}F, rain chance {dl["precipitation_probability_max"][i]}%.')
    return (f'Weather in {PLACE} now (Open-Meteo): {WMO.get(c["weather_code"], "mixed")}, {round(c["temperature_2m"])}F, '
            f'feels like {round(c["apparent_temperature"])}F, wind {round(c["wind_speed_10m"])} mph. ' + ' '.join(days))


def facts_for(text, log=print):
    """FACTS lines for the chat context, '' when no local tool applies (or the question is about another place)."""
    text = text or ''
    if OTHER_PLACE_RE.search(text):
        return ''          # "sunset in Paris": leave it to the web search lane
    out = []
    if SUN_RE.search(text):
        out.append(sun_facts(text))
    if WEATHER_RE.search(text) and not re.search(r'\b(card|task|status|server|machine|rig|laptop)\b', text, re.I):
        try:
            out.append(weather_facts())
        except Exception as e:  # noqa: BLE001
            log(f'local weather failed: {type(e).__name__}: {str(e)[:120]}')
    return '\n'.join(out)


if __name__ == '__main__':
    import sys
    print(facts_for(' '.join(sys.argv[1:]) or 'sunset and weather') or '(no local tool for that)')
