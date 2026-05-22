# Glossary

Quick definitions of terms that appear across these docs.  Each entry
is one or two sentences.

### Hub / science gateway
A web-accessible portal that hosts computational tools for a research
community.

### HUBzero
The CMS framework hubs are built on.  Open-source PHP with custom
plugins, hosted at hubzero.org, sourced at github.com/hubzero.

### Hub DB (`<hub>`)
The live CMS database.  Owned by HUBzero CMS.  Metrics reads it but
generally doesn't write to it (the exceptions are
`jos_resource_stats*` and `jos_session_geo`).

### Metrics DB (`<hub>_metrics`)
The analytics database.  Owned end-to-end by the metrics pipeline.
Contains `web`, `websessions`, `toolstart`, `userlogin`,
`summary_*_vals`, and the static reference tables (`continents`,
`countries`, `domainclass`, etc.).

### Web row
One row in `web` represents one Apache HTTP request.  Created by
`import-apache`.  Enriched by `resolve-dns` / `fill-domain` /
`fill-ipcountry` / `logfix-session`.

### Web session
One row in `websessions` represents a coalesced visitor session — a
sequence of `web` rows from the same `ip` + `host` within 1800 seconds
of each other.  Created by `logfix-session`.

### Tool start
One row in `toolstart` represents a user launching a computational
tool.  Created from `<hub>.sessionlog` by `import-hub-data` and
enriched with walltime / cputime by `middleware-wall` / `middleware-cpu`.

### Simulation
In metrics terms, a tool-launch event that resulted in a job being
submitted.  Counted by `summary_simusage_vals` and the `sim_users()`
function.  Distinct from a "view-only" tool session.

### Period
The time window a summary cell represents.  Six codes:

| Code | Span |
|:---:|:---|
| `0`  | Calendar year containing `datetime` |
| `1`  | The month itself |
| `3`  | Quarter containing `datetime` |
| `12` | Rolling 12 months ending at `datetime` |
| `13` | Fiscal year (Oct–Sep) containing `datetime` |
| `14` | All time (since 1995-01-01) |

### datetime convention
Summary tables use `'YYYY-MM-00 00:00:00'` for monthly anchors —
zero-padded day to mark "this is a month, not a specific date".
Period 14 (all-time) uses `'0000-00-00 00:00:00'` in some legacy
layouts; the rewrite preserves whichever the legacy used.

### rowid / colid
The two indexes into a summary cell.  `rowid` is the metric (e.g.,
"registered users", "simulation jobs", "domains served"); `colid` is
the breakdown axis (1 = total; 2–6 = residence by continent; 7–11 =
org type).  Each `summary_*_vals` table has its own `rowid` semantics;
see [usage-tables.md](usage-tables.md).

### Domain class
A six-bucket categorization of internet domains used for the org-type
breakdown.  Stored in the `domainclass` reference table:

| Class | Meaning |
|:---:|:---|
| `0`   | Unknown (no domain information) |
| `1`   | Educational institution |
| `2`   | Industrial / corporate |
| `3`   | Governmental |
| `4`   | Internet service provider |
| `5`   | Search engine |
| `6`   | Press / media / publication |

The mapping is hand-maintained.  Most of the entries date to 2015;
periodic refresh is recommended but rarely happens.  `fill-domain`
sets the domain on each `web` / `websessions` / `toolstart` row;
`fill-user-info` and the summary `int_users()` / `reg_users()`
functions roll the domain up into the colid 8–11 buckets.

### Registered vs unregistered (guest) user
- **Registered**: appears in `userlogin` (i.e., logged into the CMS).
  Org / residence is taken from their `jos_xprofiles` profile if they
  filled it out, otherwise treated as "unknown".
- **Unregistered** (or "guest"): not logged in.  Identified by
  `(ip, host)` pair in `websessions`.  Org / residence is inferred
  from the resolved hostname → domain → `domainclass` lookup.

Some hub deployments are anonymous-dominant — registered accounts
in the single digits — so their metrics are essentially all
guest-user inference.

### Tool top / toplist / "top" code
`jos_resource_stats_tools_topvals` and `jos_stats_topvals` store
ranked lists of tools by various metrics.  The `top` column codes the
metric:

| `top` | Tool metric |
|:---:|:---|
| `2`  | Number of users |
| `5`  | Number of jobs |
| `6`  | Walltime |
| `7`  | Simulation CPU time |
| `8`  | Simulation interaction time |
| `10` | Number of courses |
| `11` | Course user count |

`rank=0` is the special "total across all tools" row for that
`(top, period, datetime)` triple.  `rank=1, 2, 3, …` are the
individual tools in descending order.

### dnload
A boolean (`TINYINT 0/1`) column on the `web` table indicating
whether the row represents a resource download.  Set inline by
`import-apache` for new rows; `backfill-dnload` populates the
historical rows.  Introduced by the rewrite to replace a slow `LIKE`-
chain in `xlogfix_summary.php`'s download detection.

### Login IPs / login_ips_tmp
A temp table built at summary time, indexing every IP that appears
in `userlogin_lite`.  Used as the "registered user" set against
which `websessions.ip NOT IN (...)` filters identify unregistered
visitors.  The rewrite materializes this as an indexed JOIN target
instead of an in-memory comma list, dramatically speeding up the
all-time aggregation.

