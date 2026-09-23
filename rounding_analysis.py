#!/usr/bin/env python3
"""Part C: rounding-rule analysis — app raw (pre-rounding) values vs round-1 census integers.

Transcription of app/index.html computeTimes (Dulook branch) 1:1, using the server's
own country overrides for each row. Classifies per prayer: floor / ceil / nearest.
"""
import csv, json, math
from datetime import date as _date
from zoneinfo import ZoneInfo
import server  # for resolve_country + OVERRIDES (same params the app would use)

# ---------- 1:1 transcription of the app's math ----------
def fix_angle(a): return a - 360*math.floor(a/360)
def fix_hour(a):
    a %= 24
    return a if a >= 0 else a + 24
def dsin(d): return math.sin(math.radians(d))
def dcos(d): return math.cos(math.radians(d))
def dtan(d): return math.tan(math.radians(d))
def darcsin(x): return math.degrees(math.asin(x))
def darccos(x): return math.degrees(math.acos(x))
def darctan2(y, x): return math.degrees(math.atan2(y, x))
def darccot(x): return math.degrees(math.atan(1.0/x))

def julian_day(y, m, d):
    if m <= 2: y -= 1; m += 12
    A = math.floor(y/100)
    B = 2 - A + math.floor(A/4)
    return math.floor(365.25*(y+4716)) + math.floor(30.6001*(m+1)) + d + B - 1524.5

def sun_position(jd):
    Dd = jd - 2451545.0
    g = fix_angle(357.529 + 0.98560028*Dd)
    q = fix_angle(280.459 + 0.98564736*Dd)
    L = fix_angle(q + 1.915*dsin(g) + 0.020*dsin(2*g))
    e = 23.439 - 0.00000036*Dd
    decl = darcsin(dsin(e)*dsin(L))
    RA = darctan2(dcos(e)*dsin(L), dcos(L))/15
    RA = fix_hour(RA)
    eqt = q/15 - RA
    return decl, eqt

def hour_angle(lat, decl, alt_deg):
    cosH = (dsin(alt_deg) - dsin(lat)*dsin(decl)) / (dcos(lat)*dcos(decl))
    if cosH > 1 or cosH < -1: return None
    return darccos(cosH)/15

def asr_altitude(factor, lat, decl):
    return darccot(factor + dtan(math.fabs(lat-decl)))

ELEV_DIP = 0.0347

def compute_raw(y, mo, d, lat, lng, tz_h, fajr_angle, isha_angle,
                dhuhr_off=0, asr_factor=1, asr_off=0, sr_extra=0, mg_extra=0,
                isha_int=None, elev_m=0):
    jd0 = julian_day(y, mo, d)
    decl0, eqt0 = sun_position(jd0 + 0.5 - lng/360)
    noon = 12 - lng/15 + tz_h - eqt0
    def at(alt_deg, dir_):
        t = noon + dir_*6
        for _ in range(3):
            jdI = jd0 + (t - tz_h)/24
            decl, eqt = sun_position(jdI)
            local_noon = 12 - lng/15 + tz_h - eqt
            h = hour_angle(lat, decl, alt_deg)
            if h is None: return None
            t = local_noon + dir_*h
        return t
    def at_asr(factor):
        t = noon + 3.5
        for _ in range(3):
            jdI = jd0 + (t - tz_h)/24
            decl, eqt = sun_position(jdI)
            local_noon = 12 - lng/15 + tz_h - eqt
            aa = asr_altitude(factor, lat, decl)
            h = hour_angle(lat, decl, aa)
            if h is None: return None
            t = local_noon + h
        return t
    dip = ELEV_DIP * math.sqrt(max(0, elev_m))
    horizon = 0.833 + dip
    fajr = at(-fajr_angle, -1)
    sr = at(-horizon, -1)
    sunrise = None if sr is None else sr + sr_extra/60
    dhuhr = noon + dhuhr_off/60
    asr = at_asr(asr_factor)
    if asr is not None: asr += asr_off/60
    ss = at(-horizon, 1)
    maghrib = None if ss is None else ss + mg_extra/60
    if isha_int is not None:
        isha = None if maghrib is None else maghrib + isha_int/60
    else:
        isha = at(-isha_angle, 1)
    return {'fajr': fajr, 'sunrise': sunrise, 'dhuhr': dhuhr, 'asr': asr,
            'maghrib': maghrib, 'isha': isha}

