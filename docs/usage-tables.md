# Summary table cheat sheet

The five `summary_*_vals` tables in the metrics database are the
output of the pipeline and the input to the usage-overview UI.  This
document is a decoder for what their fields mean.

It is closely adapted from J.M. Sperhac's
"Hub usage data overview and table translator" (Jan 2025), which
remains the most readable reference for the legacy `xlogfix_summary.php`
that the rewrite ports.  The output values are identical — the
rowid/colid/period/datetime/value/valfmt shape is preserved bit-for-bit
by the rewrite.

## Common shape

All summary tables have the same six columns:

```
( rowid  TINYINT,    -- metric index, table-specific meaning (see below)
  colid  TINYINT,    -- breakdown index (1=total, others=continent/orgtype)
  datetime DATETIME, -- 'YYYY-MM-00 00:00:00' for monthly anchors;
                     -- '0000-00-00 00:00:00' for all-time
  period TINYINT,    -- 0=year, 1=month, 3=quarter, 12=rolling-12mo,
                     -- 13=fiscal year (Oct-Sep), 14=all-time
  value  VARCHAR(200), -- the metric value (numeric-as-string)
  valfmt TINYINT )   -- 1=count, 2=percent, 4=jobs, 5=duration in seconds
```

The denormalized shape is awkward — the same `colid` column means
"residence" for one breakdown and "organization type" for another —
but every reporting tool is built around it.  The rewrite preserves
this exactly.

## Period codes

| Period | Meaning |
|:------:|---------|
| `0`    | Calendar year containing `datetime` |
| `1`    | Just that month |
| `3`    | Quarter containing `datetime` |
| `12`   | Rolling 12 months ending at `datetime` |
| `13`   | Fiscal year (Oct–Sep) containing `datetime` |
| `14`   | All time (since `1995-01-01`) |

Period 14 is the historically-expensive one — see
[motivations.md](motivations.md) for what the rewrite did about it.

## colid (breakdown axis)

Same meaning across `summary_user_vals` and `summary_simusage_vals`:

| colid | Meaning |
|:-----:|---------|
| `1`   | **Total** (always — used by single-value metrics) |
| `2`   | Residence "identified" — total with known continent |
| `3`   | Residence — United States |
| `4`   | Residence — Asia |
| `5`   | Residence — Europe |
| `6`   | Residence — Other |
| `7`   | Organization "identified" — total with known org type |
| `8`   | Organization — Educational |
| `9`   | Organization — Industry |
| `10`  | Organization — Government |
| `11`  | Organization — Other |

`summary_misc_vals` rows are total-only and all use `colid=1`.

When `valfmt=2` (percent) the value is the column count, and the UI
divides by the colid=2 or colid=7 "identified" total to render
percentages.

## `summary_user_vals` rowids

User counts.  Critical invariant: **`rowid=1` (total) equals
`SUM(rowid IN (6, 7, 8))`** for every `(datetime, period, colid,
valfmt)` cell.  This is asserted by `tests/ab/port_invariants/`.

| rowid | Metric                     | colid=1 means                  | Breakdowns |
|:-----:|----------------------------|--------------------------------|------------|
| `1`   | Total users (= 6+7+8)      | All user-IP-host visits         | Residence, Org |
| `2`   | Simulation users           | Distinct sim users              | Residence, Org |
| `3`   | Unregistered user IPs (raw)| Pre-filter unregistered count   | Total only |
| `4`   | Unique download user IPs   | Pre-filter download count       | Total only |
| `5`   | UNUSED                     | —                               | — |
| `6`   | Registered users           | Distinct users in `userlogin_lite` | Residence, Org |
| `7`   | Unregistered users (visitors) | Distinct visitors from `websessions` (duration ≥ 900s, jobs=0, ip ∉ login_ips) | Residence, Org |
| `8`   | Download users             | Distinct downloaders (filtered) | Residence, Org |

## `summary_simusage_vals` rowids

Tool / simulation usage.  Different meanings of `rowid` from
`summary_user_vals`.

