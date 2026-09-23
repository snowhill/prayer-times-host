"""Solve the app's parameter vector from a site-derived 4-date timeset.

The site publishes HH:MM wall times (local tz). This inverts them into the same
parameter shape the app's COUNTRY_OVERRIDES/Tier-2 tables use:
  fajrAngle, ishaAngle (or ishaIntervalMin), dhuhrOffsetMin, asrFactor,
  asrOffsetMin, sunriseExtraMin, maghribExtraMin
All solve steps use the ported engine (solar.Engine) so the solved parameters are
reproduced by the client with the same math. Sanity bounds applied AFTER solving:
angles 12-22 deg, minute offsets within +-60, asrFactor in {1,2}. Anything outside
is rejected (returned as None overall) by the caller.
"""
import datetime

from solar import Engine, ts_to_decimal

REFERENCE_DATES = [datetime.date(2026, 3, 20), datetime.date(2026, 6, 21),
                   datetime.date(2026, 9, 22), datetime.date(2026, 12, 21)]


def _minute_err(calc_hours, obs_hours):
    if calc_hours is None or obs_hours is None:
        return None
    d = (calc_hours - obs_hours) * 60.0
    if d > 720:   # wrap across midnight
        d -= 1440
    elif d < -720:
        d += 1440
    return d


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def solve_params(lat, lng, elevation_m, tz_hours, times, tz_by_date=None):
    """times: {iso_date: {'fajr': hhmm|None, 'sunrise':..., 'dhuhr':, 'asr':, 'maghrib':, 'isha':}}
    Returns (params_dict, residuals_dict) or (None, residuals) when sanity bounds fail.
    """
    eng = Engine(lat, lng, tz_hours, elevation_m)
    obs = {}
    for iso, t in times.items():
        y, m, d = map(int, iso.split('-'))
        jd0 = eng._jd0(datetime.date(y, m, d))
        obs[iso] = {k: ts_to_decimal(v) for k, v in t.items()}
        obs[iso]['_jd0'] = jd0
        obs[iso]['_tz'] = (tz_by_date or {}).get(iso, tz_hours)

    # ---- dhuhrOffsetMin (vs true transit) ----
    dhuhr_offs = []
    for iso, o in obs.items():
        noon, _, _ = eng.transits(o['_jd0'], o['_tz'])
        if o.get('dhuhr') is not None and noon is not None:
            e = _minute_err(o['dhuhr'], noon)
            if e is not None:
                dhuhr_offs.append(e)
    dhuhr_offset = round(_median(dhuhr_offs)) if dhuhr_offs else None

    # ---- sunriseExtraMin / maghribExtraMin (vs dip-horizon) ----
    sr_extras, ss_extras = [], []
    for iso, o in obs.items():
        _, sr, ss = eng.transits(o['_jd0'], o['_tz'])
        if o.get('sunrise') is not None and sr is not None:
            e = _minute_err(o['sunrise'], sr)
            if e is not None:
                sr_extras.append(e)
        if o.get('maghrib') is not None and ss is not None:
            e = _minute_err(o['maghrib'], ss)
            if e is not None:
                ss_extras.append(e)
    sunrise_extra = round(_median(sr_extras)) if sr_extras else None
    maghrib_extra = round(_median(ss_extras)) if ss_extras else None

    # ---- binary search helpers ----
    def solve_crossing(field, direction, lo=12.0, hi=22.0):
        """Find angle whose crossing time matches observed, per date; median across dates."""
        per_date = []
        for iso, o in obs.items():
            target = o.get(field)
            if target is None:
                continue
            a_lo, a_hi = lo, hi
            best_err = None
            for _ in range(80):
                mid = (a_lo + a_hi) / 2.0
                t = eng.at(o['_jd0'], -mid, direction, o['_tz'])
                err = _minute_err(t, target) if t is not None else None
                if err is None:
                    return None
                if abs(err) <= 0.001:
                    best_err = err
                    per_date.append(mid)
                    break
                # fajr (dir=-1): larger angle => EARLIER time => err decreases with angle
                # isha (dir=+1): larger angle => LATER time   => err increases with angle
                if (err > 0) == (direction < 0):
                    a_lo = mid
                else:
                    a_hi = mid
            else:
                per_date.append((a_lo + a_hi) / 2.0)
        if not per_date:
            return None
        return round(_median(per_date), 1)

    fajr_angle = solve_crossing('fajr', -1)
    isha_angle = solve_crossing('isha', 1)

    # ---- isha interval alternative ----
    gaps = []
    for iso, o in obs.items():
        if o.get('maghrib') is not None and o.get('isha') is not None:
            e = _minute_err(o['isha'], o['maghrib'])   # (isha - maghrib) in minutes
            if e is not None and e > 0:
                gaps.append(e)
    isha_interval = round(_median(gaps)) if len(gaps) >= 2 else None

    # decide angle vs interval: whoever fits observed better
    residuals = {}
    if isha_angle is not None and isha_interval is not None and 55 <= isha_interval <= 130:
        err_ang, err_int = [], []
        for iso, o in obs.items():
            if o.get('isha') is None:
                continue
            t_ang = eng.at(o['_jd0'], -isha_angle, 1, o['_tz'])
            t_int = (o.get('maghrib') + isha_interval / 60.0) if o.get('maghrib') is not None else None
            if t_ang is not None:
                err_ang.append(abs(_minute_err(t_ang, o['isha'])))
            if t_int is not None:
                err_int.append(abs(_minute_err(t_int, o['isha'])))
        mean_a = sum(err_ang) / len(err_ang) if err_ang else 999
        mean_i = sum(err_int) / len(err_int) if err_int else 999
        if mean_i < mean_a - 0.5:
            isha_angle = None
        else:
            isha_interval = None

    # ---- asr factor + offset ----
    asr_factor, asr_offset = None, 0
    if any(o.get('asr') is not None for o in obs.values()):
        best = None
        for f in (1.0, 2.0):
            errs = []
            for iso, o in obs.items():
                if o.get('asr') is None:
                    continue
                t = eng.at_asr(o['_jd0'], f, o['_tz'])
                e = _minute_err(o['asr'], t)
                if e is not None:
                    errs.append(e)
            if errs:
                mae = sum(abs(e) for e in errs) / len(errs)
                if best is None or mae < best[0]:
                    best = (mae, f, errs)
        if best:
            asr_factor = int(best[1])
            asr_offset = round(_median(best[2]))

    params = {}
    if fajr_angle is not None:
        params['fajrAngle'] = fajr_angle
    if isha_angle is not None:
        params['ishaAngle'] = isha_angle
    if isha_interval is not None and 55 <= isha_interval <= 130:
        params['ishaIntervalMin'] = isha_interval
    if dhuhr_offset is not None:
        params['dhuhrOffsetMin'] = dhuhr_offset
    if asr_factor is not None:
        params['asrFactor'] = asr_factor
    if asr_offset:
        params['asrOffsetMin'] = asr_offset
    if sunrise_extra is not None:
        params['sunriseExtraMin'] = sunrise_extra
    if maghrib_extra is not None:
        params['maghribExtraMin'] = maghrib_extra

    residuals = {
        'fajr': fajr_angle, 'isha': isha_angle, 'ishaIntervalMin': isha_interval,
        'dhuhrOffsetMin': dhuhr_offset, 'asrFactor': asr_factor, 'asrOffsetMin': asr_offset,
        'sunriseExtraMin': sunrise_extra, 'maghribExtraMin': maghrib_extra,
    }

    # ---- sanity bounds (per spec) ----
    for k, v in params.items():
        if k == 'fajrAngle' or k == 'ishaAngle':
            if not (12.0 <= v <= 22.0):
                return None, residuals
        elif k in ('sunriseExtraMin', 'maghribExtraMin', 'dhuhrOffsetMin', 'asrOffsetMin'):
            if abs(v) > 60:
                return None, residuals
        elif k == 'ishaIntervalMin':
            if not (55 <= v <= 130):
                return None, residuals
        elif k == 'asrFactor':
            if v not in (1, 2):
                return None, residuals
    return params, residuals