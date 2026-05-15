# Motivations

The hzmetrics rewrite exists for one main reason and several
contributing ones.  This document explains what was broken with the
legacy pipeline that justified the work, and what the rewrite did and
didn't try to change.

## The main reason: it stopped keeping up

The legacy pipeline grew at the same time the web grew, and the web
won.

By 2024–2025 a daily metrics run on a busy hub took multiple hours.
The slowest single step — the all-time period aggregation in
`xlogfix_summary.php` — would run for **10+ hours and sometimes crash
MariaDB**.  The bottleneck was a `SELECT ... WHERE content LIKE ... OR
content LIKE ... [several dozen patterns]` chain against the `web`
table, which on a mature hub has 30M–500M rows.  No index helps a
`LIKE %x%` chain; every all-time summary scanned everything.

Reverse DNS was the second hot spot.  The legacy `xlogfix_dns_v2.sh`
shells out to the `host(1)` command per IP, one at a time.  Cold
resolution against the upstream resolver clocks in at **~294 ms per
IP**.  A monthly batch with tens of thousands of new IPs spends most
of an hour just on DNS.

Bot traffic made both problems worse.  In late 2024 cookie-retaining
crawlers inflated the largest hub's "unique visitors" count from a typical
~250,000/month to over 1.1M, all of which had to be ingested,
enriched, and then partially deleted by hand-maintained "clean-bots"
SQL scripts.  Some of those cleanup scripts spent three hours per
night trying to delete rows that hadn't existed for years.

By the time we reached a HUBzero hub at Purdue, three
operational facts were obvious:

1. Slow scripts on growing data become broken scripts.  The pipeline
   wasn't reliably finishing.
2. The hand-maintained PHP+Perl+Bash mix had become hard to reason
   about — three languages, multiple include files, dozens of cron
   entries, with each generation of patches layered on top of
   incomplete documentation from the last.
3. Catch-up was painful.  When the pipeline fell behind, no mechanism
   processed the backlog automatically — logs just piled up in
   `daily/` directories until somebody noticed.

## What the rewrite is and isn't

The rewrite is a focused **port and optimization pass**:

- **One Python file** (`hzmetrics.py`) replaces ~20 PHP/Perl/Bash
  scripts.  Same database schemas, same metric definitions, same
  output formats.  Bug-for-bug compatible with the legacy code at the
  numbers level — verified by an A/B test harness (see
  [testing.md](testing.md)).
- **`async` DNS via `aiodns`** replaces fork-per-IP shell-out.
  Concurrency=100 against the system resolver runs DNS at ~4 ms/IP;
  with a local `unbound` in front of it concurrency=500 drops to
  ~2 ms/IP cold and ~1 ms/IP warm — versus 294 ms/IP for the legacy
  approach.
- **Indexed `dnload` column** replaces the `LIKE`-chain download
  detection.  Set once at import time (or via a one-time
  `backfill-dnload` pass for historical data), then summary queries
  read `WHERE w.dnload = 1` — bounded by the index, not the row
  count.  Period 14 (all-time) is now minutes, not hours.
- **Single cron entry** replaces seven.  `hzmetrics.py tick` runs
  every 5 minutes; the per-stage scripts are now subcommands invoked
  inside one Python process.
- **Catch-up is built in**.  Each `tick` invocation does at most one
  month of backlog work (so a long-stalled host gradually drains the
  log queue), guarded by a PID lock and a daily-state file.
- **Schema is self-installing**.  A `migrations` table tracks applied
  schema deltas; `hzmetrics.py migrate --apply` brings any database
  up to current schema.  No more "did we run the SQL by hand on this
  host?"

The rewrite **isn't** a redesign of the metric definitions.  Every
existing reporting UI, downstream consumer, and grant-reporting query
keeps working with no changes.  Where the legacy code had quirks that
fall short of being bugs — implementation-defined orderings, slightly
weird date math edge cases, missing tie-breakers — we preserved them.
The A/B harness fails if the new code disagrees with legacy on any
row of any output table.

What we explicitly did **not** try to do:

- Solve the bot problem.  Bot mitigation lives at the firewall,
  `robots.txt`, and the `exclude_list` table — places the metrics
  pipeline reads from, not places it owns.  The rewrite makes the
  pipeline fast enough to absorb the current bot volume without
  falling over; reducing the bot volume itself is a separate
  operational concern.
- Add new metrics.  If a downstream consumer wants a new figure, that
  goes in a future PR with its own design discussion.
- Replace the live `whoisonline` map's "5-minute refresh of a static
  XML file" architecture.  That works fine and changing it would
  break the existing Google Maps widget.

## What we kept

A surprising amount of the legacy design is sound and worth keeping:

- **Two databases** (`<hub>` for live CMS state, `<hub>_metrics` for
  enriched analytics).  The split prevents the metrics pipeline from
  ever writing to anything the CMS reads in real time, with one
  pragmatic exception (`jos_session_geo` for the whoisonline map).
- **Period codes 0/1/3/12/13/14**.  Calendar year, month, quarter,
  rolling-12, fiscal year, all-time.  Stable, documented, and what
  the UI expects.
- **`rowid`/`colid`/`period`/`datetime`/`value` shape** of the
  summary tables.  Denormalized and a little quirky, but the existing
  reporting code is built around it and the shape is documented
  (see [usage-tables.md](usage-tables.md)).
- **Daily run rhythm**.  Process yesterday's logs overnight; recompute
  summaries for the current month each run; freeze last-month at
  end-of-month.

## Why Python (and not the abandoned Python-with-Celery-and-Redis attempt)

A previous attempt — `hubzero-analytics`, used on the largest hub — already
tried to replace the legacy pipeline with Python.  It introduced
Celery, Redis, and a parallel Truth/Provisional/Production directory
layout for log files.  It never fully replaced the legacy pipeline on
the hubs that adopted it (the largest hub still ran the original PHP/Perl
fetch/import scripts in parallel) and was effectively abandoned for
the open-source HUBzero distribution.  See [history.md](history.md)
for more on that.

This rewrite is deliberately the opposite shape: **one file, no
broker, no daemon, no background workers**.  The pipeline is short
enough and the data volume is small enough (compared to web-scale
workloads) that `cron` + a PID lock + a state file is a fully
adequate scheduler.  The complexity that doomed `hubzero-analytics`
isn't justified by the problem.

Python over PHP/Perl was driven by:

1. **`asyncio` + `aiodns`** — the single biggest performance win
   (DNS) was natively expressible.
2. **`pymysql`** is straightforward, the standard library has
   everything else.  No framework, no ORM.
3. **Operability**: one file, standard `argparse` CLI, `--dry-run`
   mode on every mutating subcommand, structured logging to a single
   file.  Easier to grep, easier to run in production by hand when
   needed.
4. **Testability**: the A/B harness can call `python3 hzmetrics.py
   import-apache file.log` as easily as it can call `php
   xlogimport_apache.php file.log`.  The wire-equivalence test is
   exactly that: same inputs, diff the database states.
