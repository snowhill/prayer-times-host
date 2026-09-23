# Reusable patterns from this project

## Tiered-accuracy architecture (reusable)
Three-tier data design for any locally-computed value that needs authoritative confirmation:
- Tier 1 (baseline): pure local calculation from embedded/reference data (fast, always available)
- Tier 2 (verified): confirmed against an authoritative external source over time (poll/queue + budget cap)
- Tier 3 (exact): byte-identical real daily table for specific location+date (optional, separate budget)
Client reads server tier/status; badge label surfaces the full picture (verified/estimated/queued/exact/offline/checking) not a binary.

## Verification discipline that catches silent bugs
Every passing test in this project masked a real defect at some point: the missing prayer row (CSS overflow, only visible at small viewport sizes), the timezone fix that only reached the clock label (not countdown/next-prayer/Jumu'ah), the exact-tier cache leak (keyed by date only), the duplicate dead `paintAll`. What actually caught them:
- Read the real code (not just the description) before trusting a fix
- Reproduce with real evidence: code diff + measured bounding boxes (date overflow: before x=1040, after x=905) + real screenshots at all 3 viewport sizes + live data verification (London mismatch proves timezone fix, not just clock label)
- Never rely on a single passing check or a confident report; prefer showing before/after state directly

## Timezone-correct display (reusable for any location-configurable app)
Compute a single zone-adjusted "now" (`zoneNowParts`) once per tick using `Intl.DateTimeFormat({timeZone: zone})`, then thread `zp.hours/minutes/seconds/day` through every function that needs it (clock, countdown, which-prayer-is-next, day-of-week checks like Jumu'ah). Don't let each function independently decide whether to convert — that creates the "works by accident" failure mode where the display looks right but calculations stay wrong.

## Multi-viewport testing
The countdown clearance issue, the header date clipping, and the missing Isha row were all invisible at a single viewport size. Real testing requires at least: primary (TV/full), laptop, and genuine sub-floor (1024x768 via iframe — required here because viewport engines clamp below ~1258px).

## Load-testing with free tunnels
Free `trycloudflare.com` quick tunnels have a documented 200 concurrent in-flight request cap (HTTP 429 at limit). Load-testing before deployment gives a real practical ceiling (~100 concurrent screens observed clean) rather than assuming no limit exists.
