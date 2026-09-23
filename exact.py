#!/usr/bin/env python3
"""Exact tier: real daily IACAD/salatcalendar.com tables, byte-served, not recalculated.

Marked locations (cap 10) get a full-year table fetched from the named-city path
(nearest census/supplement city, same as the Tier-2 worker), under a SEPARATE daily cap
(daily_exact_budget, default 50 - independent of the Tier-2 200/day budget). Annual
renewal: when <30 days of [today..Dec31] remain uncovered for the table's year, the worker
starts filling the NEXT year. Each row stores fetchedAt.
"""
import datetime
import json
import logging
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
MARKED_PATH = os.path.join(DATA, 'exact_marked.json')
TABLES_PATH = os.path.join(DATA, 'exact_tables.json')
BUDGET_PATH = os.path.join(DATA, 'exact_budget.json')
CONFIG_PATH = os.path.join(DATA, 'config.json')
RENEW_TRIGGER = 30          # uncovered days left in [today..Dec31] that trigger next year
EXACT_CAP = 10
_lock = threading.Lock()

log = logging.getLogger('exact')
if not log.handlers:
    _h = logging.FileHandler(os.path.join(HERE, 'exact.log'), encoding='utf-8')
    _h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(_h)
    log.setLevel(logging.INFO)


def _load(p, d):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return d


