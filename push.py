#!/usr/bin/env python3
"""Web Push for Prayer Times — VAPID keys, subscription store, prayer-time scheduler.

Privacy: subscriptions are stored with coordinates/location only (no identity); the store
lives in data/ like Tier 2. The scheduler computes each subscriber's prayer times with the
SAME engine + tier params the app renders (solar.Engine + params_for merge).
"""
import base64
import datetime
import json
import logging
import os
import threading
import time

from solar import Engine, sun_position, julian_day

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
VAPID_PATH = os.path.join(DATA, 'vapid.json')
SUBS_PATH = os.path.join(DATA, 'push_subs.json')

log = logging.getLogger('push')
if not log.handlers:
    _h = logging.FileHandler(os.path.join(HERE, 'push.log'), encoding='utf-8')
    _h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(_h)
    log.setLevel(logging.INFO)

_sent = set()          # subkey|date|prayer — in-memory dedupe (restart may re-fire once)
_lock = threading.Lock()

PRAYER_KEYS = ['fajr', 'sunrise', 'dhuhr', 'asr', 'maghrib', 'isha']
PRAYER_NAMES = {'fajr': 'Fajr', 'sunrise': 'Sunrise', 'dhuhr': 'Dhuhr',
                'asr': 'Asr', 'maghrib': 'Maghrib', 'isha': 'Isha'}


