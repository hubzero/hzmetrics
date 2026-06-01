# Future work

Items deliberately deferred during the 2026-05 geodynamics catch-up.
None of these block the pipeline — it runs correctly today.  These are
shape-improving / cost-reducing changes for when the operator has
bandwidth to take them on.

## Web table retention

Today every rebuild tick re-derives counts from raw `web` rows.  The
table at geodynamics grows ~250 k–500 k rows/month at steady state
post-filter, so it will reach ~25 M rows by 2028 and ~50 M by 2032.
Nothing breaks at that scale, but scans get slower in linear
proportion.  Three complementary moves let us bound the growth.

### Routine: trim irrelevant rows after summarize

A `web` row is structurally counted by:

- `do_andmore_usage` iff its `content` matches one of the resource
  URL patterns (built from `jos_resources` aliases).
- `_summary_build_download_*` iff `dnload = 1`.

A row that matches NEITHER is dead weight — `logfix-session` already
extracted its session contribution into `websessions`, and summarize
will never query it again.  After a month is fully summarized, those
rows can be deleted with no downstream effect.

Expected reach on geodynamics: most `/resources/browse?…`,
`/citations/…`, `/groups/…`, `/support/…`, `/login`, `/register`,
`/about`, plus any imported bot-like noise the import-time filters
miss.  Measure the set size before designing the cron — could be
30–50 % of current `web`.

**Implementation sketch**

- Build a pattern table from `jos_resources` once per maintenance run
  (or query it live).
- Chunked DELETE of `web` rows where `dnload = 0` AND `content` matches
  NO pattern, only for months that are fully summarized AND not
  flagged dirty.
- Run as a separate command (`prune-web --strategy=irrelevant`), not a
  pipeline phase — keep the orchestrator's per-tick work predictable.

### Deep prune: snapshot tables + year-by-year delete

When the routine trim isn't enough (or when an operator wants to bound
the table by retention rather than content), the analytics paths that
read `web` need to be served from per-month aggregate snapshots
instead.

**Snapshot tables**

```
web_resource_users(month, resid, ip, host)
  -- one row per distinct (ip, host) that hit resource `resid` in `month`
  -- PK (resid, month, ip, host); INDEX (month, resid)

web_dnload_users(month, ip, host, ipcountry)
  -- one row per distinct downloader per month

web_dnload_sessions(month, sessionid)
  -- one row per distinct download session per month
```

Estimated size on geodynamics: ~20–30 MB / year for
`web_resource_users` (700 resources × 12 months × ~50 avg unique
users); the dnload tables are an order of magnitude smaller.

**Read-path change** (per resource):

```sql
SELECT COUNT(*) FROM (
  SELECT ip, host FROM web_resource_users
    WHERE resid = X AND month IN <pruned months in window>
  UNION   -- de-dups across the gap
  SELECT DISTINCT ip, host FROM web
    WHERE (<resource X patterns>) AND datetime in <live months in window>
) u;
```

`UNION` (not `UNION ALL`) handles the across-month dedup: an IP that
hit a resource in pruned 2022 AND live 2024 still counts once.

**Sequence per year being pruned**

1. **Backfill snapshots** while raw `web` is still present — one-shot
   read job per month, no risk.
2. **Verify** snapshot-derived count = raw-derived count to ±0 % per
   (resource, month).
3. **Switch read path** to the hybrid query above.
4. **`prune-year YYYY`** — chunked DELETE of `web` rows in the year,
   by PK range, gated on snapshots-exist + fully-summarized + not-dirty.
5. **Mark the year as pruned** in `pipeline_state` so summarize uses
   snapshots for those months and rebuild refuses to touch them.

**Scope**: 1 migration, ~200 lines write-path additions to
`do_andmore_usage` and the dnload helpers, ~50 lines read-path
hybrid, two new CLI commands (`backfill-snapshots`, `prune-year`),
tests for parity and the hybrid query.  Probably a focused week.

### File-size reclaim after prune

InnoDB doesn't return deleted page space to the OS — the .ibd file
size stays the same and scans still walk every page even if half are
empty.  After the May-2026 cleanup `web` had ~3.6 GB of internal free
space (27 % wasted) and `websessions` ~600 MB (45 %).

