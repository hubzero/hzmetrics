# Summary

`hzmetrics.py` is the analytics pipeline for a HUBzero-based science
gateway.  It ingests Apache access logs and CMS authentication logs,
enriches the data (reverse DNS, domain classification, GeoIP, session
coalescing), and produces monthly summary statistics stored in a
MariaDB metrics database.  Those statistics drive the hub's usage
reporting pages and are used for grant reporting.

The project replaces a decade-plus accumulation of PHP, Perl, and Bash
scripts (preserved under [`tests/legacy/`](../tests/legacy/)) with a
single Python entry point.  The wire-format, schemas, and produced
numbers are intentionally **bug-for-bug compatible** with the legacy
code — the rewrite changes the implementation, not the metric
definitions, so existing reporting UIs and downstream consumers keep
working unchanged.

## What it produces

Five tables in the `<hub>_metrics` database, written one row per
`(rowid, colid, period, datetime)` cell:

| Table                     | What it holds |
|---------------------------|---------------|
| `summary_user_vals`       | User counts (registered, unregistered, downloaders) by org type and continent |
| `summary_simusage_vals`   | Tool/simulation usage (jobs, CPU, wall, view time) |
| `summary_misc_vals`       | Domains, sessions, visitors, new accounts, max-logins-on-day |
| `summary_andmore_vals`    | Per-resource user counts (separate from tool stats) |
| `jos_resource_stats*` (hub DB) | Per-tool aggregate stats and ranked top-lists |

Plus three real-time-ish artifacts:

- **`jos_session_geo`** (hub DB) and **`whoisonline.xml`** (web root) —
  the live "who is online" map, refreshed every 5 minutes.
- **`web.dnload`** column (since 2026) — pre-computed download flag
  used by the summary's expensive period-14 (all-time) aggregations.
- **`userlogin_lite`** — filtered view of `userlogin` rebuilt each
  summary run.

See [usage-tables.md](usage-tables.md) for the full rowid/colid
decoder.

## How it runs

One cron entry, every 5 minutes:

```
*/5 * * * *  apache  python3 /opt/hubzero/bin/hzmetrics.py tick
```

`tick` updates the whoisonline map on every invocation.  At `:30` past
each hour it also opportunistically runs the metrics pipeline (one
PID-lock-guarded process at a time).  Orchestrator state lives in the
`pipeline_state` DB table — one row per key, including the current
mode (`normal` / `catchup` / `rebuild`).

If the host has been down for a while, log files just accumulate in
`/var/log/httpd/daily/`, `/var/log/httpd/daily.holding/`, or
`/var/log/hubzero/daily/`.  Once `tick` resumes, the orchestrator
detects the backlog, flips itself into `catchup` mode, and drains
months oldest-first — one per invocation — using a per-month decision
matrix that picks between fresh import, wipe+reimport, or DB-only
resummarize.  After catchup, a `rebuild` phase walks every affected
month with all six period codes to fix long-window cells.  No manual
intervention required for routine backfill; see
[architecture.md → Catchup orchestration](architecture.md#catchup-orchestration-state-machine).

## Pipeline at a glance

```
┌──────────────────────────────────────────────────────────────────┐
│  tick (every 5 min)                                              │
│  ├── whoisonline   (refresh session_geo + xml map)               │
│  └── run (once per hour, :30 past)                               │
│       ├── status check (anything pending?)                       │
│       ├── import   (apache + auth logs → web, userlogin)         │
│       ├── analyze  (per-month enrichment: DNS, domain, GeoIP,    │
│       │             middleware, logfix-session, user-info,       │
│       │             gen-tool-stats/tops/toplists)                │
│       └── summarize  (period 0/1/3/12/13/14 grids → summary_*    │
│                       _vals; full re-aggregation each run)       │
└──────────────────────────────────────────────────────────────────┘
```

`hzmetrics.py status` reports what's pending; `process --next` runs
one full month manually.  `analyze` and `summarize` are individually
runnable for any month.

## Who reads the metrics

The pipeline produces numbers; different audiences read them in
different shapes.  Knowing which is which helps when deciding what
to fix when a number looks off:

- **Gateway owner / PI** — total registered users, total tool runs,
  visits per month/year, geographic distribution.  Surfaced at
  `hub.org/usage` (the public usage-overview page).  Aggregated; no
  individual user data.
- **Tool contributor** — per-tool ranked stats (users, jobs, CPU
  time) over rolling windows.  Surfaced at `hub.org/usage/tools/12`
  and on each tool resource's "Usage" tab.  Driven by
  `jos_resource_stats_tools_topvals` and `jos_stats_topvals`.
- **Operator / ops** — pipeline health, log import status, exclude
  list effectiveness, bot inflation events.  Surfaced via
  `hzmetrics.py status` and the pipeline log file.  See
  [operations.md](operations.md).
- **Funder / grant reporting** — long-window aggregates (period 12
  / 14), often pulled by ad-hoc SQL against `summary_*_vals` rather
  than via the UI.  See [usage-tables.md](usage-tables.md) for
  example queries.

The bug-for-bug-parity contract of the rewrite means the existing
UI consumers and reporting queries all keep working unchanged — the
new pipeline produces the same numbers in the same shapes.

## Reading the rest of these docs

- **[motivations.md](motivations.md)** — why this rewrite happened.
- **[history.md](history.md)** — where the code comes from.
- **[architecture.md](architecture.md)** — pipeline internals, tables,
  scheduling, catch-up, locking.
- **[usage-tables.md](usage-tables.md)** — `summary_*_vals` cheat sheet.
