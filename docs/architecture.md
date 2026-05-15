# Architecture

How `hzmetrics.py` is organized and how a day's data moves through it.

## The two databases

Every HUBzero hub has two MariaDB schemas, both named after the hub:

| Database          | Owned by | Role |
|-------------------|----------|------|
| `<hub>`           | CMS      | Live CMS state — users, profiles, resources, sessions.  Metrics reads from it; writes only `jos_session_geo` (for the whoisonline map) and `jos_resource_stats*` (per-tool aggregates surfaced by the UI). |
| `<hub>_metrics`   | Pipeline | Enriched analytics — `web`, `websessions`, `toolstart`, `userlogin`, `summary_*_vals`, etc.  Owned end-to-end by `hzmetrics.py`. |

On the reference deployment these are `foo` and
`foo_metrics`.  Credentials are in
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

## Scheduling and concurrency

One cron entry:

```
*/5 * * * *  apache  python3 /opt/hubzero/bin/hzmetrics.py tick
```

`tick` does:

1. Always: refresh `whoisonline` (jos_session_geo + xml map).
2. If the wall clock minute is in `{30..34}`: try to acquire
   `/var/run/hzmetrics/hzmetrics.pid`.  If acquired, run the metrics
   pipeline.  If another `tick` holds the lock, exit cleanly.

Inside `run`:

1. Read `hzmetrics.state` (last-daily-run timestamp).  If a daily run
   completed today, exit without doing anything.
2. Find the oldest pending log day.  If none, mark daily complete and
   exit.
3. Process at most one full month of backlog per `run` invocation.
4. Update `hzmetrics.state` if a full daily cycle finished.

This is the catch-up mechanism: a long-stalled host gradually drains
the log queue at one month per hour without operator intervention,
without holding the lock for hours, and without skipping any data.

`tests/ab/port_dryrun/` verifies that `--dry-run` mode on every
mutating subcommand performs zero database writes.
`tests/ab/port_idempotency/` verifies that re-running the pipeline on
already-processed state produces byte-identical output.
`tests/ab/port_determinism/` verifies that two fresh-DB runs produce
identical output.  See [testing.md](testing.md).

## State files

- `/etc/hubzero-metrics/access.cfg` — DB credentials, paths.  Owned
  by root:apache, mode 640.
- `/etc/hubzero-metrics/hzmetrics.conf` — *optional* runtime overrides
  (DNS nameserver, concurrency, timeout).  See
  [`conf/hzmetrics.conf.sample`](../conf/hzmetrics.conf.sample).
- `/var/run/hzmetrics/hzmetrics.pid` — PID lock, ensures one
  pipeline at a time.  Created at boot by `/etc/tmpfiles.d/hzmetrics.conf`.
- `/var/run/hzmetrics/hzmetrics.state` — daily state (last completed
  daily-run timestamp).
- `/var/log/hubzero/metrics/` — pipeline log directory.
- `/var/log/httpd/daily/`, `/var/log/hubzero/daily/` — incoming log
  files.  Pipeline reads, processes, and moves them to `imported/`.

## CLI surface

`hzmetrics.py --help` lists everything, but the major subcommands are:

```
tick                       cron entry point (whoisonline + metrics at :30)
run [--dry-run]            autonomous metrics run (analyze/summarize)
whoisonline                refresh whoisonline state
status                     show pending vs imported log state
process --next             process oldest pending month manually
process --month YYYY-MM    process a specific month
import / analyze / summarize  individual stages, --month YYYY-MM
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
- **`tests/ab/`** — the A/B test harness (26 tests; see
  [testing.md](testing.md)).
- **`README.txt`** — historical hub-installation notes (largely
  superseded by these docs).