`ALTER TABLE web ENGINE=InnoDB` (== `OPTIMIZE TABLE`) rebuilds the
file and reclaims the holes.  Cost: ~15–30 min, needs ~1× table size
of temp space (the disk is tight on geodynamics — 16 GB free vs a 13
GB table).  Schedule alongside a maintenance window when the table
has accumulated > ~30 % waste.  `pt-online-schema-change` would do
it lock-free but isn't installed.

## Summarize performance via monthly_seen rollups

The summarize stage currently does the same expensive `COUNT(DISTINCT
ip, host)` scan against `web` / `websessions` for every period of
every month.  On the geodynamics 2026-05 rebuild, wide-window periods
(0, 12, 14) took 9–25 min per month even after the `web(dnload,
datetime)` index — the bulk of that time is the JOIN against
`websessions` for distinct-visitor counts, not anything dnload-shaped.

Period 14 (all-time) is the worst case: it re-derives the entire
historical count on every rebuild tick, even though the answer for
month M is just "(answer for month M-1) plus this month's deltas."

### Schema

One table serves every period query:

```
monthly_seen(month, ip, host, ipcountry, orgtype)
  -- one row per distinct (month, ip, host) — "this user was
  -- seen in this month".  Per-month deduped at population time.
  -- PK (month, ip, host)
  -- INDEX (month, ipcountry)
  -- INDEX (month, orgtype)
  -- INDEX (ip, host, month)        — dedup-style lookups
```

Optional companion (only if period 14 still isn't fast enough off
`monthly_seen`):

```
all_time_seen(ip, host, first_seen, ipcountry, orgtype)
  -- one row per distinct (ip, host) ever seen
  -- PK (ip, host); INDEX (first_seen, ipcountry, orgtype)
  -- Maintained as: INSERT IGNORE on monthly_seen population —
  -- PK uniqueness means re-seeing an (ip, host) is a no-op,
  -- so first_seen stays correct.
```

Per-resource variant for andmore-usage:

```
resource_monthly_seen(resid, month, ip, host)
  -- PK (resid, month, ip, host); INDEX (resid, month)
resource_all_time_seen(resid, ip, host, first_seen)   -- optional
  -- PK (resid, ip, host); INDEX (resid, first_seen)
```

### Read path

Every period query collapses to a small indexed scan over the rollup
instead of a wide JOIN against `web` / `websessions`:

```sql
-- Period 1 (month): exact-match, no DISTINCT needed
SELECT COUNT(*) FROM monthly_seen WHERE month = '2025-08-01';

-- Periods 0/3/12/13 (multi-month windows): DISTINCT across the window
SELECT COUNT(DISTINCT ip, host) FROM monthly_seen
  WHERE month >= '2025-01-01' AND month < '2026-01-01';

-- Period 14 via the optional companion: pure indexed range scan
SELECT COUNT(*) FROM all_time_seen WHERE first_seen < '2026-01-01';
```

The 11-column variants (US / EU / Asia / Other; orgtype splits) just
add an indexed filter — `WHERE … AND ipcountry = 'US'` etc.

### Population

At each tick that imports web rows (current month) or processes a
historical month (catchup / rebuild reset), populate `monthly_seen`:

```sql
INSERT IGNORE INTO monthly_seen (month, ip, host, ipcountry, orgtype)
SELECT
  DATE_FORMAT(datetime, '%Y-%m-01'),
  ip, host, ipcountry,
  COALESCE(u.orgtype, '')
FROM web
LEFT JOIN xprofiles_metrics u ON u.username = web.uidNumber  -- or via userlogin_lite
WHERE datetime >= <month_start> AND datetime < <month_end>;
```

PK is `(month, ip, host)` so re-running is idempotent.  If the
companion `all_time_seen` is in play, the same population step does
an `INSERT IGNORE` into it with `first_seen = month_start`.

### Sequencing

1. **One-time backfill** for existing history — walk every month
   that has `web` rows, populate `monthly_seen`.  Probably an hour
   of read-heavy work.
2. **Wire in incremental population** to the catchup / rebuild / normal
   tick paths.  `monthly_seen` becomes part of `_do_usage_metrics_stage`'s
   row-level enrichment (idempotent, fine to run daily).
3. **Add the read-path** — modify summarize's per-period helpers to
   read from `monthly_seen` instead of doing the live JOIN.  Keep a
   feature flag so we can A/B compare numbers against the old code
   path during validation.