def _save(p, o):
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(o, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _cfg():
    return _load(CONFIG_PATH, {})


# ---------------- budget (separate from Tier 2!) ----------------
def budget_state():
    limit = int(_cfg().get('daily_exact_budget', 50))
    st = _load(BUDGET_PATH, {})
    today = time.strftime('%Y-%m-%d')
    if st.get('date') != today:
        st = {'date': today, 'used': 0}
    return limit, st


def budget_meta():
    limit, st = budget_state()
    used = int(st.get('used', 0))
    return {'daily_limit': limit, 'used_today': used, 'remaining': max(0, limit - used)}


def _budget_take():
    limit, st = budget_state()
    used = int(st.get('used', 0))
    if used >= limit:
        return False
    st['used'] = used + 1
    _save(BUDGET_PATH, st)
    return True


def cluster_key(lat, lng):
    return f"{round(lat, 2):.2f},{round(lng, 2):.2f}"


# ---------------- marking (opt-in, capped) ----------------
def request_mark(lat, lng, replace=None):
    key = cluster_key(lat, lng)
    with _lock:
        marked = _load(MARKED_PATH, {})
        if key in marked:
            return True, 'already marked exact'
        if len(marked) >= EXACT_CAP:
            chosen = None
            if replace == 'oldest':
                chosen = min(marked, key=lambda k: marked[k].get('requested_at', 0))
            elif isinstance(replace, str) and replace in marked:
                chosen = replace
            if chosen is None:
                return False, (f'The exact-location cap of {EXACT_CAP} is reached. '
                               f'Replace one (replace: \'<cluster>\' or \'oldest\') first.')
            del marked[chosen]
            tables = _load(TABLES_PATH, {})
            if chosen in tables:
                del tables[chosen]
                _save(TABLES_PATH, tables)
        marked[key] = {'lat': lat, 'lng': lng,
                       'year': datetime.date.today().year,
                       'requested_at': time.time()}
        _save(MARKED_PATH, marked)
        return True, f'marked exact - backfill queued (years from {datetime.date.today().year})'


def marked_list():
    marked = _load(MARKED_PATH, {})
    tables = _load(TABLES_PATH, {})
    out = []
    for key, m in marked.items():
        tab = tables.get(key) or {}
        dates = tab.get('dates', {})
        year_days = 366 if (int(m.get('year', 2026)) % 4 == 0) else 365
        out.append({'cluster': key, 'lat': m.get('lat'), 'lng': m.get('lng'),
                    'city': tab.get('city', ''), 'year': tab.get('year', m.get('year')),
                    'days_ready': len(dates), 'days_total': year_days,
                    'requested_at': m.get('requested_at')})
    return {'cap': EXACT_CAP, 'marked': len(marked), 'locations': out,
            'budget': budget_meta()}


def exact_row(lat, lng, date_iso):
    """Stored row for (cluster, date) or None."""
    key = cluster_key(lat, lng)
    tables = _load(TABLES_PATH, {})
    tab = tables.get(key)
    if not tab:
        return None
    return tab.get('dates', {}).get(date_iso)


# ---------------- backfill worker + renewal ----------------
def _year_days(y):
    return 366 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 365


def _date_batch_order(year):
    """Today-first: dates from today backwards to Jan 1, then forwards to Dec 31."""
    today = datetime.date.today()
    first = datetime.date(year, 1, 1)
    last = datetime.date(year, 12, 31)
    order = []
    d = today
    while d >= first:
        order.append(d)
        d -= datetime.timedelta(days=1)
    d = today + datetime.timedelta(days=1)
    while d <= last:
        order.append(d)
        d += datetime.timedelta(days=1)
    return order


def _next_missing(cluster, tables):
    tab = tables.get(cluster)
    if not tab:
        return None, None
    year = int(tab.get('year', datetime.date.today().year))
    dates = tab.get('dates', {})
    for d in _date_batch_order(year):
        if d.isoformat() not in dates:
            return d, year
    return None, year


def _renewal_needed(cluster, tables):
    """<30 uncovered days inside [today..Dec31] of the table year -> fill next year."""
    tab = tables.get(cluster)
    if not tab:
        return False
    year = int(tab.get('year', datetime.date.today().year))
    dates = tab.get('dates', {})
    today = datetime.date.today()
    dec31 = datetime.date(year, 12, 31)
    if dec31 < today:
        return True  # table year already behind
    uncovered = 0
    d = today
    while d <= dec31:
        if d.isoformat() not in dates:
            uncovered += 1
        d += datetime.timedelta(days=1)
    return uncovered <= RENEW_TRIGGER


def backfill_step():
    """One worker pass: advance every marked location up to ~3 fetches (cap-gated)."""
    from salatclient import SalatClient
    marked = _load(MARKED_PATH, {})
    if not marked:
        return
    client = SalatClient(os.path.join(HERE, 'round3_times.csv'),
                         os.path.join(HERE, 'data', 'city_supplement.json'))
    tables = _load(TABLES_PATH, {})
    changed = False
    for key, m in marked.items():
        tab = tables.setdefault(key, {'lat': m['lat'], 'lng': m['lng']})
        tab.setdefault('year', int(m.get('year', datetime.date.today().year)))
        tab.setdefault('city', '')
        tab.setdefault('dates', {})
        if _renewal_needed(key, tables):
            tab['year'] = tab.get('year', datetime.date.today().year) + 1
            log.info('renewal: %s -> year %s', key, tab['year'])
        if not tab.get('city_id'):
            city, dist = client.table.nearest(m['lat'], m['lng'])
            tab['city_id'] = city['city_id']
            tab['city'] = city['city_name'] + ' (' + city['iso2'] + ')'
            tab['city_lat'], tab['city_lon'] = city['lat'], city['lon']
        opener = client.open_city_session(tab['city_id'])
        if opener is None:
            log.warning('session failed for %s', key)
            continue
        for _ in range(3):
            if not _budget_take():
                log.info('exact budget exhausted (used %s/%s today)',
                         budget_meta()['used_today'], budget_meta()['daily_limit'])
                break
            nxt, yr = _next_missing(key, tables)
            if nxt is None:
                if _renewal_needed(key, tables):
                    tab['year'] = tab.get('year', datetime.date.today().year) + 1
                    log.info('renewal: %s -> year %s', key, tab['year'])
                    continue
                log.info('%s: year %s complete', key, yr)
                break
            ddmmyy = f"{nxt.day:02d}/{nxt.month:02d}/{nxt.year}"
            row = client.fetch_named_date(opener, tab['city_id'], ddmmyy)
            changed = True
            if row:
                store = {k: row[k] for k in ('fajr', 'sunrise', 'dhuhr', 'asr', 'maghrib', 'isha')}
                store['fetchedAt'] = time.time()
                tab['dates'][nxt.isoformat()] = store
            else:
                log.warning('no row for %s %s', key, nxt.isoformat())
    if changed:
        _save(TABLES_PATH, tables)


def exact_worker():
    log.info('exact worker started')
    while True:
        try:
            backfill_step()
        except Exception as e:
            log.exception('exact worker error: %r', e)
        time.sleep(12)