| rowid | Metric                     | valfmt  | Notes |
|:-----:|----------------------------|---------|-------|
| `1`   | Total simulation users     | 1 count | — |
| `2`   | Simulation jobs            | 4 jobs  | `COUNT(*)` over `toolstart` with `success=1` |
| `3`   | CPU time                   | 5 sec   | `SUM(cputime)` |
| `4`   | Wall time                  | 5 sec   | `SUM(walltime)` |
| `5`   | View time                  | 5 sec   | `SUM(viewtime)` |
| `6`   | Users with ≥10 min CPU     | 1 count | — |
| `7`   | Average jobs per user      | 1 count | Computed from #2/#1 |
| `8`   | Average wall per user      | 5 sec   | Computed from #4/#1 |
| `9`   | Repeat users with ≥10 sims | 1 count | — |
| `10`  | Repeat users >3 months     | 1 count | — |

## `summary_misc_vals` rowids

Miscellany.  All `colid=1`.

| rowid | Metric                     | valfmt  | Notes |
|:-----:|----------------------------|---------|-------|
| `1`   | Domains served             | 1 count | `COUNT(DISTINCT domain)` |
| `2`   | Cumulative user sessions   | 1 count | — |
| `3`   | Cumulative session time    | 5 sec   | `SUM(duration)` — NULL is written as empty string (legacy quirk preserved by the port) |
| `4`   | Visitor count              | 1 count | `COUNT(DISTINCT ip, host)` |
| `5`   | Visit count                | 1 count | `COUNT(datetime)` |
| `6`   | New user accounts          | 1 count | From `jos_xprofiles_metrics.registerDate` |
| `7`   | Max user logins on a day   | 1 count | `'N users on YYYY-MM-DD'` — value is a formatted string |
| `8`   | Web server hits            | 1 count | `SUM(hits)` from `webhits` |

## `summary_andmore_vals`

Per-resource user counts, written by `andmore-usage` (the only
summary writer that has its own table).

```
( resid  INT,        -- jos_resources.id (the resource being counted)
  period TINYINT,    -- 1, 12, 14 only — see below
  datetime DATETIME, -- 'YYYY-MM-00 00:00:00'
  users  INT,        -- count of distinct users
  valfmt TINYINT )
```

**`andmore-usage` only writes periods 1, 12, and 14.**  It doesn't
produce a full period grid like the other summary tables — that's
inherited from the legacy `xlogfix_andmore_usage.php` and preserved
by the port.

## Hub-side: `jos_stats_topvals` and `jos_resource_stats_tools_topvals`

The per-tool ranked toplists, written into the hub DB (not the
metrics DB) by `gen-tool-toplists` / `gen-tool-tops`.

```
( id        INT (auto),
  top       TINYINT,     -- which tool metric (see table below)
  datetime  DATETIME,    -- 'YYYY-MM-00 00:00:00'
  period    TINYINT,     -- one of the six period codes
  rank      SMALLINT,    -- 0 = total across all tools; 1, 2, 3 ... ranked tools
  name      VARCHAR(255),-- 'Total Simulation Jobs' at rank=0; '<resid> ~ <title>' otherwise
  value     BIGINT )
```

The `rank=0` row is the special "total across all tools" row for
that `(top, period, datetime)` triple.  Reporting UIs (e.g.,
`hub.org/usage/tools/12`) display the totals as the headline number
and the ranks 1+ as the per-tool list below.

### `top` codes

| `top` | Tool metric |
|:---:|:---|
| `2`  | Number of users |
| `5`  | Number of jobs |
| `6`  | Walltime |
| `7`  | Simulation CPU time |
| `8`  | Simulation interaction time |
| `10` | Number of courses |
| `11` | Course user count |