### whoisonline
The live "who is currently online" widget.  Reads
`<hub>.jos_session` every 5 minutes, looks up reverse DNS + GeoIP for
new IPs, writes `<hub>.jos_session_geo` and
`/var/www/<hub>/app/site/stats/maps/whoisonline.xml` for the Google
Maps widget on the public usage page.  Real-time-ish, but a separate
concern from the daily metrics pipeline.

### Tick
The cron entry point — `hzmetrics.py tick` — that runs every 5
minutes.  Always refreshes whoisonline; at `:30` past the hour, also
invokes `cmd_run` (which acquires the flock and dispatches by mode).

### Orchestrator mode
The string in `pipeline_state.mode` that controls what each `tick`
does.  One of:

- `normal`: steady-state.  Import today's pending logs; summarize the
  previous month when it's fully imported.
- `catchup`: process one backlog month per tick, applying the
  per-month decision matrix.  Summarize with `periods=(1,)` only.
- `rebuild`: walk `rebuild_cursor` through prev-month, re-summarizing
  each with all six periods to fix long-window cells that catchup
  left stale.

Transitions are computed at the start of every tick from filesystem
+ DB state, not stored across ticks.  See
[architecture.md → Catchup orchestration](architecture.md#catchup-orchestration-state-machine).

### Backlog month
A month strictly before the current calendar month that either has a
pending source log or has DB rows with an incomplete summary.  The
orchestrator picks the oldest backlog month at each catchup tick.

### Decision matrix (catchup)
Three-helper test used to route a backlog month into one of five
branches: `month_has_source(m)`, `month_has_data(m)`,
`is_month_fully_summarized(m)`.  The table is documented in
[architecture.md → The per-month decision matrix](architecture.md#the-per-month-decision-matrix).
The unusual cell — `source ✗ data ✓` — is what handles months whose
DB rows survived an old import but whose source files are gone.

### `pipeline_state`
Key/value table in the metrics DB that stores orchestrator state:
`analyzed`, `mode`, `catchup_started`, `rebuild_cursor`, `dirty_months`.
Replaces the pre-2026 `/var/run/hzmetrics/hzmetrics.state` file that
sat on tmpfs and was lost on every reboot.  Multi-key writes are
atomic via single `INSERT … ON DUPLICATE KEY UPDATE`.

### Migration (schema)
A row in `<hub>_metrics.migrations` recording an applied schema
delta.  `hzmetrics.py migrate --apply` walks the unapplied migrations
in order and runs them.  Standard schema-migration pattern.

Each migration carries a `check_sql` against `information_schema` (or
data state) that lets it auto-detect "already applied" — important
because the DDL in `setup-db` now bakes in the post-migration state,
so a fresh install marks every migration applied without re-running
them.

### `access.cfg`
`/opt/hubzero/metrics/conf/access.cfg`.  Bare `$var = 'value';` PHP-style
file with DB credentials.  Owned `root:apache` mode 640.  Read by
`hzmetrics.py`, the legacy Perl scripts, and the test harness (via
`HZMETRICS_ACCESS_CFG` env var).

### exclude_list
Per-hub table in the metrics DB.  Filters bots / scanners / utility
traffic out of metrics processing.  See
[operations.md](operations.md) for ops use.

### A/B parity / bug-for-bug parity
The rewrite's parity contract: the new code's output tables must be
byte-identical to the legacy code's output tables for the same input
fixtures.  Verified by `tests/ab/run-all.sh`.  Preserved even where
the legacy had quirks (NULL vs empty-string, implementation-defined
ordering with no tie-breaker, etc.) — those are documented and
matched.

### `_findweeks` / week-chunking
A legacy pattern from `xlogfix_summary.php` / `xlogfix_domain.php`:
break a month into ~4 week-sized scans for memory-bounded
enrichment.  Has a known quirk — each week-chunk starts on the day
**before** the month begins.  So July 2025's first chunk runs from
`2025-06-30` to `2025-07-07`, not from `2025-07-01`.  Preserved
bug-for-bug by the rewrite.

### Banker's rounding (the middleware port quirk)
MariaDB's `ROUND()` on a `DOUBLE` column uses round-half-to-even
(banker's rounding) — `ROUND(200.5) → 200`.  Perl's `int($x + 0.5)`
in `xlogfix_middleware_{wall,cpu}.pl` is round-half-up — `200.5 →
201`.  The rewrite uses `FLOOR(x + 0.5)` instead of `ROUND()` to
mirror the Perl semantics exactly.  Caught by the
`port_middleware` A/B test.

### `gridstat` / `hctest_` user filter
Two patterns of "test account" excluded from metrics processing
across the legacy code.  `gridstat` is an **exact-string** match
(so `gridstatx` is NOT excluded).  `hctest%` is a LIKE pattern (so
`hctest`, `hctest_x`, `hctestlonger`, `hctester` are all excluded).
Preserved bug-for-bug.

### Unflushed last session
A legacy quirk in `logfix_session.pl`: the very last session of
each run never gets emitted to `websessions` if no later row
triggers the "session end found" path.  Preserved by the rewrite —
the `port_logfix_session` test fixture exercises it explicitly.

### Two paths to MySQL `INT` rounding
Yet another preserved quirk.  The legacy PHP stringifies values
before binding into SQL, so MariaDB applies half-away-from-zero
rounding when casting a float-string to INT: `'488.5' → 489`.  The
Python port's `pymysql` originally bound Python floats as numeric
literals, hitting banker's rounding: `488.5 → 488`.  The fix:
stringify Python floats before binding.  Caught by the
`port_gen_tool_stats` A/B test.
