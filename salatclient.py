"""Rate-limited client for salatcalendar.com's named-city path (round-2/3 flow).

Reuses the exact flow proven in the census: countries/cities -> select_city
(settings cookie carries the city DB elevation/timezone) -> get_day x4 dates.
A single process-wide rate limiter keeps outbound traffic well under anything
automated-looking (min 3.5 s between requests).
"""
import csv
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
import http.cookiejar
import os

BASE = 'https://www.salatcalendar.com/index.php'
DATES = {"2026-03-20": "20/03/2026", "2026-06-21": "21/06/2026",
         "2026-09-22": "22/09/2026", "2026-12-21": "21/12/2026"}
MARKERS = ('إضاءة', 'فوق', 'تحت', '-----')
TD = re.compile(r'<td class="no-wrap text-center">([^<]+)</td>')

_rate_lock = threading.Lock()
_last_req = [0.0]
MIN_INTERVAL = 3.5


class RateLimited(Exception):
    pass


def _pace():
    with _rate_lock:
        wait = MIN_INTERVAL - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()


def _request(opener, url, data=None):
    _pace()
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None)
    return opener.open(req, timeout=45).read().decode('utf-8', 'replace')


def _haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


class CityTable:
    """Nearest-city lookup built from the round-3 census output (239 cities)."""

    def __init__(self, csv_path, supplement_path=None):
        self.cities = []
        seen = set()
        with open(csv_path, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                key = r['city_id']
                if key in seen:
                    continue
                seen.add(key)
                self.cities.append({
                    'city_id': r['city_id'], 'city_name': r['city_name'],
                    'country_id': r['country_id'], 'iso2': r['iso2'],
                    'lat': float(r['lat']), 'lon': float(r['lon']),
                    'elevation_m': float(r['elevation_m'] or 0),
                    'timezone': r['timezone'],
                })
        if supplement_path and os.path.exists(supplement_path):
            with open(supplement_path, encoding='utf-8') as f:
                for r in json.load(f):
                    key = r['city_id']
                    if key in seen:
                        continue
                    seen.add(key)
                    self.cities.append({
                        'city_id': r['city_id'], 'city_name': r['city_name'],
                        'country_id': '', 'iso2': r.get('iso2', ''),
                        'lat': float(r['lat']), 'lon': float(r['lon']),
                        'elevation_m': float(r.get('elevation_m', 0) or 0),
                        'timezone': r.get('timezone', ''),
                    })
        print(f"CityTable: {len(self.cities)} cities loaded")

    def nearest(self, lat, lng):
        best, best_d = None, 1e18
        for c in self.cities:
            d = _haversine_km(lat, lng, c['lat'], c['lon'])
            if d < best_d:
                best, best_d = c, d
        return best, best_d


def _tz_offset_hours(tz_id, when=None):
    """IANA tz -> hours for a given date (DST-aware). when = datetime.date or None (2026-09-20)."""
    try:
        from zoneinfo import ZoneInfo
        import datetime as dt
        d = when or dt.date(2026, 9, 20)
        off = dt.datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo(tz_id)).utcoffset()
        return off.total_seconds() / 3600.0
    except Exception:
        FIX = {'Asia/Dubai': 4, 'Europe/London': 1, 'America/New_York': -4, 'Asia/Karachi': 5}
        return FIX.get(tz_id, 0)


class SalatClient:
    def __init__(self, city_csv='round3_times.csv', supplement_path=None):
        self.table = CityTable(city_csv, supplement_path)
        self.last_error = None

    def open_city_session(self, city_id):
        """Select a named city once; returns an opener to reuse for many get_day calls."""
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.addheaders = [('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')]
        try:
            _request(opener, f'{BASE}/countries/select_city/{city_id}')
        except Exception as e:
            self.last_error = f'select_city {city_id}: {e!r}'
            return None
        return opener

    def fetch_named_date(self, opener, city_id, ddmmyyyy):
        """One day's 6 times from an already-open named-city session. Returns
        {'date': 'YYYY-MM-DD', 'fajr': 'HH:MM', ...} or None. Site markers -> None fields."""
        try:
            h = _request(opener, f'{BASE}/app/get_day', {'day': ddmmyyyy})
            cells = [c.strip() for c in TD.findall(json.loads(h).get('html', ''))]
        except Exception as e:
            self.last_error = f'get_day {ddmmyyyy}: {e!r}'
            return None
        if len(cells) < 8 or cells[0] != ddmmyyyy:
            return None
        row = {'fajr': cells[2], 'sunrise': cells[3], 'dhuhr': cells[4],
               'asr': cells[5], 'maghrib': cells[6], 'isha': cells[7]}
        for k in row:
            if row[k] in MARKERS:
                row[k] = None
        d, m, y = map(int, ddmmyyyy.split('/'))
        row['date'] = f'{y:04d}-{m:02d}-{d:02d}'
        return row

    def fetch_cluster_times(self, lat, lng):
        """Named-city flow for the nearest census city. Returns
        dict(city=..., lat, lon, elevation_m, tz_hours, tz_id, times={iso: {...}})
        or raises on failure."""
        city, dist = self.table.nearest(lat, lng)
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.addheaders = [('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')]
        try:
            _request(opener, f'{BASE}/countries/select_city/{city["city_id"]}')
        except Exception as e:
            self.last_error = f'select_city {city["city_id"]}: {e!r}'
            raise
        settings = None
        for c in jar:
            if c.name == 'settings':
                settings = urllib.parse.unquote(c.value)
        tz_id, elev = city['timezone'], city['elevation_m']
        try:
            st = json.loads(settings) if settings else {}
            tz_id = st.get('TimeZone', tz_id)
            elev = float(st.get('Altitude') or elev)
        except Exception:
            pass
        times = {}
        for iso, ddmmyy in DATES.items():
            try:
                h = _request(opener, f'{BASE}/app/get_day', {'day': ddmmyy})
                cells = [c.strip() for c in TD.findall(json.loads(h).get('html', ''))]
            except Exception as e:
                self.last_error = f'get_day {ddmmyy}: {e!r}'
                continue
            if len(cells) < 8:
                continue
            row = {'fajr': cells[2], 'sunrise': cells[3], 'dhuhr': cells[4],
                   'asr': cells[5], 'maghrib': cells[6], 'isha': cells[7]}
            for k in row:
                if row[k] in MARKERS:
                    row[k] = None
            times[iso] = row
        if not times:
            raise RuntimeError('no usable get_day rows')
        import datetime as _dt
        tz_by_date = {}
        for iso in times:
            y, m, d = map(int, iso.split('-'))
            tz_by_date[iso] = _tz_offset_hours(tz_id, _dt.date(y, m, d))
        return {
            'city': city['city_name'], 'city_id': city['city_id'],
            'country_id': city['country_id'], 'iso2': city['iso2'],
            'city_lat': city['lat'], 'city_lon': city['lon'],
            'distance_km': round(dist, 1),
            'elevation_m': elev, 'tz_id': tz_id,
            'tz_hours': _tz_offset_hours(tz_id),
            'tz_by_date': tz_by_date,
            'times': times,
        }