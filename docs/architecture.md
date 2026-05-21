# Architecture

How `hzmetrics.py` is organized and how a day's data moves through it.

## The two databases

Every HUBzero hub has two MariaDB schemas, both named after the hub:

| Database          | Owned by | Role |
|-------------------|----------|------|
| `<hub>`           | CMS      | Live CMS state — users, profiles, resources, sessions.  Metrics reads from it; writes only `jos_session_geo` (for the whoisonline map) and `jos_resource_stats*` (per-tool aggregates surfaced by the UI). |
| `<hub>_metrics`   | Pipeline | Enriched analytics — `web`, `websessions`, `toolstart`, `userlogin`, `summary_*_vals`, etc.  Owned end-to-end by `hzmetrics.py`. |

For a hub named `foo`, these are `foo` and `foo_metrics`
respectively.  Credentials are in
`/etc/hubzero-metrics/access.cfg` (owned `root:apache`, mode 640).
The config file uses a bare `$var = 'value';` syntax that is read by
both the Python pipeline and the Perl scripts under
[`tests/legacy/`](../tests/legacy/).

## Tables

### Hub DB (`<hub>`)

| Table | Role in pipeline |
|---|---|
| `jos_users`, `jos_user_profiles`, `jos_xprofiles` | Read by `import-hub-data` to rebuild `jos_xprofiles_metrics` every run. |
| `jos_resources`, `jos_resource_assoc` | Read by `gen-tool-stats` / `andmore-usage` to enumerate tools and resource hierarchies. |
| `jos_tool_version`, `jos_tool_version_alias` | Read to map tool versions back to canonical names. |
| `sessionlog`, `joblog` | Read by `gen-tool-stats` and `middleware-{wall,cpu}` for tool-session and per-job metrics. |
| `jos_session` | Read every 5 min by `whoisonline` for currently-online users. |
| `jos_session_geo` | **Written** by `whoisonline` — IP→geocoord lookups used by the live map. |
| `jos_resource_stats`, `jos_resource_stats_tools`, `jos_resource_stats_tools_topvals`, `jos_stats_topvals` | **Written** by `gen-tool-stats`/`gen-tool-tops`/`gen-tool-toplists`/`andmore-usage` — surfaced by the usage UI per resource. |

### Metrics DB (`<hub>_metrics`)

Pipeline-owned.  The big ones:

| Table | One row per | Set by |
|---|---|---|
| `web` | HTTP request (Apache log line) | `import-apache` |
| `websessions` | Coalesced visitor session | `logfix-session` |
| `toolstart` | Tool launch | `import-hub-data` + `middleware-{wall,cpu}` |
| `userlogin` | Login / sim_login event | `import-auth` |
| `webhits` | Hourly hit count | `import-webhits` |
| `userlogin_lite` | Filtered view of `userlogin` (login + simulation only) | Rebuilt from scratch on each `summarize-month` run |
| `sessionlog_metrics` | Enriched tool-session record | `import-hub-data` |
| `jos_xprofiles_metrics` | User profile snapshot | `import-hub-data` (full rebuild from `<hub>.jos_xprofiles`) |
| `bot_useragents` | Known-bot user agent | `identify-bots` |
| `exclude_list` | IP / URL / useragent / domain filter | Operator (via a CMS-side migration; pipeline only reads) |

Reference tables that are read-only at pipeline runtime
(`domainclass`, `classes`, `continents`, `countries`,
`country_continent`, `regions`) are populated by
`hzmetrics.py setup-db` and used during enrichment.

### Summary tables

The five output tables that drive the usage reporting UI.  All have
the same shape:

```
( rowid TINYINT,        -- per-table-defined metric index
  colid TINYINT,        -- breakdown (1 = total, others = continent / org-type)
  datetime DATETIME,    -- 'YYYY-MM-00 00:00:00'  (period 14 uses '0000-00-00')
  period TINYINT,       -- 0 calendar year, 1 month, 3 quarter, 12 rolling-12mo,
                        -- 13 fiscal year (Oct-Sep), 14 all-time
  value VARCHAR(200),   -- the metric value (often numeric-as-string)
  valfmt TINYINT )      -- format hint: 1=count, 2=percent, 4=jobs, 5=duration sec
```

