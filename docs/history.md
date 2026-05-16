# History

This document traces where the code under `tests/legacy/` came from
and what came after it on the path to the current
`hzmetrics.py`-based pipeline.

## Early lineage (period 14 starts in 1995)

The summary tables' all-time period uses `1995-01-01` as the lower
bound — a hint at how long this codebase has been in service.  The
modern PHP/Perl pipeline accreted on top of an even older stats
infrastructure.  Concrete fragments of 2014–2016 ops notes survive
in the project's history — including the very first "basic new-month
checks" runbook that the [operations.md](operations.md) runbook here
is loosely modeled on.

## 2010s — The original HUBzero metrics package

The HUBzero metrics subsystem was originally written in Perl by
Swaroop Shivarajapura.  Nicholas J. Kisseberth later ported the
codebase to PHP, and that PHP form is what accreted through the 2010s
and is preserved verbatim under [`tests/legacy/`](../tests/legacy/).

HUBzero is a CMS for "science gateways" — hosted collections of
computational tools.  The metrics package was part of the
open-source HUBzero distribution and shipped to every hub.  It
consisted of:

- A cron-driven set of **PHP scripts** under `/opt/hubzero/bin/metrics/`
  that parsed daily Apache and CMS authentication logs into a
  `<hub>_metrics` database, enriched the rows (DNS, GeoIP, session
  coalescing), and computed monthly summary statistics.
- A HUBzero CMS "usage overview" plugin that read those summary
  tables and rendered the public usage reporting pages
  (`hub.org/usage`).

The package was installed via RPM/DEB built from
`source/Makefile` in this repo (and its `gitlab.hubzero.org/.../hubzero-metrics`
predecessor).  Over a decade it grew through layered additions:
Perl scripts to handle Apache log fetch and archive, more PHP for
specific metric definitions, Bash glue between stages, exclusion-list
tables to filter bots.

The shape that survived to the rewrite:

- One file per metric subdomain (`xlogfix_dns_v2.sh`, `xlogfix_domain.php`,
  `xlogfix_summary.php`, etc.)
- One cron entry per stage (`import`, `process_tool_metrics`,
  `process_usage_metrics`, `process_usage_metrics_summary`)
- Two databases: live CMS (`<hub>`) and analytics (`<hub>_metrics`)
- Five summary tables keyed by `(rowid, colid, period, datetime)`

All preserved verbatim under [`tests/legacy/`](../tests/legacy/) at
the snapshot just before this rewrite began.  See
[architecture.md](architecture.md) for the table-by-table reference.

## ~2017–2021 — A large hub goes its own way

The largest HUBzero deployment had traffic, tool count, and reporting
requirements that outgrew the stock pipeline.  Its operators forked
and accumulated their own code:

- **`metrics.custom.<hub>`** — additional PHP+Bash specific to that
  hub.  Lived under `/opt/hubzero/bin/metrics.custom.<hub>`.
  Refactored the daily run to use a Truth/Provisional/Production
  directory layout for logs (size-bounded sanity check on incoming
  log files) and added custom cleanup SQL scripts to delete known-bot
  rows from `web` and `websessions` after enrichment.
- Custom Usage Overview plugins (`overview2017`, `overview2021`,
  `overviewnew`, etc.) — multiple competing versions in the CMS
  plugin directory, of which `overview2017` was the
  actually-deployed one.

By 2024 that deployment was running **three** metrics codebases
concurrently:

1. `hubzero-metrics` — the open-source vanilla package, used for the
   fetch/import/archive of raw log files.
2. `metrics.custom.<hub>` — hub-specific enrichment and summary.
3. `hubzero-analytics` — see below.

J.M. Sperhac's 2024-11 status writeup is the best account of how this
got fragile: "complex, dated, fragile, and would benefit from an
audit. They continue to require regular fixes. Some code that we
execute daily is no longer relevant."

## ~2017–2022 — `hubzero-analytics`, the attempted Python rewrite

In parallel, a Python rewrite was attempted under the name
**`hubzero-analytics`**.  It lived at `/usr/share/hubzero-analytics`
on the largest hub and was the only Python metrics code in production
anywhere in the HUBzero ecosystem.

Design choices:

- **Python + Celery** for task scheduling.
- **Redis** as a task broker and as a real-time data cache for the
  `whoisonline.py` widget and per-resource usage plugins
  (`plg_resources_usagenewdata` et al.).
- **Independent log import** — `hubzero-analytics` did its own Apache
  log fetch and parse, parallel to the legacy `hubzero-metrics`
  fetch.
- Per-month scripts: `user_count.py`, `visitor_count.py`,
  `visitors_cumulative.py`, `andmore_user_builder.py`, etc., each
  Celery-scheduled.

