#!/usr/bin/env python3
"""Prayer Times host: static PWA + /api/params + /api/flag + Tier-2 verification worker.

Tier 1 (static): country-level overrides resolved from the embedded boundaries dataset
                  (same logic the client uses offline).
Tier 2 (grows):   city/area overrides keyed by ~0.01-deg rounded cluster, produced by the
                  verification worker from salatcalendar.com named-city data, sanity-bounded.

Run:  python server.py [port]   (default 8787)   — working dir = this file's dir.
"""
import csv
import datetime
import io
import json
import math
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(HERE, 'app')
DATA_DIR = os.path.join(HERE, 'data')
TIER2_PATH = os.path.join(DATA_DIR, 'tier2.json')
QUEUE_PATH = os.path.join(DATA_DIR, 'verify_queue.json')
SEEN_PATH = os.path.join(DATA_DIR, 'seen_clusters.json')

def _exact_budget_meta():
    try:
        import exact
        return exact.budget_meta()
    except Exception:
        return {}


EXACT_PATH_MARKED = os.path.join(DATA_DIR, 'exact_marked.json')
CONFIG_PATH = os.path.join(DATA_DIR, 'config.json')
BUDGET_PATH = os.path.join(DATA_DIR, 'budget.json')
FLAG_LOG = os.path.join(DATA_DIR, 'flags.log')
REJECT_LOG = os.path.join(DATA_DIR, 'tier2_rejected.log')

_TZF = None
_TZ_CACHE = {}
def resolve_tz(lat, lng):
    global _TZF
    key = f'{round(lat, 2)},{round(lng, 2)}'
    if key in _TZ_CACHE:
        return _TZ_CACHE[key]
    if _TZF is None:
        try:
            from timezonefinder import TimezoneFinder
            _TZF = TimezoneFinder()
        except Exception:
            _TZF = False
    name = None
    if _TZF:
        try:
            name = _TZF.timezone_at(lat=lat, lng=lng)
        except Exception:
            name = None
    _TZ_CACHE[key] = name
    return name
CLUSTER_KM = 1.0           # lookup radius for tier-2 coverage (cluster grain ~1 km; 10 km served neighbours' params as verified)
RAFT_TIME = 3.5            # salatcalendar min request interval (salatclient enforces)

_lock = threading.Lock()


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _polygons(feat):
    return [feat['coords']] if feat['type'] == 'Polygon' else feat['coords']


BOUNDARIES = load_json(os.path.join(DATA_DIR, 'boundaries.json'), [])
OVERRIDES = load_json(os.path.join(DATA_DIR, 'country_overrides.json'), {})
for feat in BOUNDARIES:
    lons, lats = [], []
    for poly in _polygons(feat):
        for ring in poly:
            for pt in ring:
                lons.append(pt[0])
                lats.append(pt[1])
    feat['_bbox'] = (min(lons), min(lats), max(lons), max(lats))
print(f"Tier1 loaded: {len(BOUNDARIES)} boundary features, {len(OVERRIDES)} countries w/ overrides")


def point_in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _polygons(feat):
    return [feat['coords']] if feat['type'] == 'Polygon' else feat['coords']


def point_in_feature(x, y, feat):
    for poly in _polygons(feat):
        for ring in poly:
            if point_in_ring(x, y, ring):
                return True
    return False