The semantics of `rowid` differ per table:

- `summary_user_vals` — user counts (registered, unregistered,
  downloaders).  `rowid=1` is total = sum of `rowid IN (6,7,8)`.
- `summary_simusage_vals` — tool/simulation usage (jobs, CPU, wall).
- `summary_misc_vals` — domains, sessions, visitors, new accounts,
  max-logins-on-day, hits.
- `summary_andmore_vals` — per-resource user counts (different
  shape).

The full rowid/colid decoder is in
[usage-tables.md](usage-tables.md), adapted from J.M. Sperhac's
"Hub usage data overview and table translator."

## Pipeline stages

The pipeline runs in three nested loops, all driven by
`hzmetrics.py run` (invoked by `tick` every hour at `:30`):

```
for each pending month, oldest first:                  ┐ outer loop:
                                                       │ catch-up across
    import:    apache + auth logs → web, userlogin    │ multiple months
    analyze:   per-month enrichment chain              │
    summarize: period grids → summary_*_vals          │
                                                       ┘
```

### import

```
fetch        → concatenate daily/*.gz into staging files
import-apache → web (one row per HTTP request)
import-auth   → userlogin (one row per login event)
import-webhits → webhits (hourly aggregate)
identify-bots → bot_useragents (table of known bot UAs)
archive       → gzip and move daily logs into imported/
```

### analyze (per-month, idempotent)

Enrichment runs for the rows imported into `web`, `websessions`,
`toolstart` since the last analyze.  Each step is restartable; each
step processes only rows still missing the relevant column.

```
import-hub-data       → jos_xprofiles_metrics, sessionlog_metrics
middleware-wall       → toolstart.walltime
middleware-cpu        → toolstart.cputime
resolve-dns (web)     → web.host
resolve-dns (toolstart, sessionlog_metrics)
fill-domain (web)     → web.domain
fill-domain (toolstart, sessionlog_metrics)
fill-ipcountry        → web.ipcountry, websessions.ipcountry, toolstart.ipcountry
logfix-session        → websessions (coalesce web rows into sessions)
clean-bots            → DELETE bot rows from web / websessions
fill-user-info        → toolstart user-meta columns
gen-tool-stats        → jos_resource_stats_tools
gen-tool-tops         → jos_resource_stats_tools_topvals
gen-tool-toplists     → jos_stats_topvals
andmore-usage         → jos_resource_stats (per-resource counts)
```

`backfill-dnload` is a one-shot variant that walks `web` from a given
month forward and sets `web.dnload` from the URL pattern.
`import-apache` sets `dnload` inline on new rows, so this is only
needed once after the column was introduced.

`do_analyze(month, sessions=...)` has a `sessions` parameter that
controls whether the session-bound sub-stages run:

  - `sessions=True` (the default) — runs every step above, including
    `logfix-session` and the `clean-bots` / `fill-ipcountry` passes
    against `websessions`.  Used by catchup, rebuild, and month-close
    normal ticks, where the month's input set is stable.

  - `sessions=False` — skips `logfix-session` and the
    websessions-bound steps; row-level enrichment (DNS, domain,
    country, clean-bots on web, fill-user-info, fill-ipcountry on
    web/toolstart) still runs.  Used by normal-mode for the
    still-incomplete current month, because daily logfix-session
    creates a fresh sessionid boundary at every tick (rows already
    stamped on a prior tick are excluded from the next pass by the
    `sessionid IS NULL OR sessionid = '0'` predicate, so a session
    that genuinely spans the tick boundary gets split).