By 2024 the project had been effectively abandoned.  The repo at
`gitlab.hubzero.org/hubzero/hubzero-analytics` was out of date
relative to what was actually deployed.  Redis-backed resource usage
plugins were timing out in the UI ("Currently retrieving data.
Please check back later."  Permanently.).  The `hubzero-analytics`
cron entry ran in production alongside the legacy `hubzero-metrics`
cron entry, with neither codebase a complete replacement for the
other.

For a sense of scale, the `hubzero-analytics` codebase reached:

- `Backend/Metrics/metrics_base.py` — 1639 lines
- `Backend/Metrics/indTool.py` — 730 lines
- `Backend/Metrics/baseTool.py` — 508 lines
- ...plus the Celery scheduler, Redis broker, API/verb modules, and
  Debian + RPM packaging.

…all of which never fully displaced the ~800-line `xlogfix_summary.php`
it was meant to replace.

The lesson informed the current rewrite: **no broker, no daemon, no
background workers**.  Cron + a single Python process + a PID lock is
sufficient for the actual data volumes involved.

## 2025 — Purdue migration, exclude_list refresh

In early-to-mid 2025 the HUBzero hubs migrated from SDSC hosting to
Purdue's RCAC.  The migration touched the metrics pipeline mostly
through the `exclude_list` table — IP addresses, useragent strings,
and domains that should be excluded from metrics accounting because
they represent infrastructure activity, not user activity.

Sperhac's pull request to add columns and data to the `exclude_list`
metrics table extended the schema (`notes` and `date_added` columns)
and refreshed the bot/scanner/security-monitor entries.  The
post-migration ops work that fed into this rewrite:

- Added `rcac.purdue.edu` and `%.itap.purdue.edu` exclusions.
- Updated entries for current security scanners (Nessus, PRTG,
  Detectify, gatus).
- Per-hub IP refreshes (each hub has its own metrics DB and exclusion
  list).

By late 2025 the migration was done and the team had a clear view of
what wasn't working in the original metrics pipeline on the
post-migration hosts.

## 2025 — Pre-rewrite stabilization

Before the full rewrite, ~2025 saw a year of in-place fixes against
the legacy package on a Purdue-hosted hub.  These are the commits
that ultimately defined the "TRUE pre-refactor legacy" used as the
A/B parity baseline:

- **Jan 2025** — `whoisonline` bot-flag fix; `dns_worker` script
  comments + qualified table-name worker-process check.
- **Jan 30 2025** — explicit `timeout` prefix on the `host(1)`
  shell-out per a StackOverflow recipe ([`gethostbyaddr` timeout
  workaround](https://stackoverflow.com/questions/6972989/)) — the
  closest the legacy code got to the rewrite's `aiodns` solution.
- **Mar 3 2025** — Remove unused plotting scripts and dependencies.
- **Apr 29 2025** — Fix to enddate; exclude `/cron/tick` and `/api/`
  content from metrics.
- **May 5 2025** — null-handling triplet: null-var check before
  `preg_match`, null-string `dbquote` handling, array element check
  removing 'force' processing.
- **Jun 10 2025** — Accommodate undefined parameters (Jeanette
  Sperhac, jsperhac@ucsd.edu).
- **Jun 12 2025** — Don't pass null to `mysqli_real_escape_string`.
- **Jul 14 2025** — Handle `preg_match()` null parameter warning in
  `xgethostbyaddr` (the function the rewrite ultimately replaced
  wholesale with `aiodns`).

These commits' authors include Jeanette Sperhac (SDSC, UCSD),
Nicholas J. Kisseberth (Purdue), and other contributors.  The
"January 2025 status" doc (`hzdocs/hubzero-metrics todo.md`)
summarized the state at that point and set the agenda the rewrite
later executed against.

## 2026 — Current rewrite (this repo)

In 2026-05 a Purdue-hosted hub became the first deployment target for
a focused Python rewrite.  Goals:

- Replace `tests/legacy/*` with a single `hzmetrics.py` file.
- Bug-for-bug compatible with the legacy code at the metric-value
  level (verified by A/B harness).
- Built-in catch-up so a stalled pipeline can be left to drain
  unattended.
- Fix the `xlogfix_summary.php` period-14 performance blowup by
  introducing the indexed `dnload` column.
- Replace the `host(1)` per-IP DNS shell-out with `aiodns`.

Notable mile markers preserved in commit history:

- **`Pipeline refactor: hzmetrics.py manager, dnload column, schema
  migrations, backlog catch-up`** — the first commit introducing the
  Python pipeline manager, the `dnload` column, the `migrations`
  table, and the backlog-catchup loop.
- **`Move legacy reference code into tests/legacy/`** — the synthetic
  commit that relocates the original PHP/Perl/Bash scripts into a
  reference location.  After this commit, edits to legacy files are
  rare and explicitly for testability.
- **`A/B harness step 1: restore legacy scripts + HZMETRICS_ACCESS_CFG`**
  through **`A/B re-baseline: legacy is now aa245f7^ (TRUE
  pre-refactor commit)`** — building out the A/B test harness against
  the pristine pre-refactor legacy as the wire-format reference.
- Per-port test commits (`A/B test: fill-domain`, `A/B test:
  middleware-{wall,cpu} — caught three real divergences`, etc.) —
  each documents real divergences caught by the harness during the
  port.

`hzmetrics.py` reached A/B parity in late 2026-05.  See
[architecture.md](architecture.md) for the current shape of the
pipeline and [testing.md](testing.md) for the test infrastructure.

## What was left behind on purpose

- **The Truth/Provisional/Production log staging dance** from the
  hub-specific custom metrics layer.  Simple `daily/` → `imported/`
  with a state file and a single-process lock is enough.
- **Celery + Redis from `hubzero-analytics`**.  See above.
- **Live-DB Redis caches** for per-resource usage plugins.  The
  database is fast enough now; the cache is unnecessary complexity.
- **Multiple competing usage-overview plugins** in the CMS
  codebase.  The rewrite only writes the summary tables; the UI side
  is downstream and out of scope.

These are documented here to make clear that "not in the rewrite" is
not the same as "we forgot."  Each was a deliberate decision.