def seg_dist_m(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def nearest_feature_dist(x, y, feat):
    """meters from point to the nearest ring edge of the feature (deg->m approx at lat y)."""
    deg_m = 111320.0 * math.cos(math.radians(y))
    lat_m = 111320.0
    best = 1e18
    for poly in _polygons(feat):
        for ring in poly:
            j = len(ring) - 1
            for i in range(len(ring)):
                ax, ay = ring[j][0], ring[j][1]
                bx, by = ring[i][0], ring[i][1]
                d = seg_dist_m(x * deg_m, y * lat_m, ax * deg_m, ay * lat_m, bx * deg_m, by * lat_m)
                best = min(best, d)
                j = i
    return best


def resolve_country(lat, lng):
    x, y = lng, lat
    for feat in BOUNDARIES:
        (x0, y0, x1, y1) = feat['_bbox']
        if x0 - 1e-9 <= x <= x1 + 1e-9 and y0 - 1e-9 <= y <= y1 + 1e-9:
            if point_in_feature(x, y, feat):
                return feat['code']
    best_feat, best_d = None, 1e18
    for feat in BOUNDARIES:
        d = nearest_feature_dist(x, y, feat)
        if d < best_d:
            best_d, best_feat = d, feat
    return best_feat['code'] if best_feat else None


def cluster_key(lat, lng):
    return f"{round(lat, 2):.2f},{round(lng, 2):.2f}"


def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def tier2_get(lat, lng):
    """Best tier-2 entry within CLUSTER_KM (params + meta), or None."""
    data = load_json(TIER2_PATH, {})
    best, best_d = None, 1e18
    for k, v in data.items():
        try:
            clat, clng = map(float, k.split(','))
        except Exception:
            continue
        d = haversine_km(lat, lng, clat, clng)
        if d <= CLUSTER_KM and d < best_d:
            best_d, best = d, dict(v, cluster=k)
    return best


def _iso_from_stored(v):
    """'YYYY-MM-DD HH:MM:SS <tz label>' -> ISO 8601 with the machine's offset (fallback: raw)."""
    if not v:
        return None
    try:
        import re as _re
        m = _re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', v)
        if not m:
            return v
        dt = datetime.datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
        return dt.astimezone().isoformat(timespec='seconds')
    except Exception:
        return v


def params_for(lat, lng):
    """Tier 2 first, then Tier 1 (client's own boundary logic ported here)."""
    t2 = tier2_get(lat, lng)
    code = resolve_country(lat, lng)
    t1 = dict(OVERRIDES.get(code) or {})
    if t2:
        params = dict(t1)
        params.update(t2.get('params', {}))
        return {'tier': 'tier2', 'source': 'tier2', 'country': code, 'cluster': t2['cluster'],
                'params': params, 'verified_at': t2.get('verified_at'),
                'verifiedAt': _iso_from_stored(t2.get('verified_at')),
                'site_city': t2.get('site_city'), 'flags': t2.get('flags', 0)}
    return {'tier': 'tier1', 'source': 'tier1', 'country': code, 'cluster': cluster_key(lat, lng),
            'params': t1}


# ---------------- verification worker ----------------
def verify_cluster(clat, clng, reason):
    from salatclient import SalatClient
    from solver import solve_params
    client = SalatClient(os.path.join(HERE, 'round3_times.csv'),
                              os.path.join(HERE, 'data', 'city_supplement.json'))
    info = client.fetch_cluster_times(clat, clng)
    params, residuals = solve_params(info['city_lat'], info['city_lon'],
                                         info['elevation_m'], info['tz_hours'], info['times'],
                                         info.get('tz_by_date'))
    entry = {
        'params': params, 'residuals': residuals, 'verified_at': time.strftime('%Y-%m-%d %H:%M:%S %Z'),
        'site_city': f"{info['city']} ({info['iso2']})", 'site_city_id': info['city_id'],
        'site_lat': info['city_lat'], 'site_lon': info['city_lon'],
        'distance_from_cluster_km': info['distance_km'],
        'elevation_m': info['elevation_m'], 'tz_id': info['tz_id'],
        'reason': reason,
    }
    return entry


def worker_loop():
    while True:
        try:
            q = load_json(QUEUE_PATH, [])
            changed = False
            for item in list(q):
                limit, bst = budget_state()
                if int(bst.get('used', 0)) >= limit:
                    print(f"[worker] daily budget exhausted ({bst.get('used', 0)}/{limit}) "
                          f"- {len(q) - q.index(item)} job(s) waiting for next day", flush=True)
                    break   # FIFO: everything else waits; nothing is dropped
                budget_consume()
                key = item['cluster']
                try:
                    clat, clng = map(float, key.split(','))
                except Exception:
                    q.remove(item)
                    changed = True
                    continue
                flags = item.get('flags', 1)
                existing = load_json(TIER2_PATH, {}).get(key)
                entry = verify_cluster(clat, clng, item.get('reason', 'flag'))
                if entry['params'] is None:
                    with open(REJECT_LOG, 'a', encoding='utf-8') as f:
                        f.write(json.dumps({'cluster': key, 'residuals': entry['residuals'],
                                            'site_city': entry['site_city']}) + '\n')
                    print(f"[worker] REJECTED {key}: out of sanity bounds", flush=True)
                else:
                    old = existing.get('flags', 0) if existing else 0
                    entry['flags'] = old + flags
                    t2 = load_json(TIER2_PATH, {})
                    t2[key] = entry
                    save_json(TIER2_PATH, t2)
                    print(f"[worker] verified {key}: {json.dumps(entry['params'], ensure_ascii=False)} "
                          f"(city {entry['site_city']})", flush=True)
                q.remove(item)
                changed = True
            if changed:
                save_json(QUEUE_PATH, q)
        except Exception as e:
            print(f"[worker] loop error: {e!r}", flush=True)
        time.sleep(4)


def _enqueue_locked(cluster, reason):
    q = load_json(QUEUE_PATH, [])
    for it in q:
        if it['cluster'] == cluster:
            it['flags'] = it.get('flags', 1) + 1
            return False
    q.append({'cluster': cluster, 'reason': reason, 'flags': 1})
    save_json(QUEUE_PATH, q)
    return True


def auto_enqueue_if_new(lat, lng):
    """First Tier-1 visit for this cluster -> mark seen + enqueue exactly once.
    Lock covers check-and-mark-and-enqueue, so concurrent hits can't double-enqueue."""
    key = cluster_key(lat, lng)
    with _lock:
        seen = load_json(SEEN_PATH, {})
        if key in seen:
            return False
        seen[key] = time.time()
        save_json(SEEN_PATH, seen)
        _enqueue_locked(key, 'auto-queue (first visit)')
        return True


def get_config():
    return load_json(CONFIG_PATH, {})

import secrets as _secrets

def exact_token():
    cfg = get_config()
    tok = cfg.get('exact_api_token')
    if not tok:
        cfg = dict(cfg)
        cfg['exact_api_token'] = _secrets.token_hex(16)
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=1)
        tok = cfg['exact_api_token']
    return tok