(From J.M. Sperhac's "Hub tool stats summarized" reference.)

## Reference: `domainclass` table

The 6-bucket categorization that drives the orgtype breakdown in
`summary_user_vals` colid 8–11 and `summary_simusage_vals` same.
Stored in `<hub>_metrics.domainclass`:

| Class | Meaning |
|:---:|:---|
| `0`   | Unknown (no domain information) |
| `1`   | Educational institution |
| `2`   | Industrial / corporate |
| `3`   | Governmental |
| `4`   | Internet service provider |
| `5`   | Search engine |
| `6`   | Press / media / publication |

The mapping is hand-maintained.  Most of the entries date to 2015
(see Sperhac's "Organization type and location" reference).  Periodic
refresh is recommended but rarely happens — adding a new
educational, government, or industry domain to this table is what
makes that domain show up in the right org-type bucket on the
usage page.

## How registered vs guest users are classified

Two paths into the colid 2–11 breakdowns:

**Registered users** (`reg_users`, rowid=6).  Identified by
appearing in `userlogin_lite` (= filtered view of `userlogin` for
login + simulation actions).  Org type and residence taken from
their `jos_xprofiles_metrics` profile (`orgtype` and
`countryresident` columns).  If they didn't fill out the profile,
they roll up into "unknown" (colid=2 and 7 — i.e., they don't
contribute to any of 3–6 or 8–11, only to the colid=1 total).

**Guest / unregistered users** (`int_users`, rowid=7;
`download_users`, rowid=8).  Identified by `(ip, host)` in
`websessions` with no matching login row.  Org type from
`domainclass` lookup on the resolved domain.  Residence from
`fill-ipcountry`'s GeoIP, mapped to continent via `country_continent`.

The some deployments is a special case: about 10 registered accounts
total (the maintenance staff), almost all visitors are anonymous.
So its summary_user_vals is essentially all rowid=7 (unregistered)
and rowid=8 (download users), with rowid=6 (registered) nearly
zero.  Most hubs have a richer registered-user population.

## Example queries

These work against the live `<hub>_metrics` database and are exactly
what the usage-overview UI does behind the scenes.

**Total user visits, monthly time series:**

```sql
SELECT datetime, value
FROM summary_user_vals
WHERE rowid = 1     -- total users
  AND colid = 1     -- total breakdown
  AND period = 1    -- monthly
ORDER BY datetime;
```

**All-time totals with continent breakdown for March 2025:**

```sql
SELECT colid, value, valfmt
FROM summary_user_vals
WHERE rowid = 1
  AND period = 14
  AND datetime = '2025-03-00'
ORDER BY colid;
```

(`colid=3` is the US count, `colid=4..6` are Asia/Europe/Other.  Any
of those that are `valfmt=2` should be displayed as `value /
colid=2_total * 100`%, see the legacy `default.php` view for the
exact rendering.)

**Tool jobs (period 12 = rolling 12mo):**

```sql
SELECT datetime, value
FROM summary_simusage_vals
WHERE rowid = 2     -- simulation jobs
  AND colid = 1
  AND period = 12
ORDER BY datetime;
```

## Things that surprise people

- `colid=2` and `colid=7` are "identified" rolled-up counts — they
  represent the population for which residence or org type is known,
  and act as denominators for the percent breakdowns at `colid 3..6`
  and `8..11`.
- The same `colid` column number means different things depending on
  whether you're looking at `3..6` (residence) or `8..11`
  (organization).  Both axes share the same column.
- `summary_misc_vals.rowid=7` stores a formatted string like
  `'42 users on 2025-07-15'`.  The integer value is parseable from
  the first token; the date is parseable from the last.
- `summary_misc_vals.rowid=3` (cum session time) writes an empty
  string when `SUM(duration)` returns NULL on an empty window — a
  legacy quirk preserved for bug-for-bug parity (see
  `port_period_sweep` in the test harness for the case that surfaced
  it).
- `userlogin_lite` (input to `int_users` and similar) has **no date
  filter** in legacy.  Counts derived from it grow monotonically over
  time independent of the period being summarized.  This is a known
  design issue preserved for parity.
- `summary_andmore_vals` only has periods 1/12/14 (not the full 6).
