#!/usr/bin/env python3
"""One-time enrichment: read DB coords/elevation/tz for high-value cities by selecting
them on salatcalendar (settings cookie). Writes data/city_supplement.json — loaded by
CityTable so nearest-city lookups can resolve small cities the census didn't cover."""
import json
import os
import re
import time
import urllib.parse
import urllib.request
import http.cookiejar

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 'https://www.salatcalendar.com/index.php'
TARGETS = [
    ('Khor Fakkan', '50145', 'AE'),
    ('Al Ain', '50141', 'AE'),
    ('Fujairah', '50158', 'AE'),
    ('Sharjah', '50138', 'AE'),
    ('Ras Al Khaimah', '50132', 'AE'),
    ('Ajman', '50157', 'AE'),
    ('Abu Dhabi', '50139', 'AE'),
    ('Dubai', '50137', 'AE'),
]
out = []
last = [0.0]
for name, cid, iso2 in TARGETS:
    wait = 3.5 - (time.time() - last[0])
    if wait > 0:
        time.sleep(wait)
    last[0] = time.time()
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')]
    try:
        op.open(f'{BASE}/countries/select_city/{cid}', timeout=45).read()
    except Exception as e:
        print('FAIL', cid, e)
        continue
    settings = None
    for c in jar:
        if c.name == 'settings':
            settings = urllib.parse.unquote(c.value)
    st = json.loads(settings) if settings else {}
    rec = {'city_name': name, 'city_id': cid, 'iso2': iso2,
           'lat': float(st.get('Latitude', 0)), 'lon': float(st.get('Longitude', 0)),
           'elevation_m': float(st.get('Altitude', 0) or 0), 'timezone': st.get('TimeZone', '')}
    out.append(rec)
    print(rec)
with open(os.path.join(HERE, 'data', 'city_supplement.json'), 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f'wrote {len(out)} cities to data/city_supplement.json')