import collections as _collections
_RLC = _collections.defaultdict(_collections.deque)

def rate_limited(ip, key, limit, window=3600):
    now = time.time()
    dq = _RLC[key + '|' + ip]
    while dq and now - dq[0] > window:
        dq.popleft()
    if len(dq) >= limit:
        return True
    dq.append(now)
    return False

def _client_ip(self):
    xff = self.headers.get('X-Forwarded-For')
    if xff:
        return xff.split(',')[0].strip()
    return self.client_address[0]



def budget_state():
    """(daily_limit, state_dict) with automatic date rollover."""
    limit = int(get_config().get('daily_verify_budget', 200))
    st = load_json(BUDGET_PATH, {})
    today = time.strftime('%Y-%m-%d')
    if st.get('date') != today:
        st = {'date': today, 'used': 0}
    return limit, st


def budget_consume():
    limit, st = budget_state()
    st['used'] = int(st.get('used', 0)) + 1
    save_json(BUDGET_PATH, st)


def enqueue_flag(lat, lng, prayer, user_time):
    key = cluster_key(lat, lng)
    with _lock:
        enqueued = _enqueue_locked(key, f'{prayer} @ {user_time}')
        return enqueued, ('queued' if enqueued else 'already queued (flag count incremented)')
    with open(FLAG_LOG, 'a', encoding='utf-8') as f:
        f.write(json.dumps({'lat': lat, 'lng': lng, 'prayer': prayer,
                            'userStatedTime': user_time, 'ts': time.time(), 'cluster': key}) + '\n')
    return True, 'queued'


# ---------------- HTTP ----------------
MIME = {'.html': 'text/html; charset=utf-8', '.json': 'application/json',
        '.png': 'image/png', '.js': 'text/javascript; charset=utf-8',
        '.webmanifest': 'application/manifest+json', '.svg': 'image/svg+xml',
        '.css': 'text/css; charset=utf-8', '.ico': 'image/x-icon', '.txt': 'text/plain'}


