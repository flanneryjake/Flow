import datetime as dt
import unittest
from unittest import mock

import live
import lookup


class SunTest(unittest.TestCase):
    def test_boston_equinox_matches_almanac(self):
        # Almanac: Boston, Sep 22 2025, sunrise 6:32 AM, sunset 6:43 PM EDT.
        rise, sset = live.sun_times(dt.date(2025, 9, 22), 42.3601, -71.0589)
        eastern = dt.timezone(dt.timedelta(hours=-4))
        self.assertLessEqual(abs((rise.astimezone(eastern).replace(tzinfo=None)
                                  - dt.datetime(2025, 9, 22, 6, 32)).total_seconds()), 180)
        self.assertLessEqual(abs((sset.astimezone(eastern).replace(tzinfo=None)
                                  - dt.datetime(2025, 9, 22, 18, 43)).total_seconds()), 180)

    def test_sunset_stays_on_the_asked_day(self):
        rise, sset = live.sun_times(dt.date(2026, 6, 21))
        self.assertLess(rise, sset)

    def test_facts_routes_sun_and_skips_small_talk(self):
        self.assertIn('sunset', live.facts('hey jarvis when is sunset tonight'))
        with mock.patch.object(live, 'weather_fact', side_effect=AssertionError('no weather')):
            self.assertEqual(live.facts('thanks jarvis'), '')
            self.assertEqual(live.facts('is the printer bed hot'), '')


class WeatherTest(unittest.TestCase):
    def test_weather_from_nws(self):
        pages = {
            'points': {'properties': {'forecast': 'F', 'observationStations': 'S'}},
            'F': {'properties': {'periods': [
                {'name': 'Tonight', 'detailedForecast': 'Clear, low around 48.', 'shortForecast': 'Clear',
                 'temperature': 48, 'temperatureUnit': 'F'},
                {'name': 'Saturday', 'detailedForecast': 'Sunny, high near 66.', 'shortForecast': 'Sunny',
                 'temperature': 66, 'temperatureUnit': 'F'}]}},
            'S': {'features': [{'id': 'KPYM'}]},
            'KPYM/observations/latest': {'properties': {'temperature': {'value': 12.0}, 'textDescription': 'Clear'}},
        }
        live._cache.clear()
        with mock.patch.object(live, '_get', side_effect=lambda u, **k: pages['points' if 'points' in u else u]):
            out = live.facts('do I need a jacket tonight')
        self.assertIn('Now: 54°F, Clear.', out)
        self.assertIn('Tonight: Clear, low around 48.', out)


class SpokenTest(unittest.TestCase):
    def test_trim_and_old_home(self):
        self.assertEqual(lookup.trim('One. Two. Three. Four.'), 'One. Two. Three.')
        self.assertEqual(lookup.scrub("I'm idle here in the South End. Canberra, sir."), 'Canberra, sir.')

    def test_excerpt_finds_the_answer_sentence(self):
        page = ('Welcome to our site. Cookie settings apply here. Sunset in Plymouth today is at 6:22 PM and '
                'sunrise is at 6:41 AM. Subscribe for more news.')
        self.assertIn('6:22 PM', lookup.excerpt(page, 'sunset Plymouth today'))


if __name__ == '__main__':
    unittest.main()