The completeness signal `is_month_complete(prev)` decides when a
normal-mode tick fires the month-close `sessions=True` analyze on
prev: it returns True when either prev's last-day log file is in
`imported/`, OR `web` has at least one row dated in the month after
prev (a data-driven signal that import time has crossed the
boundary; replaces the legacy `days_in > 5` calendar fallback).

### summarize (per-month, full re-aggregation)

```
build_userlogin_lite       (DROP + recreate from userlogin)
build_login_ips_table      (temp table indexing registered-user IPs)
build_download_users_table (temp table indexing dnload=1 rows per period)
for period in (0, 1, 3, 12, 13, 14):
    for each metric function:
        write a row per (rowid, colid)
```

The temp-table approach was a key performance win: replacing
`SELECT … WHERE ip NOT IN (literal-comma-list-of-100k-IPs)` with a
LEFT JOIN to an indexed temp table, and replacing a correlated
`EXISTS` against a 30M-row `web` table with a JOIN against a
pre-filtered `WHERE dnload=1` subset.  The all-time period (period
14) went from "10+ hours sometimes crash MariaDB" to "a few minutes"
after these and the indexed `dnload` column.

`summarize-month` always rewrites every cell it produces for the
target month — there is no incremental summary mode.  Idempotency
comes from `DELETE` + `INSERT` per cell, not from skipping rows.

The key performance optimizations the rewrite added to summarize
(without changing what gets produced):

- **`build_login_ips_table()`** materializes the registered-user IP
  set into an indexed temp table.  Replaces a literal
  comma-separated `WHERE ip NOT IN (…)` string that on mature hubs
  grew past 100k IPs.
- **`build_download_users_table()`** builds `dl_users_period_tmp`
  once per period via a JOIN that drives from the (small) `WHERE
  dnload=1` side rather than the (huge) `web` side.  Replaces a
  correlated `EXISTS` against full `web` — the structural fix
  behind the period-14 win.
- **`download_sessions_tmp`** is built via a single `INSERT …
  SELECT` instead of the legacy's row-by-row chunked INSERTs.
- **`country_continent` lookups** are cached once at run start
  rather than re-queried per cell.
- **`_summary_11col_cells()`** is a shared helper for the 11-column
  residency+orgtype block that `reg_users` and `sim_users` both
  emit, parametrised by the country and orgtype columns.  This is
  structural deduplication, not a behavior change.

`gen_tool_stats.php` separately had `_findweeks()` for chunking the
month into 4 week-sized scans.  The Python port preserves the
chunking pattern, including the **legacy quirk that each week-chunk
begins on the day BEFORE the month starts** (so July 2025's first
chunk is `[2025-06-30, 2025-07-07)`).  This came up in A/B testing
of `fill-domain` and is documented in the
[`testing.md`](testing.md) and the commit message for that fix.

## Scheduling and concurrency

One cron entry:

```
*/5 * * * *  apache  python3 /opt/hubzero/bin/hzmetrics.py tick
```

`tick` does:

1. Always: refresh `whoisonline` (jos_session_geo + xml map).
2. If the wall clock minute is `30`: invoke `cmd_run`.
   `cmd_run` acquires `/var/run/hzmetrics/hzmetrics.pid` via
   `fcntl.flock`; if another `tick` holds it, exit cleanly.

The lock is the only concurrency guard.  `flock` releases automatically
on process death, so there is no stale-lock recovery to write.

## Catchup orchestration (state machine)