4. **Validation pass** — for each month, assert
   `summary_user_vals(via monthly_seen)` matches
   `summary_user_vals(via live JOIN)` to ±0.  Catches any subtle
   semantic divergence before cutting over.
5. **Cut over and remove the live-JOIN path.**

### Estimated win

Concrete on geodynamics:

- Period 1 today: 15s.  After: <1s (already-deduped table).
- Period 0/12/13 today: 1–9 min depending on month width.  After:
  seconds (indexed range scan + DISTINCT over a small set).
- Period 14 today: 5–25 min on heavy months.  After: milliseconds
  with the companion table, or seconds without.
- andmore-usage today: ~700 resources × 3 periods × full scan.
  After: per-resource indexed range scan.

Net: a heavy rebuild tick should drop from 30–50 min to a few
minutes.  Compounds with everything else (no need for the dedicated
`(dnload, datetime)` index, etc.).

### Storage cost — be honest

`monthly_seen` is not a small table.  Sized on geodynamics 2026-05:

- Per-month distinct `(ip, host)` averages 200k–400k tuples across
  the 4-year history.  Heavy bot-traffic months reach 500k–1M.
- Row width ~500 B with PK + secondary indexes.
- Projected 4-year total: ~12 M rows, **~6 GB**.

That's bigger than the current `websessions` (~1.3 GB), smaller than
current `web` (~13 GB).  On a hub with disk headroom this is a clear
disk-for-CPU win; on geodynamics specifically (16 GB free on
/var/lib/mysql today) it'd be a meaningful commitment.

Critically, the bulk of `monthly_seen`'s rows aren't real users —
they're surviving bot / crawler traffic.  The 2026-05 websessions
audit showed sessions-per-event ≈ 1.05, meaning almost every
"session" is a single-hit crawler with a fresh IP.  A crawl that
rotates through 100k IPs in a month adds 100k `monthly_seen` rows
that represent zero real visitors.  So the size scales with how
tight the upstream bot filtering is — see the next section.

### Alternatives if size is the binding constraint

In rough order of preference if disk is tight:

1. **Materialize period 14 only.**  Build just
   `all_time_seen(ip, host, first_seen, …)` and leave the other
   periods doing live JOINs.  ~150k rows × ~50 B = ~30–60 MB on
   geodynamics — three orders of magnitude smaller than full
   `monthly_seen`.  Only the worst-case period gets the speed
   boost (5–25 min → ms), but that's a real chunk of the
   slowness.  Modest win, very cheap, easy to validate.

2. **Time-bound retention.**  Keep `monthly_seen` only for the
   most recent N months; for months older than N, mark them
   "frozen" — assume their summary cells don't change.  Pairs
   naturally with the deep-prune plan in
   "Web table retention" — the same frozen flag protects both
   `web` (against re-import) and `monthly_seen` (against
   re-population).  Caps storage to a known size at the cost
   of no longer being able to retroactively rebuild old months.

3. **HyperLogLog sketches per (month, country, orgtype).**
   Fixed-size 16 KB blob per group, mergeable across months for
   any window with ~2 % approximation error.  Tiny storage
   (~50 MB total for all history).  No MySQL native HLL
   support, so it'd live as opaque blobs with Python-side
   merge.  Architecturally different and more complex; only
   worth it if both #1 and #2 are insufficient.

4. **Per-(month, country, orgtype) row counts only.**  Drop the
   per-`(ip, host)` granularity entirely — store only an
   integer count for each cell.  Tiny (~1 MB total), but you
   lose cross-month dedup so wide-window periods over-count
   visitors who appear in multiple months.  Accept the
   inaccuracy or give up wide-window correctness.  Not
   recommended.

### Scope

About 1–2 focused weeks for the full design: schema migration,
population helper, read-path rewrites, one-time backfill, parity
tests, cut-over.  This is also the same data structure the deep-prune
plan needs (both site-wide and per-resource), so the engineering
investment is reusable.  Easiest to implement after the `resid_match`
flag (see "Web table retention") since it pre-computes the
per-resource bucket each row belongs to.

If size is a hard constraint, start with alternative #1 (period-14
only `all_time_seen`) — it's a few days of work, captures the worst
single-period speedup, and informs whether the full design is worth
it.