class Handler(BaseHTTPRequestHandler):
    server_version = 'PrayerTimes/1.0'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype='application/json', extra=None):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == '/api/params':
            q = parse_qs(u.query)
            try:
                lat, lng = float(q['lat'][0]), float(q['lng'][0])
            except Exception:
                return self._send(400, json.dumps({'error': 'lat/lng required'}))
            dreq = (q.get('date') or [''])[0] or None
            if dreq:
                import exact
                row = exact.exact_row(lat, lng, dreq)
                if row:
                    return self._send(200, json.dumps({
                        'source': 'exact', 'date': dreq,
                        'times': {k: row[k] for k in ('fajr', 'sunrise', 'dhuhr', 'asr', 'maghrib', 'isha')},
                        'fetchedAt': row.get('fetchedAt'),
                        'cluster': exact.cluster_key(lat, lng),
                        'country': resolve_country(lat, lng),
                        'timezone': resolve_tz(lat, lng),
                    }, ensure_ascii=False))
            res = params_for(lat, lng)
            res['timezone'] = resolve_tz(lat, lng)
            if res.get('source') == 'tier1':
                res['verificationQueued'] = auto_enqueue_if_new(lat, lng)
            else:
                res['verificationQueued'] = False
            return self._send(200, json.dumps(res, ensure_ascii=False))
        if u.path == '/api/status':
            t2 = load_json(TIER2_PATH, {})
            q = load_json(QUEUE_PATH, [])
            limit, bst = budget_state()
            used = int(bst.get('used', 0))
            return self._send(200, json.dumps({
                'tier2_count': len(t2), 'queue_depth': len(q),
                'tier2_clusters': sorted(t2.keys()),
                'tier1_overrides_count': len(OVERRIDES),
                'seen_clusters': len(load_json(SEEN_PATH, {})),
                'budget': {'daily_limit': limit, 'used_today': used,
                           'remaining': max(0, limit - used)},
                'exact': {'marked': len(load_json(EXACT_PATH_MARKED, {})),
                          'budget': _exact_budget_meta()},
            }, ensure_ascii=False))
        if u.path == '/api/health':
            return self._send(200, json.dumps({'ok': True, 't': time.time()}))
        if u.path == '/api/push/vapid':
            import push
            pub = push.vapid_public_b64()
            if pub is None:
                return self._send(500, json.dumps({'error': 'push backend unavailable'}))
            return self._send(200, json.dumps({'publicKey': pub}))
        if u.path == '/api/exact/status':
            import exact
            return self._send(200, json.dumps(exact.marked_list(), ensure_ascii=False))
        if u.path == '/api/push/status':
            import push
            subs = push._load(push.SUBS_PATH, {})
            return self._send(200, json.dumps({'subscriptions': len(subs)}))
        return self._serve_static(u.path)

    def _serve_static(self, path):
        if path in ('/', ''):
            path = '/index.html'
        if path in ('/tv', '/tv/'):
            path = '/tv/index.html'
        full = os.path.normpath(os.path.join(APP_DIR, path.lstrip('/')))
        if not full.startswith(os.path.normpath(APP_DIR)) or not os.path.isfile(full):
            return self._send(404, json.dumps({'error': 'not found'}))
        ext = os.path.splitext(full)[1]
        with open(full, 'rb') as f:
            data = f.read()
        if path in ('/', '') or path.endswith('/index.html') or path == '/index.html':
            data = data.replace(b'__EXACT_TOKEN__', exact_token().encode())
        self.send_response(200)
        self.send_header('Content-Type', MIME.get(ext, 'application/octet-stream'))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == '/api/push/subscribe':
            if rate_limited(_client_ip(self), 'pushsub', 60):
                return self._send(429, json.dumps({'error': 'rate limited'}))
            try:
                n = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                return self._send(400, json.dumps({'error': 'bad json'}))
            import push
            ok, msg = push.subscribe(body.get('subscription', {}), body.get('lat'),
                                     body.get('lng'), body.get('elevationM', 0),
                                     body.get('tzId', ''))
            return self._send(200 if ok else 400, json.dumps({'ok': ok, 'msg': msg}))
        if u.path == '/api/push/unsubscribe':
            try:
                n = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                body = {}
            import push
            ok, msg = push.unsubscribe(body.get('endpoint', ''))
            return self._send(200 if ok else 404, json.dumps({'ok': ok, 'msg': msg}))
        if u.path == '/api/exact/request':
            try:
                n = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                return self._send(400, json.dumps({'error': 'bad json'}))
            got = (body.get('token') or self.headers.get('X-Exact-Token') or '')
            if got != exact_token():
                return self._send(401, json.dumps({'error': 'unauthorized'}))
            if body.get('lat') is None or body.get('lng') is None:
                return self._send(400, json.dumps({'error': 'lat/lng required'}))
            import exact
            ok, msg = exact.request_mark(float(body['lat']), float(body['lng']),
                                         body.get('replace'))
            return self._send(200 if ok else 409, json.dumps({'ok': ok, 'msg': msg}))
        if u.path == '/api/flag':
            if rate_limited(_client_ip(self), 'flag', 30):
                return self._send(429, json.dumps({'error': 'rate limited'}))
            try:
                n = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                return self._send(400, json.dumps({'error': 'bad json'}))
            lat, lng = body.get('lat'), body.get('lng')
            if lat is None or lng is None:
                return self._send(400, json.dumps({'error': 'lat/lng required'}))
            ok, msg = enqueue_flag(float(lat), float(lng), body.get('prayer', ''),
                                   body.get('userStatedTime', ''))
            return self._send(200, json.dumps({'ok': ok, 'msg': msg}))
        return self._send(404, json.dumps({'error': 'not found'}))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    os.makedirs(DATA_DIR, exist_ok=True)
    threading.Thread(target=worker_loop, daemon=True).start()
    try:
        import push
        threading.Thread(target=push.push_loop, daemon=True).start()
    except Exception as e:
        print(f'[push] scheduler not started: {e!r}', flush=True)
    try:
        import exact  # noqa
        threading.Thread(target=exact.exact_worker, daemon=True).start()
        print('[exact] worker started', flush=True)
    except Exception as e:
        print(f'[exact] worker not started: {e!r}', flush=True)
    srv = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f"PrayerTimes host on http://0.0.0.0:{port}  (tier1 {len(OVERRIDES)} countries, "
          f"tier2 rows: {len(load_json(TIER2_PATH, {}))})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()