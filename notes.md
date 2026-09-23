# Prayer Times — self-hosted PWA + shared calibration backend

Host: HermesPc (Windows), app dir `C:\Users\LENOVO\prayer-times-host\`

## Public URL (quick tunnel — changes on every tunnel restart)
https://sensitivity-earl-boom-plug.trycloudflare.com
(HTTPS is provided by Cloudflare; browser geolocation permission works on this origin.)

## Restarting after a reboot
```
cd C:\Users\LENOVO\prayer-times-host
start_prayer_times.bat
```
starts the backend (`python server.py 8787`) then the tunnel, and prints the new
trycloudflare URL. (Python used must be the KiCad 10.0 build — it has `tzdata` installed,
which the backend needs for DST-correct per-season verification.)

## Tier 2 row count
**4 verified clusters** (2026-09-21):
- 25.33,56.34  Khor Fakkan (AE)  — fajr 18.0 / isha 18.1 / dhuhr +2 / sunrise −1 / maghrib +1
- 51.51,-0.13   London (GB)      — fajr 18.0 / isha 17.0 / dhuhr 0 / sunrise −1 / maghrib +2
- 40.71,-74.01  New York City (US) — fajr 17.9 / isha 17.0 / dhuhr 0 / sunrise −2 / maghrib +2
- 24.86,67.00   Karachi (PK)     — fajr 18.1 / isha 18.0 / dhuhr 0 / asrFactor 2 (Hanafi) / maghrib +5

Each was solved from salatcalendar.com's named-city output for the 4 seasonal dates
(Mar 20 / Jun 21 / Sep 22 / Dec 21) with the ported app engine, sanity-bounded
(angles 12–22°, offsets ±60 min, asrFactor ∈ {1,2}), and each independently
**cross-validates the app's own static country table** (GB/US isha 17.0, PK asrFactor 2,
AE dhuhr +2). Re-verification only happens when a cluster is flagged again.
Outbound salatcalendar requests are rate-limited to 1 per 3.5 s.

## Endpoints
- GET  /api/params?lat=&lng=  — tier-2 cluster within 10 km if present, else tier-1 country
  (boundary resolution = the same embedded-dataset logic the client uses offline)
- POST /api/flag {lat,lng,prayer,userStatedTime} — marks cluster for re-verification
  (never writes values directly); dedupes per cluster
- GET  /api/status — tier2 count, queue depth
- GET  / — the app (PWA: manifest.json, sw.js, icons; service worker caches the shell
  so the app works fully offline after first load)

## Files
- server.py (HTTP + worker), solver.py (parameter inversion), solar.py (ported engine),
  salatclient.py (rate-limited site client), enrich_cities.py (city-table supplement)
- app/index.html (app + client-side hooks: /api/params with localStorage cache + 3.2 s
  timeout fallback to the local 239-country table; "This looks off" flag control in Settings)
- data/{boundaries,country_overrides,tier2,verify_queue,city_supplement}.json
- start_prayer_times.bat, cloudflared.exe

## Known caveats
- trycloudflare URL rotates per tunnel start — fine for the PWA test, but the stable
  subdomain (prayer.<your-domain>) needs a Cloudflare account login (`cloudflared tunnel login`, once).
- The nearest-city table came from the 239-country census (1 city/country) + 13 supplement
  cities; a flagged cluster far from its country's representative city verifies against
  that representative (same country method), flagged as distance_km in tier2.json.
- 'Paris' in city_supplement.json with id 47444 is Paris, UKRAINE (the site DB name clash),
  not Paris-France; France's representative city is Abbeville.
## PRODUCTION READINESS (2026-09-21)

### Self-healing watchdog (watchdog.py)
Runs continuously and owns the stack:
- backend  : HTTP health check on :8787 every 20 s -> respawns `python server.py 8787` if down
- tunnel   : probes the current trycloudflare URL -> hard-restarts cloudflared if wedged
             (kills stale process, fresh quick tunnel; the URL ROTATES on every restart)
- backups  : once per day, copies data/tier2.json (+ queue/flags/supplement/overrides) to
             backups/tier2-YYYY-MM-DD.json + .sha256 manifest; keeps the newest 30 snapshots
Logs: watchdog.log (same dir).

### Startup (Task Scheduler — registered)
- Task "PrayerTimesHost Watchdog": `pythonw.exe watchdog.py` every minute (schtasks /sc minute /mo 1)
  -> after a reboot, backend + tunnel return within ~1 min of your first logon. No manual step.
- NOTE: `onlogon`/`onstart` triggers could NOT be registered (schtasks: "Access is denied" for this
  user context); the per-minute recurrence is the mechanism instead. It is user-scoped, so it
  starts at first logon after boot — acceptable for this machine.
- Manual fallback: start_prayer_times.bat (starts watchdog + prints health/URL).
- To remove the automation: schtasks /delete /tn "PrayerTimesHost Watchdog" /f

### Crash verification performed (2026-09-21)
- Killed the backend (taskkill /PID) and all cloudflared processes while the watchdog ran:
  both respawned within 60 s; /api/health passed on localhost AND the new public URL;
  new tunnel URL logged (rotates by design at this stage).
- Daily backup verified: backups/tier2-2026-09-21.json sha256 matches its .sha256 manifest
  (4 Tier-2 rows at snapshot time).

### RESTORE DRILL — RESTORE-VERIFIED (2026-09-21), distinct from checksum-only
Performed end-to-end, not just checksummed:
1. live data/tier2.json renamed away (loss simulated) -> /api/params for New York immediately
   degraded to tier1 {"ishaAngle":17.0} (tier2 entries gone) — loss provable, not assumed
2. cp backups/tier2-2026-09-21.json -> data/tier2.json ; simulator file removed
3. backend restarted via watchdog (server PID killed, watchdog respawned it)
4. /api/params?lat=40.7128&lng=-74.0060 returned tier2 with the exact stored values
   (fajrAngle 17.9, sunriseExtraMin -2, maghribExtraMin 2, ishaAngle 17.0) — NOT baseline;
   /api/status shows all 4 tier2 clusters back
5. sha256 of restored live file == backup sha256 == c6ab4bdf... (identical to pre-drill)
STATUS: RESTORE-VERIFIED.

### REAL-DEVICE REGRESSION — AWAITING USER'S PHONE (2026-09-21)
The update-button / geolocation / PWA checks have only been exercised in a sandboxed
browser (which cannot grant real geolocation). A staged checklist for a real phone is
below; each item stays NOT TESTED until run on a physical device.
1. Open current tunnel URL (check tunnel.log) on phone browser
2. Add to Home Screen -> opens standalone, no browser chrome
3. Geolocation permission prompt appears; granting returns a real position (not denial)
4. Place name renders (not stuck on coordinates)
5. Tap "update" -> visible "updating..." -> new result
6. Times on screen look sane for the real location
7. Submit "this looks off" -> hits the backend (/api/status flags/queue increments)
8. Airplane mode -> relaunch from home-screen icon -> shell opens, times compute offline
No sandbox rerun is accepted as a substitute for these.

## KNOWN PENDING ITEM (deliberately NOT done — paused on user)
- Stable subdomain: named Cloudflare Tunnel + domain purchase at Cloudflare Registrar
  (.com $10.46/yr quote) is PAUSED: requires the user's interactive `cloudflared tunnel login`
  (Authorize click) + billing step at dash.cloudflare.com. Until then the service rides on a
  trycloudflare QUICK TUNNEL whose URL rotates on every tunnel restart — anyone who installed
  the app from the old URL must RE-ADD the shortcut when the URL changes. Current URL at the
  time of writing: https://largest-bunny-bedrooms-rolls.trycloudflare.com (check tunnel.log
  after any restart).

## SHARED-CALIBRATION LOOP (2026-09-21)

### Auto-verification on first visit
GET /api/params for a cluster with no Tier-2 data: first-ever visitor marks it in
data/seen_clusters.json and enqueues a verification job (same worker/solve as /api/flag;
lock-guarded so concurrent first hits cannot double-enqueue). Response gains
"verificationQueued": true/false. Already-seen clusters never re-enqueue — including
after a REJECTED verification (seen record is separate from tier2 outcomes).

### Daily verification budget (sustainability cap)
- Default 200 verified-executions/day against salatcalendar.com, shared by auto-queue and
  /api/flag alike. Config: edit data/config.json -> {"daily_verify_budget": N} (server
  reads it live, no restart needed).
- When exhausted, new jobs queue FIFO and wait for the next day (or a raised cap) — nothing
  is dropped. Current usage visible at /api/status (budget.used_today / remaining + queue_depth,
  tier2_count, seen_clusters).

### Measured turnaround (real timings, 2026-09-21)
Queued clusters observed enqueue->tier2 (wall clock, 3-10s poll resolution):
- Reading UK (solo)   : 36 s   (poll granularity; worker likely ~26-29 s)
- Tokyo (solo)        : <= 20 s after budget release
- Manila              : 37 s   (waited FIFO behind Tokyo)
- Rome                : ~50 s  (waited behind 2 others at a busy moment)
Range: 20-50 s depending on queue position; solo jobs 20-40 s. Client "queued" badge copy
uses "usually ready within ~1 minute" (real bounded number, no guessing).

### Current state (end of this pass)
tier2 = 10 clusters, queue = 0, seen = 6, budget used today = 6/200.
New Tier-2 mem­bers this pass: Reading/Guildford-area UK (51.45,-0.97), Tokyo (Andong, kr),
Manila (Abucay, ph), Marseille (Monaco, mc), Canterbury (London, GB), Rome (Vatican City, va).

## FUNCTIONAL GAPS CLOSED (2026-09-21)

### A — Real location fallback (no more silent Canterbury)
- Geolocation denial/failure now shows "Couldn't get your location" + a Nominatim city-search
  (forward endpoint, AbortController 4s, top 5 matches; same pattern as reverse geocoding).
  Picking a result sets lat/lng and proceeds exactly like a successful geolocation would.
- Same city search added to Settings above the raw lat/lng fields.
- Canterbury constant remains ONLY as a last resort (no geolocation AND no network), and is
  visibly labeled "(fallback — network unavailable)" in the location pill — never presented
  as the user's real location.

### B — Prayer-time notifications
- Settings toggle "Notify me at prayer times" (off by default, permission requested on enable).
  Three-way graceful behavior, verified live: notifications unsupported -> toggle disabled
  with reason; Web Push unavailable (e.g. non-installed PWA tab) -> local-only mode with a
  one-line explanation; fully supported (installed PWA, browser with PushManager) -> Web Push.
- Local mode: while the app/tab is open, each prayer time fires a Notification (30 s poll,
  ­90 s window, per-day+prayer dedupe).
- Web Push (background delivery when app is closed): server generates/stores VAPID keys
  (data/vapid.json), endpoints /api/push/vapid, /api/push/subscribe, /api/push/unsubscribe
  (subscriptions in data/push_subs.json, keyed by endpoint only — no identity), and a push
  scheduler that recomputes each subscriber's prayer times with the same engine + tier params
  the app uses (spot-checked vs the site: Khor Fakkan 04:46/06:01/12:10/15:34/18:14/19:29 —
  within a minute) and sends at each prayer time. SENT PATH verified end-to-end up to the
  push service (VAPID-signed, aes128gcm-encrypted POST observed hitting a live endpoint that
  answered 405). REAL delivery to a device still needs an installed-PWA subscription from the
  user's phone (device-level verification pending — same category as the phone regression pass).
- privacy: subscriptions carry only routing keys + the location used for computing the user's
  times; no name, no account.

### C — OSM attribution
- Settings footer: "Location search by OpenStreetMap contributors (ODbL)" linking to
  https://www.openstreetmap.org/copyright — covers both reverse geocoding and forward search.

### Verification observed (this pass)
- Denial -> search UI appears (no silent fallback); "Sharjah Emirate, United Arab Emirates"
  picked from real Nominatim results -> times computed (AE params), badge went 'queued'
  (cluster was fresh -> auto-verification), later 'verified'.
- Settings search: "Abu Dhabi" -> 24.4538, 54.3774 filled.
- Notification toggle: granted on this Chrome; PushManager present but no installed SW in the
  sandbox -> local-only note shown verbatim ("On — while the app is open. (Background delivery
  wasn't available this time.)"). Unsupported-device path: toggle stays off with the reason.
- Push endpoints: subscribe round-trip (1 sub stored -> 0 after unsubscribe), scheduler
  computes today's times, send attempt completed over HTTP (405 from example.com = full
  client pipeline working).

### Two-stage geolocation (indoor-reliability fix, 2026-09-21)
- tryGeolocation() replaces the single getCurrentPosition call in BOTH the initial-load path
  and the "update" button: Stage 1 precise (enableHighAccuracy:true, timeout 12s, maximumAge 0)
  -> on failure Stage 2 coarse (enableHighAccuracy:false, timeout 15s, maximumAge 300000) ->
  only if BOTH fail does the flow fall to city search (load path) or "couldn't refresh" (update
  button). Stage messages: "Getting a precise location…" -> "Trying an approximate location…"
  (update button shows 'updating…' -> 'trying approximate…').
- Coarse fixes may lack altitude — the elevation field just stays 0/edited manually (unchanged).
- Verified live in-browser with instrumented geolocation responses: indoor-equivalent
  (precise timeout -> coarse success) resolved via exactly 2 calls with the coarse options
  and both messages observed; outdoor-equivalent (precise success) used exactly 1 call, no
  stage-2 message, altitude auto-filled. (Physical GPS behavior itself remains device-dependent;
  the app now handles the reported indoor precise-timeout case by falling to the coarse fix.)

### Auto-refresh on queued verification (2026-09-21)
- When /api/params resolves to 'queued' (tier1 + verificationQueued), the client polls the
  same lat/lng every 15 s (max 8 attempts ~2 min; backend 'seen' semantics mean subsequent
  hits report verificationQueued:false, so the poll tracks source tier1->tier2).
- On tier2: stops polling, badge upgrades to 'verified' with relative verifiedAt detail, and
  re-renders with the now-authoritative params (tier2 may differ slightly from the tier1
  estimate). Cap reached: badge stays 'queued' - no error, no misrepresentation.
- Any new loadServerParams call (location change, manual entry, recalc) invalidates stale
  polls via queuePollToken, so a poll from an old location can never write over the new one.
- Observed live (Bristol UK, fresh cluster, budget-gated): queued -> polled every 15 s while
  waiting (0 interaction) -> auto-flipped to 'verified' after the worker completed; 4 total
  /api/params calls (1 initial + 3 polls), ZERO after the flip (20 s quiet window). Mid-poll
  switch test: Cardiff queued -> switched to a verified cluster -> no stale poll fired at the
  next tick (call accounting: 6 = 1 Bristol + 3 Bristol polls + 1 Cardiff + 1 new-cluster probe).

## 2026-09-21 — Exact tier (opt-in, cached real tables)
- New tier in /api/params: ?date=YYYY-MM-DD served from data/exact_tables.json when the cluster+date row exists -> source:'exact' (no local formula for those fields at all). No date param -> previous tier2/tier1 logic unchanged.
- Marking: Settings -> "Get exact times for this location" -> POST /api/exact/request {lat,lng[,replace]} -> persistent data/exact_marked.json; cap 10 (409 + clear message; replace:'oldest' or '<cluster>' offered). NOTE: replacing a location DELETES that location's stored table (intentional — predictable, re-backfillable).
- Backfill worker (exact.py, thread in server.py): named-city path (CityTable + supplements, same as census/Tier2), today-first order (today -> back to Jan 1 -> forward to Dec 31), then next year once the window [today..Dec 31] has <30 days uncovered (renewal, logged "renewal: <cluster> -> year <year+1>"), so backfill never lapses into year end. Rows carry fetchedAt.
- Budget: SEPARATE daily cap data/config.json "daily_exact_budget" (default 50, live-read), persisted data/exact_budget.json {date,used}; independent of the Tier-2 verify budget (200) — /api/status exposes both.
- Client: badge state 'exact' (label "Exact — real IACAD times, not calculated", GOLD dot #f0b429, distinct from verified teal); /api/params responses with source:exact populate window.__exactTimes (persisted localStorage pt_exact_<cluster>) and render() uses the stored row for that date key (decimal hours) instead of computeTimes; day flips re-resolve via loadServerParams(lat,lng) which derives the current date; out-of-cache dates degrade to verified/estimated/queued/offline normally. Settings shows live progress (e.g. "142/365 days ready (backfilling year 2026)") refreshed every 20 s.
- Observed this session: byte-identical row (Khor Fakkan 2026-09-21 stored 04:46 06:01 12:10 15:34 18:14 19:29 == live salatcalendar.com fetch, all 6); both budget counters moved independently (exact consumed its own cap while tier2 stayed 14/200); cap 409 message + replace-oldest verified in UI; renewal triggered automatically (window seeded to 20 uncovered -> year flip 2026->2027 logged, 2027-01-01..04 fetched); out-of-range date fell back to verified; gold dot computed rgb(240,180,41).
- State at handoff: tier2 = 18 clusters, exact marked = 1 (Khor Fakkan), exact rows = 3 (today + 2 days prior; dummies from renewal test scrubbed), exact budget 50/day resetting next midnight; stable-subdomain item still the one known-pending item (user-side).
- Current tunnel URL (rotates on each restart): https://awards-anyway-some-informational.trycloudflare.com — re-add shortcut if it ever changes. Observed: heavy local CPU load (zip build) can slow /api/health past the watchdog probe timeout -> transient tunnel respawn churn (4 respawns in 35s on 2026-09-21 16:58); stops once load clears. URL for the live instance is always the one in tunnel.log.

## 2026-09-21 — Professional visual redesign ("kids app" -> instrument)
- Fonts: Google Fonts -> IBM Plex Sans (display/body) + IBM Plex Mono (all numerals: hero countdown, prayer times, greg date, qibla figures) — tabular instrument look; --font-mono token added.
- Color: full token swap to ink/bronze (dark: text #EDE6D8, accent #B8935A/#C9A876/#accent-soft 0.14, line rgba(237,230,216,.14), card 0.05/0.08, body-fallback #0B1420; light: accent #8A6534 etc.). skyGradient desaturated to muted ink/bronze bands; pre-JS body fallback + theme-color meta updated to match. Zero cyan/teal/amber values remain anywhere (grep-verified: #5FD6DA/#3FB8BD/#E3B341/#f0b429/#FFE7B0 = 0; hardcoded #fff = 0; only inline color = OSM link var(--text-muted)).
- Glow/pulse removed: 3 drop-shadows (arc fill/dot, needle) and all box-shadow glows gone, @keyframes badgePulse deleted (file-wide grep = 0). Zero decorative animation left (only functional transitions: sky fade 2.5s, needle 0.4s).
- Geometry: all pills -> 6px radius (loc pill, date strip, prayer cards, settings inputs, apply/status/live buttons, city-search input+results); circles (nav arrows, icon buttons, dial, date arrows) intentionally kept circular.
- Status badge: one accent, filled-vs-outline semantics — neutral hollow default (1px ivory ring), verified = filled accent, estimated = accent outline, offline = faint ring, queued = dashed accent ring (pending, no own hue), exact = filled accent + 2px ring (distinguished from verified by the ring, not a new color); no pulse anywhere.
- Active prayer card: 2px solid accent left border + accent-soft fill (structural marker, no glow).
- Mosque banner: cartoon mosque/palms SVG replaced with an abstract 8-point star lattice (4 large + 3 small line-art khatam stars, stroke-only in var(--mosque-fill), thin connector grid) — generated, no illustration.
- NOTE: the promised "reference copy with the redesign already implemented" was never actually received (checked documents cache + session DB; only the original kid-style doc_0428b5f159f3 exists). Part A font tokens and Part D star SVG were therefore implemented from the brief's own stated principles (IBM Plex fonts confirmed by name in Part E; "abstract 8-point star lattice" built as line-art khatam). Parts B/C/E applied verbatim from the brief's given values.
- Verified in browser (computed styles): body bg = muted ink gradient rgb(10,15,22)->..., font IBM Plex Sans, cards 6px, badge dot 6px/1px border, active card 2px accent left border, ptime/hero/qibla numerals IBM Plex Mono, dial 50%, exact progress + settings all functional. Screenshot saved; vision AI unavailable on this box so the "calmer" verdict is the user's to make on a device.
- State unchanged: tier2 18, exact marked 1 (3 rows), budgets 200/50.

## 2026-09-21 — Font link + star pattern corrected to exact spec (doc_50b80fc138bb)
- PART A font link: replaced with the brief's exact string (IBM Plex Mono:wght@500;600;700 first, IBM Plex Sans:wght@400;500;600;700) — verified char-for-char.
- PART D: polyline star lattice replaced with the brief's exact markup — <symbol id="star8"> (two crossing squares = 8-point star) + ten <use href="#star8"> in two opacity groups (mosque-fill lattice 0.22, central accent star 0.5) + baseline <line> — zero <polyline> (grep-verified 10 use / 1 symbol / 0 polyline).
- Genuine bold: hero-count, .pcard .ptime, .apply-btn set to font-weight:700 (700 was the missing weight in the old link); verified in-browser that the 700 mono + 700 sans faces load from the new link (document.fonts.load -> 'IBM Plex Mono w700' / 'IBM Plex Sans w700'; check('700 1em ...') = True for both; date strip 500 face also loads). Computed weights on the trio: 700/700/700.
- No substitution this round: both blocks are the brief's exact markup, applied verbatim (whitespace normalized to file indent only).

## 2026-09-21 — Accessibility/sizing pass (doc_2e9f03e5ebeb) — all 40 value reps applied
- Root: --line 0.14->0.24, --text-muted 0.58->0.75 (dark theme only, as specified). Light theme untouched (#171310 / 0.60 both blocks intact).
- Top bar: brand -> var(--font-body) + letter-spacing 0.01em; icon-btn 34->42px, bg rgba(237,230,216,0.06), 17px.
- Hero: arc 230x122; label 14px/0.75; name 26px; count 46px w600 sans (back to --font-display per spec, ls 0); time 15px/0.78.
- loc-pill 15px / rgba(237,230,216,0.92) (layout props untouched); pill button 15px.
- Badge: centered, 14px, margin -4px auto 14px, padding 0 16px; dot 9px, border 1.5px rgba(...,0.5); checking = 0.5; offline 0.32; verified text 0.9; state dots carried over (exact ring at 2.5px — Chrome snaps to 2px on 1x DPR, fine on phones).
- Date strip: buttons 38px/17px/bg 0.08; greg 18px w600 body; hijri 13.5px.
- Cards: padding 14/15; active left border 3px; dim 0.7 (was 0.55); icon 19px muted; pname 15px; par 13.5/400; ptime 32px w600 sans.
- panel-title 19px body; dial ticks 0.5; labels 13px/0.8; needle 2.5; qibla val 24px; lbl 13px; live-btn 15px/11x20.
- Settings: labels 14.5px; inputs 16px/12x13/bg 0.06; apply 16px/14px pad w600; note 13.5px lh1.6; city-search input + notify toggle matched to field values (16px/12x13/0.06 bg).
- Status card p 16px lh1.6; button 16px/13x24; nav 13px gap4 pad 6x18 color 0.62, svg 24px; #app pb 96px.
- Verify: 40/40 reps applied; every new value grep-present; old 0.14/0.58 zero remaining; light-mode contrast + star8 symbol (10 uses) intact. Browser computed: hero-count 46px w600, ptime 32px w600, badge dot 9px, gear 42px, arrows 38px, cards 6px radius. On-phone readability = user's check.

## 2026-09-21 — LIGHT-MODE CONTRAST FIX (re-send doc_1bec8652c4f0) — isolated, verified
- Both light-mode blocks replaced with the minimal 2-token version:
  @media (prefers-color-scheme: light) and :root[data-theme="light"] now set ONLY
  --card: rgba(255,255,255,0.07) and --card-strong: rgba(255,255,255,0.10).
  All other tokens (--line, --text, --text-muted, --accent, --mosque-fill) fall back
  to the dark :root values -> text stays ivory #EDE6D8 on the always-dark sky gradient.
  Dark --line 0.24 / --text-muted 0.75 (accessibility pass) untouched.
- VERIFY: grep -c 171310 = 1 (line 201, .apply-btn color:#171310 — dark text on gold button, intentional).
  Both light blocks: 0 occurrences (regex-scanned).
  Rendered with prefers-color-scheme: light ACTIVE in browser (matchMedia().matches = true, no data-theme):
  computed --text #EDE6D8, --accent #B8935A (falls back from dark root now), cards 0.07,
  hero text rgb(237,230,216) on gradient rgb(13,18,26) — light-on-dark, legible.
  Screenshot saved light-mode-proof.png in project root.

## 2026-09-21 — Improvement pass (doc_b5e8464f318c): A/B/C/D applied, E wired, F scoped
A) TIMEZONE — /api/params now returns IANA timezone (timezonefinder 9.0.0, ~pure-offline, lazy-loaded + cluster cache) on tier1/tier2/exact responses. Client stores state.timezone and computes with tzOffsetForZone(date, zone) (Intl, DST-correct) via effTz(); phone-local offset remains the offline/no-response fallback. First-apply render fixed (params handler re-renders after storing timezone). VERIFIED: from a UTC+4 phone, Tokyo showed 04:03 05:27 11:35 15:03 17:41 19:06 JST on FIRST apply (sunrise 05:27 vs actual 05:24); before the fix the same point displayed JST-5h (00:2x sunrise). London dec returns Europe/London (DST handled by Intl).
B) HIJRI — toHijriUmAlQura (Intl 'en-u-ca-islamic-umalqura') replaces the tabular toHijri at the display call site; toHijri kept as the try/catch fallback. VERIFIED vs aladhan gToH (Umm al-Qura/HJCoSA): today 10 Rabi' al-thani 1448 = 10 (was 8, the reported bug); 2026-09-30 -> 19 = 19; 2026-10-01 -> 20 = 20; month name maps HIJRI_MONTHS[month-1] correctly.
C) ROUNDING — analysed 140 census rows vs 1:1-ported app engine raw values (per-row country override params; port validated to <=1 min on the Khor Fakkan row: 303/377/744/948/1108/1182 vs site 303/378/744/949/1107/1182). Per-prayer agreement: fajr nearest 91.0% (only >90% cell), sunrise floor 2.9/ceil 42.9/nearest 4.3 (systematic ~1 min model residual, no rule), dhuhr 10/10/18.6, asr 21.4/62.9/65.0, maghrib floor 49.3, isha 26.1/31.3/49.3. CONCLUSION: no consistent per-prayer asymmetric rounding rule above the 90% bar; residual is the known +/-1 min calibration noise; nearest-minute rounding kept. No code change (per brief: confirm before applying).
D) SECURITY — /api/exact/request now requires the exact_api_token from data/config.json (X-Exact-Token header or body token; 32-hex, auto-generated on first need); served into the client at serve-time (index.html literal placeholder replaced per request) with the limitation that the token is extractable from the served app (raises the bar from "anyone with the URL" to "anyone who reads the app source"; real protection = budget caps + limits). In-memory sliding-window rate limits: /api/flag 30/h/IP, /api/push/subscribe 60/h/IP. VERIFIED: no token -> 401 + nothing enqueued/marked; wrong token -> 401; correct -> 200 (marked 1->2 only on the authorized call; test entries cleaned); 35-flag hammer -> exactly 30x200 then 429s.
E) OFFSITE BACKUP — watchdog now pushes tier2 backup + sha256 + exact_tables/exact_marked/config to hermes@192.168.1.188:/home/hermes/prayer-backups via pscp after the daily backup, with an all-day idempotent retry (marker data/offsite_pushed.date) that runs every loop cycle until the node answers; failure is logged, never fatal. OBSERVED: with all candidate nodes down (Pi4 192.168.1.188, estate 192.168.1.23, OmniRoute 100.122.30.80 — none pingable/port-22 closed at test time), the watchdog logs "offsite backup: mkdir failed rc=1 ... Connection timed out" every ~21s and keeps retrying. ACTUAL LANDING on the second machine could NOT be demonstrated today — no second node reachable; will land automatically the next cycle any node is up. Needs the user's side confirmation when the fleet is back.
F) ARABIC/RTL — plan delivered in the report; not built.
State: tier2 18, queue 0, marked 1, budgets 200/50; config.json now carries exact_api_token (also shipped in the offsite payload — same trust domain).

## 2026-09-21 — Part F: Arabic / RTL (doc_a6979cc412c9)
- Locale: detectLang() = Intl.DateTimeFormat().resolvedOptions().locale (navigator.language fallback); /^ar/i -> Arabic+rtl else English; manual override select in Settings (auto/en/ar) -> localStorage pt_lang, always wins once set; auto = detection.
- ~50 strings in L dict (en/ar): brand, status/permission copy, city-search, hero, nav (times/qibla/settings), panel titles, settings labels incl. the long method note + ASR/flag/notify/exact blocks, badge states, stage messages (getting precise/trying approximate/retry/updating/locatting), notify/flag/exact feedback; data-i18n attributes on statics; placeholders via applyLang.
- render(): greg via Intl 'ar' locale; Hijri via Intl 'ar-u-ca-islamic-umalqura' long format (Arabic months + Arabic-Indic digits + هـ) with the EN tabular fallback; prayer-card names swap (ar primary / en secondary in AR mode); hero name + Today/Tomorrow lines localized.
- applyLang() sets <html lang/dir> and re-renders; called at init after all consts (TDZ-safe) and on select change.
- VERIFIED (browser, real locale override via CDP Emulation.setLocaleOverride ar-AE): auto-open Arabic with dir=rtl, no toggle — brand أوقات الصلاة, hero الوقت المتبقي حتى, hijri "10 ربيع الآخر 1448 هـ" (matches the Umm al-Qura value from Part B), greg الاثنين 21 سبتمبر 2026, prayer names الفجر..العشاء, badge دقيق — أوقات حقيقية. English-locale default stays en/ltr. Manual override: en->reload->en persisted; ar->reload->ar persisted; auto->back to detected ar. (CDP override resets on navigation — needed set-after-load + in-place reload.)

## 2026-09-21 — "Verified but wrong times" bug (doc_ecea3e6384e5) — root-caused, two fixes
ROOT CAUSE (reproduced, not guessed): the exact-tier client cache was keyed by DATE ONLY
(window.__exactTimes[dateKey]) — it leaked across location changes. Reproduced live:
viewing the exact-marked Khor Fakkan, then switching to Tokyo/Al-Ain on the same day
displayed KF's stored table under the new location indefinitely (badge honestly said
verified/tier2 for the new spot) — cleared only by reload, exactly the reported symptom.
SECONDARY (confirmed via code + probe): tier2 lookup radius was 10 km (CLUSTER_KM=10.0) —
10x the ~1 km cluster grain — serving a neighbour cluster's solved params as your own
"verified" for points up to 10 km away (probe: (25.34,56.35) 1.5 km -> Khor Fakkan
cluster pre-fix; post-fix -> own tier1 + queued).
FIXES:
1) Exact cache now keyed per cluster: __exactTimes[serverParamKey(lat,lng)][dateKey];
   localStorage keyed per cluster; render reads the current cluster's map only.
   Old cached shape is compatible (same inner {date:row} map under the one key that
   ever existed).
2) CLUSTER_KM = 10.0 -> 1.0.
3) Defense-in-depth: window.__serverParams (used for the country-params merge inside
   computeTimes) is cleared at every location-change entry (city search, manual entry,
   geo refresh, fallback) so no intermediate frame borrows a foreign cluster's params.
VERIFY (real observed):
- Reported sequence: KF exact (04:46 06:01 12:10 15:34 18:14 19:29) -> Tokyo -> FIRST
  render 04:03 05:27 11:35 15:03 17:41 19:06 (its own JST, was stuck on KF's) ->
  Al Ain -> 04:55 06:10 12:18 15:42 18:22 19:36 (its own). No wait/reload needed.
- 3+ rapid switches all correct on first render; far point (26.5,55.5) went through the
  honest queue->own-verification loop (queued -> verified with its own params);
  near point (0.2 km) still legitimately reuses/receives the cluster response.
- Reload after all changes: KF exact persists per-cluster, Tokyo unaffected.
- API evidence: pre-fix tier2_get(25.34,56.35) -> cluster 25.33,56.34; post-fix ->
  tier1+queued, then its own cluster 25.34,56.35 verified (the designed ~1-min loop).
tier2 count now 20 (two test points joined through the real loop), queue 0.

## 2026-09-21 — Qibla needle bug (doc_1d7d6de72e3d): h1 ruled out, h2 fixed
- Evidence h1 (CSS regression): selectors present (lines 179-181 .needle/.shaft/.kaaba); computed in a live render: stroke rgb(184,147,90)=--accent, stroke-width 2.5px, fill accent, bbox 14x86, transform rotate(258.94...) for KF. RULED OUT.
- Root cause (h2 class): once liveActive=true, updateQibla() stopped writing ANY transform and onOrient skipped on every missing/invalid heading — so in the toggle-on-but-no-valid-heading state (iOS Safari permission flow, events never arriving, or webkitCompassHeading/alpha both absent) the needle can hold no valid rotation — the empty dial in the screenshot. Combined with WebKit's known SVG attribute-transform + CSS transition/origin quirk it lands as "no needle at all".
- Fix: needle markup now starts with transform="rotate(0 100 100)"; updateQibla writes the static bearing whenever (NOT liveActive OR no heading received yet: liveHeading===null); onOrient never writes an invalid value — null/NaN heading or NaN rel falls back to the static computed bearing; first valid heading arms liveHeading.
- Verified (sandbox, real renders): static rotate(258.94...) visible; toggled live with no events -> needle REMAINS at static (the blank window case); synthetic heading=90 -> rotate(168.94...); event with no heading fields -> falls back to static; bbox intact throughout. Real-device (iOS) confirmation checklist handed to the user: fresh load -> needle at static; toggle live -> stays visible + rotates; deny permission -> stays at static.

## 2026-09-21 — Qibla follow-up (doc_0144c54d0a14): second writer REFUTED, on-device instrumentation shipped
- Code read (post-fix): updateQibla has exactly ONE transform write, inside the
  guard; whole file has exactly 3 write sites (1381 guarded static, 1427 static
  fallback in onOrient, 1440 live write in onOrient). render() is not on a 1s
  timer (the countdown tick() doesn't call render or updateQibla) -> the
  "re-render overwrites live rotation" hypothesis is ruled out in the current build.
- Shipped TEMPORARY debug line in the Qibla panel (id=qiblaDbg, mono 11px, muted):
  "DBG: <note> | events: N | heading: X.X | rel: Y.Y | transform: ..." updated by
  onOrient (both branches), setupLiveCompass (listener-attach timestamp), and
  updateQibla. Sandbox-validated: static shows transform at bearing; synthetic
  heading 180 -> events/heading/rel/transform all update (rotate 78.9).
- Decision tree for the next real-device run: (a) events stay 0 after grant ->
  listener-attach failure; (b) events>0 + heading changes + needle static ->
  WebKit SVG attribute-transform + CSS transition paint quirk -> remove the CSS
  transition/transform-origin on the needle <g> or drive style.transform;
  (c) heading INVALID/constant -> heading-source handling. Fix only the branch
  the device evidence lands on; remove the debug line after.

## 2026-09-22 — Qibla cleanup + smoothing (doc_2e567a1b41d6)
- Debug line fully removed (grep qiblaDbg/DBG: = 0; braces 700/700). All needle-driving logic
  kept untouched (transforms, liveActive/liveHeading, static fallback).
- smoothHeading() added (brief verbatim): circular moving average, window 6, handles 0/360 wrap.
  onOrient now uses the smoothed value for BOTH rel and liveHeading. headingHistory resets on
  every button press; duplicate-listener guard added (tapping again while armed no longer
  re-attaches listeners, which would have double-fed the smoothing window).
  NOTE: first pass left rel = fixAngle(bearing - heading) (raw) — caught in test (needle tracked
  raw heading exactly), fixed to bearing - smooth; re-verified.
- Verified in sandbox: wrap series 350,355,5,10 -> smooth ends at 0 (transform 258.94, not a
  jump) — wraparound correct; jitter 88-92 around 90 -> converges ~90 (transform 168.94);
  single 90->180 jump -> damped intermediate (transform 157.63, history-biased step), not a
  full snap. Double-tap: listeners not duplicated, smoothing single-step.
- Real-device feel check (steadier when held still, still responsive) = next device test.

## 2026-09-22 — Needle missing again: SW staleness (structural) + showPanel hardening (doc_ff5513c43340)
- H1 (stale SW) — mechanism CONFIRMED structurally: sw.js was cache-first with NO
  revalidation — `fromCache.then((cached) => cached || fromNetwork)` returns the first-
  ever-cached shell for any plain navigation (cache.match has no freshness check; CACHE_NAME
  static 'v1'). Any returning visitor on the same URL kept the ORIGINAL build indefinitely;
  my probing only worked because ?probe queries miss cache.match. NOTE: the live stale-serve
  demo could NOT be reproduced in the sandbox — harness engine (lightpanda) exposes no
  CacheStorage API, so every navigation fetched fresh; the mechanism is a code fact, and the
  definitive proof is the user-side test (clear site data -> reload).
- FIX: sw.js now network-first with offline fallback (live fetch always, cache only as the
  offline fallback) + CACHE_NAME bumped v2 (activate handler evicts v1 caches). Next plain
  navigation on the device updates sw.js, evicts v1, and serves the current build.
- H2 (updateQibla only from render) — CONFIRMED by read (single call site at line inside
  render(); showPanel only toggles hidden). Hardened: showPanel('qibla') now calls
  updateQibla() so the needle (re)draws on every tab show; updateQibla gained a null-
  location early return so the fresh-load-no-location case can't write a NaN transform.
- Verified (sandbox, fresh load after SW unregister attempt): qibla direct open with no
  location -> rotate(0 100 100) default preserved, digits '--' (guard works); 3x tab flips
  stable; located -> rotate(258.94...) on tab open; live smoothed synthetic ~90 -> 169.54
  (circular mean of the 85..92 series = 89.4 -> rel 169.5, exact).
- Remaining device-side confirmations for the user: (a) with the old build active, clear
  site data / uninstall PWA -> needle appears (confirms H1 was the trigger); (b) next
  navigation after this deploy should update by itself (network-first).

## 2026-09-22 — sw.js one-line bug: url.path -> url.pathname (doc_db11dd01405a)
- CONFIRMED: URL objects have no .path — url.path is undefined; the guard threw
  TypeError('startsWith') on EVERY fetch event before respondWith, silently killing
  caching AND the offline shell fallback (worked online only because the browser's
  default networking covered it). This has been broken since the original sw.js
  (same line in v1) — the earlier "offline confirmed" round predates the regression.
- FIX: url.pathname. In-browser proof: old line throws on both /api/ and /index.html;
  new line BYPASSES /api/ and REACHES respondWith for the shell (network-first caching
  intact, offline fallback alive).
- Airplane-mode regression test: cannot be run in this sandbox (harness engine has no
  CacheStorage; needs a real device). Manual procedure for the user: load online once
  (shell caches), enable airplane mode, relaunch from home-screen icon — app shell +
  local computation must work; then back online: normal loads + network-first updates.

## 2026-09-22 — WebKit transform fix applied at all 3 sites (doc_65074aef8ff2)
- All needle rotation now drives CSS style.transform ('rotate(Xdeg)') instead of the
  SVG transform attribute (WebKit repaint quirk: attribute + CSS transition can fail to
  repaint after change). Sites: updateQibla static write, onOrient static fallback,
  onOrient live write. CSS transform-origin (100px 100px) kept as-is per the brief.
- EVIDENCE (fresh rendered page, no prior interaction): computed
  getComputedStyle(needle).transform = matrix(-0.191822,-0.98143,0.98143,-0.191822,0,0)
  = exact cos/sin of the 258.94 deg static bearing; no-location state -> 'none' (honest,
  no NaN/identity jank); live synthetic ~90 deg -> matrix(-0.98143,0.191822,...) =
  cos/sin of 168.9 deg. Painted confirmation: screenshot pixel analysis found 355 pixels
  of the accent color (184,147,90) inside the dial box, farthest extent 92px from dial
  center = needle shaft (74) + kaaba corner (~86) geometry exactly.
- Honest limits: (a) the sandbox engine painted BOTH attribute and style variants, so it
  cannot reproduce the Safari-specific repaint failure; the fix is validated for
  mechanics + painting, and the Safari verdict is the real-device step; (b) tip-angle
  pixel bookkeeping had ~6-10 deg noise from kaaba-corner vs shaft-axis geometry -
  not a rendering defect (matrix values are exact).

## 2026-09-22 — Compass-rose UX (doc_36c7b7d9c5e7): dial rotates with the phone
- Markup: ticks, N/E/S/W labels and the needle wrapped in <g id="compassRose">; the
  dial-ring circle stays outside. CSS: #compassRose gets the same transition +
  transform-origin (100px 100px) rule as .needle.
- Rotation split: onOrient (valid heading): rose.style.transform = 'rotate('+(-smooth)+'deg)'
  (smoothed heading reused once for both layers); needle = plain fixAngle(bearing) —
  heading component now supplied by the parent group (mathematically the same composite).
  Static mode: rose never touched (N at top, needle at bearing) — unchanged.
  Fallback paths untouched.
- EVIDENCE (computed readbacks): STATIC rose none / needle matrix(cos258.94,sin258.94)=
  exact. LIVE h=90: rose matrix(0,-1,1,0)=rotate(-90) EXACT; needle unchanged at bearing.
  LIVE h=0: rose identity; needle unchanged. Painted (screenshots qibla-rose-h0/h90.png):
  needle accent 355/338 px, moved ~-96 deg between orientations (spec -90; ~6 deg standing
  bias = kaaba-corner anchor offset at 265.3 deg + corner rounding); label cluster sits at
  top at h=0 and shifts left-down at h=90 (rose rotating). Real-device rotation shots = user.

## 2026-09-22 — Depth/polish pass (doc_458cb19bad44)
- NEEDLE: line+rect -> tapered filled shaft (path M100,100 L96,42 L100,26 L104,42 Z) + 3-face
  isometric Kaaba cube (kaaba-body accent; kaaba-top accent-strong 0.9; kaaba-side #171310 0.35).
  Old .shaft stroke / .kaaba rules removed.
- STATIC SHADOWS (no transition/animation/glow): pcard 0 3px 10px .28; active 0 3px 14px .35 +
  0 0 0 1px accent-soft; date-strip/loc-pill/dial-wrap/status-card 0 3px 10px .25; apply/status
  button/live-btn 0 2px 8px .3. grep animation = 0; only transform transitions exist.
- TEXTURE: body::before fixed layer, 120px 8-point-star tile (two crossed squares), stroke
  #EDE6D8 @0.035, z-index -1, pointer-events none. Active (computed background-image = uri).
- FONT DISCIPLINE: mono now ONLY .hero-count + .pcard .ptime (both IBM Plex Mono, computed);
  qibla .val -> --font-body w700 (IBM Plex Sans, computed); ZERO --font-display users remain.
- live-btn: accent-soft bg / accent border / accent-strong text, keeps 15px/11x20/6px.
- VERIFIED computed: pcard/active/date-strip/dial shadows exact values; qibla val IBM Plex Sans
  w700; shaft rgb(184,147,90); kaaba computed fills per spec; live-btn bg/border/color.
  PIXEL-PROVEN (qibla-polish.png): 420 accent px (bigger than the old 355 thin-line)
  pointing at exactly 168 deg (static composite 258.94); cube 3 tones measured in place:
  top (199,165,115) = accent-strong blend, side (128,103,65) = #171310@0.35 over accent.

## 2026-09-22 — Qibla screen full redesign (doc_8892265c1711)
- Full markup/CSS/JS swap per the spec (with the two stated departures: kept existing
  top bar; IBM Plex Sans/Mono instead of the serif). New compass 322x322 with bezel
  gradient, kaaba-glow needle (teal #2FD9AE shaft + dark hub + gold Kaaba cube), 56
  minor/major ticks, 8 degree labels, bearing block (44px mono number), info-card
  (distance + compass word + from/to), Live Qibla toggle with sliding switch.
- Kept the rotating-dial behavior: #compassRose rotates by -smoothedHeading (161,161
  origin now), needle at absolute bearing (style.transform, WebKit-safe) - verified
  matrices: static needle exact cos/sin(258.94); live rose rotate(-73/-81.5=smoothed
  circular mean) while needle stays locked at 258.94 (earlier identity reads were
  transition-timing artifacts - 0.4s ease vs 0.25s polls; settled reads exact).
- Ticks rebuilt by buildQiblaTicks (6deg minor/30deg major, cardinal slots skipped,
  labels only the 8 non-cardinal 30 multiples) - 56 lines + 8 texts verified.
- bearingToCompassWord verbatim per brief: Khor Fakkan 258.9 -> "West" (the brief's
  example said WNW, but the formula places the West/WNW boundary at 258.75, and
  258.94 sits 0.15deg past it - reported honestly, formula kept verbatim).
- from/to: currentPlaceLabel() reads the location-pill text node (strips the trailing
  "  .  " separator), falls back to plain "to Makkah" when no label -> shown clean:
  "Khor Fakkan, United Arab Emirates -> Makkah".
- Live toggle: #liveToggle click + Enter/Space; engage() reuses the exact
  permission/listener logic; toggleTrack.on matches liveActive incl. the denied
  snap-back path; re-engage resets headingHistory.
- RTL/AR verified: emulated ar-AE -> dir rtl, title/tagline/live labels all Arabic,
  info-card columns mirror (flex), toggle mirrors.
- Screenshots: qibla-redesign-ltr.png, qibla-redesign-rtl.png, qibla-live-proof.png.
- Honest notes: on-device screenshot still awaited for the final look; sandbox cannot
  exercise iOS orientation permission.

## 2026-09-22 — Cardinal/degree counter-rotation + fixed top marker (doc_1d98f76e10cc)
- N/E/S/W each wrapped in <g class="cardinal-wrap" data-cx data-cy>; degree numbers
  generated in buildQiblaTicks now wrap per-element in <g class="degnum-wrap" data-cx data-cy>
  (same x/y as the text coords, no extra stored attrs).
- counterRotateLabels(roseAngle) sets each wrap's transformOrigin to its own position and
  rotate(-roseAngle); called with 0 on the static write and -smooth on the live update —
  exactly the angle just applied to #compassRose.
- Top gold arrow <path> moved OUT of #compassRose to a direct svg child after the rose
  group; never receives any transform.
- VERIFIED (computed matrices): static rose none/N-wrap identity/arrow transform none
  bbox.y=10. Live h=90: rose matrix(0,-1,1,0), N-wrap matrix(0,1,-1,0) -> composite
  identity (upright), arrow still none at y=10. Live h=180: rose matrix(-1,0,0,-1),
  N+S wraps matrix(-1,0,0,-1) -> identity. Screenshots qibla-upright-h90.png and
  -h180.png. Real-device two-orientation screenshots remain the last confirmation step.

## 2026-09-23 — TV/laptop display product (doc_6873869a00c1) — NEW page, phone app untouched
- New standalone page app/tv/index.html, served at /tv/ on the SAME server+tunnel (one-line
  route alias in _serve_static; no second tunnel — less fragile). Phone app/index.html
  VERIFIED untouched: sha256 dc98f45f81... unchanged before/after. No backend API changes.
- Design markup/CSS from the brief verbatim (incl. Noto Kufi Arabic; mosque silhouette;
  gold theme); integration built: setup flow (localStorage tv_config; optional mosque name ->
  "Prayer Times"/"أوقات الصلاة" default; Nominatim city search, same pattern as phone;
  discreet gear re-entry), real data (fetch /api/params?date=today; EXACT rows displayed
  verbatim when source exact, else compute from returned params), countdown with real
  tomorrow-rollover (fetch tomorrow's /api/params after Isha; needsTomorrow flag),
  clock+dates+Hijri via Intl (same Umm al-Qura engine as phone; verified Sep 21=10/22=11/
  23=12 == aladhan), burn-in shift (5-min interval, +/-3px, 1.6s ease; verified: two nudges
  produce different offsets), Jumu'ah footer = Dhuhr on Fridays else "-- --" (backend has no
  Jumu'ah field - FLAGGED fallback), Sunrise footer from the day's times.
- Iqama: NOT added (brief allows; my flag: keep single times - the real-display evidence
  showed one time per prayer and mosque Iqama schedules are site-specific).
- Verified live: setup-first-run; search->save->dashboard; real panel times for Khor Fakkan
  Sep 23 == /api/params exact row (stable across reads); countdown target/count correct;
  rollover math uses tomorrow's REAL fajr (4.7939h vs today 4.7860h - not a copy);
  hijri values matched aladhan on every checked date; noon-full screenshots saved.
- Note: earlier apparent panel-value churn was the sandbox clock rolling past midnight mid-
  test (Sep 22 -> Sep 23) hitting two different real rows, not instability; two consecutive
  reads of the same date are byte-identical.

## 2026-09-23 — TV: missing-Isha / sunrise-in-table (doc_b45ea0ff1657)
- REPRODUCED before fixing (real /tv/ renders, 3 fresh reloads, seeded config):
  times.isha = 19.4333 (19:26) present EVERY run; prayerList returned 6 rows every run;
  row_texts 04:47,06:02,12:09,15:33,18:12,19:26. Root cause was NOT data/logic: the last
  row's rect bottom sat +70px BELOW the panel box on a 622px-tall viewport and
  .prayer-panel{overflow:hidden} clipped it — the user's photo showed the data-true 5-row
  paint. The earlier "Isha verified present" was true of the API data, false of the paint.
- FIX1: PRAYERS -> exactly 5 (fajr/dhuhr/asr/maghrib/isha); Sunrise only in the footer
  (still 06:02 from today's exact row). Countdown now goes Fajr->Dhuhr (sunrise no longer
  a next-prayer target) and still rolls Isha->tomorrow's real Fajr.
- FIX2: .prow min-height:46px so rows survive short screens; overflow no longer clips:
  verified last row bottom -1px INSIDE the panel on all 3 reloads, 5 rows listed.
- Maghrib 18:12 vs photo's 18:11: two reads moments apart while the worker refetched the
  row; the row has been byte-stable since (multiple identical reads) - not a compute
  inconsistency.
- New screenshot tv-five-prayers.png (5 rows incl. Isha).

## 2026-09-23 — TV alignment audit (doc_80567720a031): 2 of 3 hypotheses NOT real, 1 fixed
- Method: live /tv/ renders + getBoundingClientRect + SCREENSHOT ink analysis (no blind CSS).
- H1 (times floating off the divider): NOT REAL — measured ptime right edge == divider x
  (gap 0px); names right-align to the panel edge per the original design. The 1fr column
  shrink-to-content means the time always meets the divider. No change.
- H2 (Arabic/Latin baseline mismatch): NOT REAL — pixel-ink analysis of the hero pair on
  the rendered page: AR ink rows 339-399 (center 368), EN ink 348-384 (center 368) —
  both visual centers exactly y=368. The 110px vs 62px box heights distribute the glyphs
  proportionally inside flex-centered boxes. No change.
- H3 (clock off-center): REAL — clock cx=800 vs header center 599 (201px off, the
  `1fr auto auto` grid strands clock+date right). FIXED: header grid -> `1fr auto 1fr`;
  measured after: cx=629 (30px residual = side-column content imbalance, fine at scale).
- BONUS: added kiosk provisioning ?cfg=lat,lng,label (also how the sandbox renders now
  that its profile blocks localStorage for this origin).
- Verified no regressions: 5 prayer rows intact, countdown ticking, next=Dhuhr correct.
- Screenshots: tv-align-before.png / tv-aligned.png.

## 2026-09-23 — TV dotted-zero fix (doc_fdd28bb6c233)
- Root: IBM Plex Mono's intentional dotted zero reads as a defect at display sizes.
  FIX (per brief): numerals switched from var(--mono) to var(--sans) (IBM Plex Sans) on
  .clock-time, .countdown, .ptime — plus font-variant-numeric:tabular-nums on each, and
  applied the same treatment to .footer-value (27px digits, same dotted-zero class).
- EVIDENCE: computed = IBM Plex Sans + tabular-nums on all four; prayer-list rows all
  start at x=835 (tabular alignment holds); SCREENSHOT ink analysis of the rendered
  countdown "00:06:59": the two zero glyphs show 4.6%/4.9% ink through their center
  strips (hollow ring; a dotted zero would be 25-40%) — colon calibrates at 6.9%.
- Phone app untouched (this pass touched only app/tv/index.html).
- Screenshot: tv-clean-zero.png.

## 2026-09-23 — iPad-size honesty + clearance fix (doc_d5303152b54b + doc_16787a1386de)
- OWNED: the earlier "iPad 1258x622" was NOT an iPad test — the engine refuses viewports
  below 1258x622 (resizeTo/setWindowBounds/setDeviceMetrics all clamp). REAL 1024x768
  achieved via a same-origin IFRAME (content viewport = exactly 1024x768, verified
  ifr_inner) — that is now THE method for sub-floor sizes here.
- Measured (direct ink scan; text bottom row -> first mosque stroke row):
  BEFORE: 1920=219px, 1440=109px, 1024real=58px, floor1258x622=-3px (art overlaps text)
  AFTER clamp(58px, 7.8vw, 148px) + margin-bottom:14px on .countdown:
  1920=219px (unchanged, cap keeps 148px), 1440=125px, 1024=69px/DOM gap 21px.
- The countdown font now scales at 7.8vw (was 9vw) — gives back clearance exactly where
  the squeeze was; TV cap untouched. Digits re-verified clean at the 80px iPad size.
- Screenshots: tv-A.png (1920), tv-B.png (1440), tv-C.png (1024 real, iframe method),
  plus tv-1024-real.png original.
- Conclusion: the phone-photo "numbers missing" artifact is NOT reproduced by any clean
  headless render at any tested size incl. real 1024x768; the real (subtle) defect was
  clearance collapse at short viewports, now fixed and measured.

## CURRENT STATE - both products (verified 2026-09-23)
Phone app (app/index.html): stable PWA; timezone-correct clock (Intl.DateTimeFormat with server timezone); tier2/verified/queued/estimated/exact badges; notifications; Qibla; Hijri; Arabic/RTL.
TV display (app/tv/index.html): finished for personal use; indicator (Exact/Verified/Estimated/Queued/Offline); timezone-correct countdown and prayer selection (zoneNowParts + Intl); header clipping fixed (@media 1100px); verified at 1920/1440/1024 (iframe); load-tested ~100 concurrent screens (free quick tunnel, 200 cap documented).
Excluded (deliberate): Iqama times, full-screen alert, phone-width layout. Open (deployment only): real-device testing, multi-week uptime, burn-in verification, stable subdomain, off-site backup landing.
Single paintAll verified (grep count = 1); no duplicate/dead code.
