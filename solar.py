"""Faithful Python port of prayer-times.html's astronomy engine (DulookCal baseline).

The JS engine (from the app file) is the single source of truth; this mirrors its
formulas exactly so the solver inverts the SAME math the client will render with.
"""
import math

D2R = math.pi / 180.0
ELEV_DIP_COEF = 0.0347      # minutes dip per sqrt(metres) — matches app constants

def _fix_angle(a):
    a %= 360.0
    if a < 0:
        a += 360.0
    return a

def _fix_hour(a):
    a %= 24.0
    if a < 0:
        a += 24.0
    return a

def julian_day(y, m, d):
    if m <= 2:
        y -= 1
        m += 12
    A = math.floor(y / 100.0)
    B = 2 - A + math.floor(A / 4.0)
    return (math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1))
            + d + B - 1524.5)

def sun_position(jd):
    """-> (decl_deg, eqt_hours) exactly like the app's sunPosition(jd)."""
    Dd = jd - 2451545.0
    g = _fix_angle(357.529 + 0.98560028 * Dd)
    q = _fix_angle(280.459 + 0.98564736 * Dd)
    L = _fix_angle(q + 1.915 * math.sin(g * D2R) + 0.020 * math.sin(2 * g * D2R))
    e = 23.439 - 0.00000036 * Dd
    decl = math.degrees(math.asin(math.sin(e * D2R) * math.sin(L * D2R)))
    RA = math.degrees(math.atan2(math.cos(e * D2R) * math.sin(L * D2R), math.cos(L * D2R))) / 15.0
    RA = _fix_hour(RA)
    eqt = q / 15.0 - RA
    return decl, eqt

def hour_angle(lat_deg, decl_deg, alt_deg):
    c = (math.sin(alt_deg * D2R) - math.sin(lat_deg * D2R) * math.sin(decl_deg * D2R)) / (
        math.cos(lat_deg * D2R) * math.cos(decl_deg * D2R))
    if c > 1.0 or c < -1.0:
        return None
    return math.degrees(math.acos(c)) / 15.0

def asr_altitude(factor, lat_deg, decl_deg):
    return math.degrees(math.atan2(1.0, factor + math.tan(abs(lat_deg - decl_deg) * D2R)))

def ts_to_decimal(hhmm):
    """'HH:MM' (or 'H:MM') -> decimal hours. None for site markers (non-numeric)."""
    try:
        h, m = hhmm.split(':')
        return int(h) + int(m) / 60.0
    except Exception:
        return None

class Engine:
    """computeTimes equivalent for the Dulook baseline (no method offsets)."""

    def __init__(self, lat, lng, tz_hours, elevation_m):
        self.lat = lat
        self.lng = lng
        self.tz = tz_hours
        self.dip = ELEV_DIP_COEF * math.sqrt(max(0.0, elevation_m))
        self.horizon = 0.833 + self.dip

    def _jd0(self, date):
        return julian_day(date.year, date.month, date.day)

    def _noon0(self, jd0):
        _, eqt0 = sun_position(jd0 + 0.5 - self.lng / 360.0)
        return 12.0 - self.lng / 15.0 + self.tz - eqt0

    def at(self, jd0, alt_deg, direction, tz=None):
        """Local wall-clock hour when the sun crosses alt_deg (3-pass refinement, as app)."""
        tz = self.tz if tz is None else tz
        t = self._noon0(jd0) + direction * 6.0
        for _ in range(3):
            jd_inst = jd0 + (t - tz) / 24.0
            decl, eqt = sun_position(jd_inst)
            local_noon = 12.0 - self.lng / 15.0 + tz - eqt
            h = hour_angle(self.lat, decl, alt_deg)
            if h is None:
                return None
            t = local_noon + direction * h
        return t

    def at_asr(self, jd0, factor, tz=None):
        tz = self.tz if tz is None else tz
        t = self._noon0(jd0) + 3.5
        for _ in range(3):
            jd_inst = jd0 + (t - tz) / 24.0
            decl, eqt = sun_position(jd_inst)
            local_noon = 12.0 - self.lng / 15.0 + tz - eqt
            aa = asr_altitude(factor, self.lat, decl)
            h = hour_angle(self.lat, decl, aa)
            if h is None:
                return None
            t = local_noon + h
        return t

    def transits(self, jd0, tz=None):
        """Return (noon, sunrise_raw, sunset_raw) local wall hours with the baseline
        horizon angle (refraction + elevation dip), exactly as the app computes them."""
        tz = self.tz if tz is None else tz
        noon = 12.0 - self.lng / 15.0 + tz - _eqt0(self.lng, jd0)
        sr = self.at(jd0, -self.horizon, -1, tz)
        ss = self.at(jd0, -self.horizon, 1, tz)
        return noon, sr, ss


def _eqt0(lng, jd0):
    _, eqt0 = sun_position(jd0 + 0.5 - lng / 360.0)
    return eqt0