`cmd_run` is a three-mode state machine, not the "one month per tick"
loop the section above used to describe.  The mode lives in the
`pipeline_state` table (see [State](#state) below) and decides what
each tick does:

| Mode     | What a tick does                                                                                   | Transition to                  |
|----------|----------------------------------------------------------------------------------------------------|--------------------------------|
| `normal` | Import pending days for the current month; summarize the previous month when it's fully imported.  | → `catchup` when any month strictly before today has either a pending source log or DB rows with incomplete summary. |
| `catchup`| Pick the oldest backlog month, apply the per-month decision matrix, summarize with `periods=(1,)`. | → `rebuild` when no backlog months remain. |
| `rebuild`| Walk forward from `rebuild_cursor` through prev-month, re-summarize each with all six periods.     | → `normal` when cursor passes prev-month. |

Mode transitions are computed at the start of every tick from
filesystem + DB state — not stored.  So the orchestrator self-corrects
after manual intervention or external changes (e.g., someone drops a
new log into `daily/2027/` mid-rebuild).

### Why three modes

The legacy "one month per tick" approach worked when backlog was small
and recent.  It produces wrong long-window numbers when the backlog
spans multiple years: a month summarized with all six periods writes
its period-14 (all-time) cell from the rows present at that moment.
Backfilling an earlier month later doesn't update those cells.

Splitting catchup from rebuild fixes that.  Catchup ticks stay cheap
because they only write period-1 (the month's own cells); the
long-window cells stay deferred.  Once catchup is done, rebuild walks
every affected month and re-summarizes with all six periods.  This
gives correct period-14 / period-13 / period-12 numbers across the
whole DB after a multi-year backfill.

### The per-month decision matrix

Catchup picks the oldest backlog month and routes it through one of
five branches based on three Phase-C helpers (`month_has_source`,
`month_has_data`, `is_month_fully_summarized`):

| source | data | summary       | action |
|:------:|:----:|:--------------|--------|
|   ✓    |  ✗   | —             | fresh import + analyze + summarize-period-1 |
|   ✓    |  ✓   | none/partial  | wipe + reimport + analyze + summarize-period-1 |
|   ✗    |  ✓   | none/partial  | DB-only: analyze + summarize-period-1 (no import) |
|   ✗    |  ✗   | —             | skip (true gap — no source ever existed) |
|  any   |  ✓   | full          | skip (already done) |

The `source ✗ data ✓` branch is what catches the 2024-access case
on geodynamics (rows exist in `web` because they were imported once,
but the archived source files are gone and can't be re-derived).

### Source-log discovery

`enumerate_log_sources(kind)` returns sorted `[(YYYYMMDD, Path), ...]`
unioning every place a source log may live, in priority order:

  - `daily/<site>-access*log*` (current standard)
  - `daily/<YYYY>/<site>-access*log*` (sysadmin year-subdir layout)
  - `daily.holding/<site>-access*log*` (alternate logrotate target)

Duplicate dates resolve toward the higher-priority location (a
warning logs the conflict).  After `do_archive_logs` moves files
into the canonical `imported/`, any `daily/YYYY/` or `daily.holding/`
subdir that just became empty gets `rmdir`'d.  Subdirs are sysadmin
convention, not pipeline policy — clean up as we drain them.

### Catching up manually

`hzmetrics status` shows the current mode, `catchup_started` anchor,
and (in rebuild mode) cursor + remaining-month count.
`hzmetrics rebuild-summaries --since YYYY-MM [--through YYYY-MM]
[--periods 0,1,3,12,13,14] [--dry-run]` is a manual range
resummarize; it does NOT touch `pipeline_state.mode`, so the
state machine keeps running independently.

`tests/ab/port_cmd_run/` walks each row of the decision matrix and
each mode transition; `tests/ab/port_periods_filter/` verifies that
catchup's `periods=(1,)` actually skips long-window writes;
`tests/ab/port_rebuild_correctness/` proves that a backfill changes
period-14 cells for downstream months without disturbing period-1.

## State

Most pipeline state lives in the DB now.  `/var/run/hzmetrics/` is
only the runtime lockfile.

`pipeline_state` is a tiny key/value table in `<hub>_metrics`:

| key                | meaning                                                       |
|--------------------|---------------------------------------------------------------|
| `analyzed`         | `YYYY-MM-DD` — date a normal-mode tick last ran analyze       |
| `mode`             | `normal` \| `catchup` \| `rebuild`                            |
| `catchup_started`  | earliest YYYY-MM that catchup touched (set on entry)          |
| `rebuild_cursor`   | next YYYY-MM that rebuild will resummarize                    |

Updates are single-statement `INSERT … ON DUPLICATE KEY UPDATE`, so
multi-key writes are atomic from any concurrent reader's point of view.

The flock-based lock stays on disk — kernel-managed dead-process
release is hard to replicate cleanly in SQL.

**Bootstrap:** on first read after upgrade, if `pipeline_state` is
empty AND `/var/run/hzmetrics/hzmetrics.state` exists (the legacy
file format), its keys are imported into the table once.  The file
is left in place — harmless and useful for operators who grep
`/var/run` first.

`tests/ab/port_state/` covers DB read/write, multi-key atomicity,
and the file→DB bootstrap (empty / skip-when-table-nonempty /
malformed-lines / latch-after-first-call / unreadable-file).

## Files on disk

- `/etc/hubzero-metrics/access.cfg` — DB credentials, paths.  Owned
  by root:apache, mode 640.
- `/etc/hubzero-metrics/hzmetrics.conf` — *optional* runtime overrides
  (DNS nameserver, concurrency, timeout).  See
  [`conf/hzmetrics.conf.sample`](../conf/hzmetrics.conf.sample).
- `/var/run/hzmetrics/hzmetrics.pid` — PID lock, ensures one
  pipeline at a time.  Created at boot by `/etc/tmpfiles.d/hzmetrics.conf`.
- `/var/run/hzmetrics/hzmetrics.state` — legacy state file.  Only
  read at first-run bootstrap into `pipeline_state`; not updated
  by current code.  Safe to delete after the bootstrap.
- `/var/log/hubzero/metrics/` — pipeline log directory.
- `/var/log/httpd/daily/`, `/var/log/hubzero/daily/` — incoming log
  files.  Pipeline reads, processes, and moves them to `imported/`.

## CLI surface

`hzmetrics.py --help` lists everything, but the major subcommands are:

```
tick                       cron entry point (whoisonline + metrics at :30)
run [--dry-run]            autonomous metrics run — dispatches by pipeline_state.mode
whoisonline                refresh whoisonline state
status                     orchestrator mode + cursors + pending/imported counts
process --next             process oldest pending month manually
process --month YYYY-MM    process a specific month
import / analyze / summarize  individual stages, --month YYYY-MM
rebuild-summaries --since YYYY-MM [--through ...] [--periods 0,1,3,12,13,14]
                           manual range resummarize (doesn't touch state.mode)
rebuild-from YYYY-MM       atomic reset: sets mode=rebuild + rebuild_cursor
mark-dirty YYYY-MM ...     flag months as needing rework after bulk web mutation
clear-dirty [--all|YYYY-MM ...]  remove months from the dirty set
fill-geo / backfill-dnload    one-shot backfill utilities
migrate [--apply]          show or apply schema migrations
setup-db [--dry-run]       create the metrics DB schema from scratch
```

Each mutating subcommand has `--dry-run`.  `--force` bypasses the
daily-state-already-completed guard on `run` / `process` / `analyze` /
`summarize`.

## Where the code lives

- **`hzmetrics.py`** — the entire pipeline.  ~6000 lines of Python.
- **`conf/hzmetrics.conf.sample`** — optional runtime overrides.
- **`conf/hubzero-metrics.cron.apache`**, **`conf/hubzero-metrics.cron.d`** —
  cron entry templates (apache crontab format and /etc/cron.d format).
- **`conf/hzmetrics-logrotate-postrotate.sh`** — logrotate hook.
- **`conf/hzmetrics.tmpfiles.conf`** — systemd-tmpfiles config to create
  `/var/run/hzmetrics/` at boot.
- **`tests/legacy/`** — the original PHP/Perl/Bash pipeline preserved
  as the A/B parity reference.
- **`tests/ab/`** — the A/B test harness (35 ports; see
  [testing.md](testing.md)).
- **`README.txt`** — historical hub-installation notes (largely
  superseded by these docs).