def _load(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------- VAPID ----------------
def vip_keys():
    try:
        from pywebpush import Vapid
    except Exception as e:
        log.error('pywebpush unavailable: %r', e)
        return None
    data = _load(VAPID_PATH, {})
    try:
        v = Vapid.from_string(data['private_pem'], data['public_pem'])
    except Exception:
        v = Vapid()
        v.generate_keys()
        _save(VAPID_PATH, {'private_pem': v.private_pem().decode(), 'public_pem': v.public_pem().decode()})
        log.info('generated fresh VAPID keys')
    return v


def vapid_public_b64():
    v = vip_keys()
    if v is None:
        return None
    # public_pem -> raw P-256 point (uncompressed 65 bytes), url-safe base64 without padding
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
        pub = serialization.load_pem_public_key(v.public_pem(), backend=default_backend())
        nums = pub.public_numbers()
        raw = (b'\x04' + nums.x.to_bytes(32, 'big') + nums.y.to_bytes(32, 'big'))
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
    except Exception as e:
        log.error('public key serialize failed: %r', e)
        return None


# ---------------- subscriptions ----------------
def subscribe(sub, lat, lng, elevation_m, tz_id):
    ep = sub.get('endpoint', '')
    if not ep:
        return False, 'endpoint missing'
    key = ep.rstrip('/').split('/')[-1] or ep
    subs = _load(SUBS_PATH, {})
    subs[key] = {'endpoint': ep, 'keys': sub.get('keys', {}),
                 'lat': lat, 'lng': lng, 'elevation_m': elevation_m,
                 'tz_id': tz_id, 'created': time.time()}
    _save(SUBS_PATH, subs)
    log.info('subscribed %s (%s, %s)', key[:24], lat, lng)
    return True, 'subscribed'


def unsubscribe(endpoint=''):
    subs = _load(SUBS_PATH, {})
    if endpoint:
        key = endpoint.rstrip('/').split('/')[-1] or endpoint
        if key in subs:
            del subs[key]
            _save(SUBS_PATH, subs)
            log.info('unsubscribed %s', key[:24])
            return True, 'unsubscribed'
    return False, 'not found'


# ---------------- prayer-time computation (mirror of the app's DulookCal) ----------------
def compute_today_times(lat, lng, elevation_m, tz_id):
    import zoneinfo as _zi
    try:
        tz = _zi.ZoneInfo(tz_id) if tz_id else None
    except Exception:
        tz = None
    if tz is None:
        return None
    today = datetime.datetime.now(tz).date()
    tz_h = datetime.datetime.now(tz).utcoffset().total_seconds() / 3600.0

    from server import params_for   # tier2 over tier1, same merge as the client
    p = params_for(lat, lng)
    pm = p.get('params', {}) or {}
    fajr_a = pm.get('fajrAngle', 18.0)
    isha_a = pm.get('ishaAngle', 18.0)
    isha_i = pm.get('ishaIntervalMin')
    dhuhr_o = pm.get('dhuhrOffsetMin', 0)
    asr_f = pm.get('asrFactor', 1)
    asr_o = pm.get('asrOffsetMin', 0)
    sr_x = pm.get('sunriseExtraMin', -1)
    mg_x = pm.get('maghribExtraMin', 1)

    eng = Engine(lat, lng, tz_h, max(0, elevation_m or 0))
    jd0 = julian_day(today.year, today.month, today.day)
    noon, sr_raw, ss_raw = eng.transits(jd0, tz_h)

    def fmt(t):
        if t is None:
            return None
        t = t % 24
        hh = int(t)
        mm = int(round((t - hh) * 60))
        if mm == 60:
            mm = 0
            hh = (hh + 1) % 24
        return [hh, mm]

    times = {
        'fajr': fmt(eng.at(jd0, -fajr_a, -1, tz_h)),
        'sunrise': fmt(sr_raw + sr_x / 60.0),
        'dhuhr': fmt(noon + dhuhr_o / 60.0),
        'asr': fmt(eng.at_asr(jd0, asr_f, tz_h) + asr_o / 60.0),
        'maghrib': fmt(ss_raw + mg_x / 60.0),
        'isha': fmt((eng.at(jd0, -isha_a, 1, tz_h)) if isha_i is None else (ss_raw + mg_x / 60.0) + isha_i / 60.0),
    }
    return {'date': today, 'tz': tz, 'times': times}


def _send_one(sub, key, prayer, payload):
    try:
        from pywebpush import webpush
        v = vip_keys()
        if v is None:
            return
        pem = v.private_pem() if callable(v.private_pem) else v.private_pem
        # py-vapid from_string wants the base64 BODY (armor stripped) — not a PEM string
        b64 = pem.split(b'-----')[2]
        b64 = b64.replace(b'\n', b'').replace(b'\r', b'')
        resp = webpush(subscription_info=sub, data=json.dumps(payload),
                       vapid_private_key=b64.decode(),
                       vapid_claims={'sub': 'mailto:prayer-times@localhost'},
                       ttl=86400, timeout=15)
        status = getattr(resp, 'status_code', 201)
        if status == 410 or status == 404:
            _remove_sub(key)
            log.warning('push %s (%s) -> stale subscription removed (%s)', prayer, key[:10], status)
        else:
            log.info('push sent %s (%s) -> %s', prayer, key[:10], status)
    except Exception as e:
        if '410' in str(e) or '404' in str(e):
            _remove_sub(key)
        log.warning('push failed %s (%s): %r', prayer, key[:10], e)


def _remove_sub(key):
    subs = _load(SUBS_PATH, {})
    if key in subs:
        del subs[key]
        _save(SUBS_PATH, subs)


def push_loop():
    log.info('push scheduler started')
    while True:
        try:
            subs = _load(SUBS_PATH, {})
            for key, sub in subs.items():
                times = compute_today_times(sub.get('lat'), sub.get('lng'),
                                            sub.get('elevation_m', 0), sub.get('tz_id') or '')
                if not times:
                    continue
                date_txt = times['date'].isoformat()
                now = datetime.datetime.now(times['tz'])
                for prayer, hhmm in times['times'].items():
                    if hhmm is None:
                        continue
                    hh, mm = hhmm
                    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                    diff = (now - at).total_seconds()
                    if diff < 0 or diff > 90:
                        continue
                    idkey = key + '|' + date_txt + '|' + prayer
                    if idkey in _sent:
                        continue
                    _sent.add(idkey)
                    _send_one({'endpoint': sub['endpoint'], 'keys': sub.get('keys', {})},
                              key, prayer,
                              {'title': 'Prayer Times',
                               'body': PRAYER_NAMES.get(prayer, prayer) + ' · ' +
                                       f'{hh:02d}:{mm:02d}',
                               'tag': 'prayer-' + prayer,
                               'timestamp': int(time.time())})
        except Exception as e:
            log.error('push loop error: %r', e)
        time.sleep(45)