## Bot detection improvements

Current `identify-bots` is a hardcoded substring list (~20 tokens).
That catches obvious bots (`bot`, `crawl`, `spider`, `scrapy`, etc.)
but misses a whole category that doesn't include any of those tokens
— measured 2026-05 on geodynamics: 35,556 unique UAs in one day's
log, only 57 flagged.

### High-leverage substrings to add

The single most useful addition is `+http` / `+https` — bots commonly
self-identify with `(... +https://example.com/bot)` in their UA;
no legitimate browser uses that convention.

Other narrow but useful tokens (each catches a real family of bots
that's currently slipping through):

- `curl/`, `wget`, `python-`, `python/` — HTTP-library bots
- `httpclient`, `go-http`, `aiohttp`, `httpx`, `node-fetch`, `axios`
- `scanner`, `checker`, `verifier`, `monitor`, `inspect`,
  `measurement`, `preview`, `linkcheck`
- Specific UAs by name: `siteimprove`, `censys`, `grammarly`,
  `iframely`, `claude-user`, `heritrix`, `bubing`

Add to `BOT_UA_FILTERS` in `hzmetrics.py`.  Volume: hundreds to low
thousands of rows / family / month — smaller wins than the referer-
spam URL filter, but cleaner signal because UA-based filtering is
exact-match and reusable across months.

### Heuristic detection (bigger change)

Substring matching will always lose to evasive bots impersonating
browsers (Chrome/137 etc).  A more durable approach:

- **Frequency**: UA hitting > N req/hour from the same /24 → bot.
- **Referer-empty rate**: UA with > 95 % empty Referer across many
  requests → bot.
- **UA shape**: very old Chrome versions (< latest minus N) are
  almost always bots, since real users upgrade.

Worth the work only if substring-based detection plateaus.

## Single-pass import

The legacy import phase read each daily access log three times —
`import-webhits` (per-day counts), `identify-bots` (UA harvest),
`import-apache` (rows into `web`).  The 2026-05 refactor folded
webhits into `do_import_apache` (now a derived per-day counter
populated inline; `rebuild-webhits` is the regenerate-from-`web`
path), leaving only `identify-bots` as a second file pass.

**Remaining work**: collapse `identify-bots` into the same loop —
collect the unique-UA set as the parser already iterates rows.  That
eliminates the last redundant pass and halves the staging-stage CPU.

**Scope**: small follow-up — fold the UA collection into the
import-apache loop, retire identify-bots as a separate CLI subcommand
for one-off backfills), update the A/B fixtures.  Probably a few
days, mostly testing.

## Cron install

The every-5-min `hzmetrics.py tick` entry is installed into the
apache user's crontab via `sudo -u apache crontab
/opt/hubzero/metrics/conf/hzmetrics.cron.apache.sample` — on geodynamics this has
been deliberately left un-registered during the audit so all ticks
are manual.  Once steady-state is verified, register the crontab
and let it drive.

No code work; cronie auto-detects the user-crontab change without
a daemon restart.

## Check nanoHUB's metrics fork for resource-merge ideas

nanoHUB is the longest-running HUBzero deployment and has had ongoing
operational pressure to handle metrics cases the upstream code never
addressed.  Worth surveying their metrics fork (if accessible) for:

- Hand-curated "merge resource B into A" logic when B is published as
  effectively a version 2 of A — upstream only supports this via
  `jos_resource_assoc` parent→child + `standalone=0` on the child,
  which requires CMS-side data changes and doesn't carry semantics
  for "A and B are the same thing for analytics purposes."
- Any other patches they've made to `xlogfix_*` / `func_*` that
  fixed bugs the geodynamics snapshot still has.

Compare their tree against `tests/legacy/` and decide what's worth
porting.  No code work here until that survey happens.

## Periodic ANALYZE TABLE

InnoDB statistics drift after heavy churn (DELETEs, ALTERs, large
imports), and the query planner sometimes picks worse indexes — we
saw this on `web` after the May 2026 cleanup where the `ip` single-
column index was picked over the new `web_dt_ip` covering index.

A monthly `ANALYZE TABLE web; ANALYZE TABLE websessions; ANALYZE
TABLE userlogin; ANALYZE TABLE toolstart;` cron job costs seconds and
keeps the planner honest.  Add to the install Makefile target.