# ---------- load census + classify ----------
rows = []
with open(r'C:\Users\LENOVO\salat-validate\salatcalendar_136_times.csv', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        rows.append(r)

hasanafi = {'Hanafi': 2.0, 'Standard': 1.0}
prayers = ['fajr', 'sunrise', 'dhuhr', 'asr', 'maghrib', 'isha']
agg = {p: {'floor': 0, 'ceil': 0, 'round': 0, 'none': 0} for p in prayers}
total = len(rows)
for r in rows:
    y, mo, d = map(int, r['date'].split('-'))
    lat, lng = float(r['lat']), float(r['lon'])
    tz = ZoneInfo(r['timezone'])
    from datetime import datetime as _dt
    tz_h = (_dt(y, mo, d, 12, tzinfo=tz).utcoffset() or __import__('datetime').timedelta(0)).total_seconds()/3600
    elev = float(r['elevation_m'])
    # the app's per-country override params (same source the deployed server uses)
    code = server.resolve_country(lat, lng)
    ov = server.OVERRIDES.get(code) or {}
    fa = float(ov.get('fajrAngle', r['fajr_angle']) or r['fajr_angle'])
    ia = float(ov.get('ishaAngle', r['isha_angle']) or r['isha_angle'])
    af = float(ov.get('asrFactor', hasanafi.get(r['asr_school'], 1.0)))
    raw = compute_raw(y, mo, d, lat, lng, tz_h, fa, ia,
                      dhuhr_off=float(ov.get('dhuhrOffsetMin', 0) or 0),
                      asr_factor=af,
                      asr_off=float(ov.get('asrOffsetMin', 0) or 0),
                      sr_extra=float(ov.get('sunriseExtraMin', -1) or -1),
                      mg_extra=float(ov.get('maghribExtraMin', 1) or 1),
                      isha_int=ov.get('ishaIntervalMin'),
                      elev_m=elev)
    for p in prayers:
        site = r[p].strip()
        if site in ('-----', 'إضاءة', 'فوق', 'تحت') or not site:
            agg[p]['none'] += 1
            continue
        try:
            sh, sm = map(int, site.split(':'))
        except ValueError:
            agg[p]['none'] += 1
            continue
        site_min = sh*60 + sm
        rawv = raw[p]
        if rawv is None:
            agg[p]['none'] += 1
            continue
        raw_min = rawv * 60
        fl, ce = math.floor(raw_min), math.ceil(raw_min)
        rn = math.floor(raw_min + 0.5)
        near = rn if abs(raw_min - rn) <= abs(raw_min - math.floor(raw_min if rn == fl else raw_min + 1e-9)) else fl
        if site_min == fl: agg[p]['floor'] += 1
        if site_min == ce: agg[p]['ceil'] += 1
        if site_min == round(raw_min): agg[p]['round'] += 1

print(f'rows analysed: {total}')
print(f'{"prayer":9s} {"floor":>6s} {"ceil":>6s} {"nearest":>8s} {"skip":>5s}')
for p in prayers:
    a = agg[p]
    n = total - a['none']
    print(f'{p:9s} {a["floor"]:5d} ({100*a["floor"]/max(n,1):5.1f}%) {a["ceil"]:5d} ({100*a["ceil"]/max(n,1):5.1f}%)'
          f' {a["round"]:7d} ({100*a["round"]/max(n,1):5.1f}%) skip={a["none"]}')