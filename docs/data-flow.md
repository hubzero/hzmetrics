# Data flow: one log line through the pipeline

This document traces a single Apache HTTP request from the raw log
file all the way through to a cell in `summary_user_vals`.
Concrete example, end to end.

## Starting point: a raw Apache log line

```
2025-07-10 14:23:11 EDT 12345 - "GET /resources/345/download/foo.zip HTTP/1.1"
  200 102400 203.0.113.42 "https://www.google.com/" "Mozilla/5.0 ..."
  TLSv1.3 0 102400 - - - - - - - - -
```

Sitting in `/var/log/httpd/daily/<hub>-access.log-20250710` after
the host's logrotate ran at midnight.

## Stage 1 — fetch (`tick` → `run` → `import`)

`tick` runs every 5 minutes.  At `:30`, it acquires the PID lock and
calls `run`.  `run` walks the daily logs oldest-first:

```
/var/log/httpd/daily/<hub>-access.log-20250710     ← target
/var/log/hubzero/daily/cmsauth.log-20250710        ← same day
```

`fetch` concatenates the day's access logs into a staging file:

```
/var/log/hubzero/metrics/_hub_apache.log
/var/log/hubzero/metrics/_hub_auth.log
```

## Stage 2 — import-apache

`hzmetrics.py import-apache /var/log/hubzero/metrics/_hub_apache.log`
reads the staging file line by line.

For our log line:

1. **Regex match.** Two regexes: `_APACHE_PAT_NEW` (23 fields,
   current Apache layout) and `_APACHE_PAT_OLD` (14 fields, kept for
   archived logs).  Our line matches NEW.
2. **Extract fields.** Method = `GET`, status = `200`, bytes =
   `102400`, IP = `203.0.113.42`, URL = `/resources/345/download/foo.zip`.
3. **Method filter.** GET or POST only.  ✓
4. **Status filter.** `status == 200 && bytes > 0`.  ✓
5. **URL exclusion filter.**  Checked against `exclude_list` for IP /
   useragent / URL hits.  The URL would normally also be filtered by
   the suffix list (`.gif|.css|.js|...`), except that
   `/resources/.*` overrides that check.  ✓
6. **Bot check.** Useragent looked up in `bot_useragents`.  Not a
   bot.  ✓
7. **dnload detection.** Matches `/resources/.*/download/.*` →
   `dnload = 1` set inline.
8. **INSERT.** A new row in `<hub>_metrics.web`:

```
datetime: '2025-07-10 14:23:11'
ip: '203.0.113.42'
content: '/resources/345/download/foo.zip'
useragent: 'Mozilla/5.0 ...'
referrer: 'https://www.google.com/'
apache_pid: '12345'
uidNumber: 0          -- not logged in
joomla_sessionid: ''   -- column name is literal in the schema
dnload: 1             -- ← downloaded resource
host: NULL            -- filled in by resolve-dns later
domain: NULL          -- filled in by fill-domain later
ipcountry: NULL       -- filled in by fill-ipcountry later
sessionid: 0          -- filled in by logfix-session later
```

## Stage 3 — analyze (enrichment chain)

`run` invokes `analyze` for the most recent month.  Each enrichment
step processes the rows still missing its target column — restartable,
idempotent.

### resolve-dns

```
[resolve-dns] web 2025-07: 4823 rows missing host
[resolve-dns] aiodns concurrency=100, system resolver
```

For `203.0.113.42`, aiodns does a PTR lookup.  Suppose it resolves to
`crawl-203-0-113-42.example.com`.  The row's `host` column is
filled in.

### fill-domain

```
[fill-domain] web 2025-07: 4823 rows missing domain
```

Strips subdomains from `host` to get a registrable domain via the
`get_domain()` rules (handles `.com / .net`, country-code TLDs,
`.k12.va.us`, etc.).  For our example: `host` =
`crawl-203-0-113-42.example.com` → `domain` = `example.com`.

The `domain` is then looked up in the `domainclass` reference table:

```
SELECT class, country FROM domainclass WHERE domain = 'example.com';
-- (no match for this hypothetical example → class stays unknown)
```

A match would set the orgtype bucket (1=Edu, 2=Industry, 3=Gov,
4=ISP, 5=SearchEngine, 6=Press).  No match leaves it unset, which
later rolls up as "unknown" in the colid=7 breakdown.

### fill-ipcountry

```
[fill-ipcountry] web 2025-07: 4823 rows missing ipcountry
```

For `203.0.113.42`, calls `help.hubzero.org/ipinfo/v1` (or the
configured GeoIP service) → returns `'US'`.  Row's `ipcountry` is set
to `'US'`.

US falls into colid 3 (Residence — United States) when this row
is later aggregated.  Other continents go to 4 (Asia) / 5 (Europe) /
6 (Other) via the `continents` reference table mapping.

### logfix-session

```
[logfix-session] 2025-07: scanning web for new session boundaries
```

Walks `web` ordered by `(ip, host, datetime)`.  Coalesces sequential
rows from the same `(ip, host)` within 1800s into a single
`websessions` row.

For our row, say there were 5 other requests from the same IP that
day, all within 5 minutes of each other.  They all get the same
`sessionid` and aggregate into one `websessions` row:

```
id: 50001
datetime: '2025-07-10 14:23:11'   -- first request in the session
ip: '203.0.113.42'
host: 'crawl-203-0-113-42.example.com'
domain: 'example.com'
ipcountry: 'US'
duration: 287                      -- seconds from first to last
webevents: 6                       -- requests in this session
jobs: 0                            -- no tool launches
```

The 6 `web` rows then get `sessionid = 50001`.

### clean-bots

```
[clean-bots] 2025-07: scanning for exclude_list hits
```

If `example.com` (or `crawl-203-0-113-42.example.com`) matches an
entry in `exclude_list`, this `websessions` row and its 6 `web` rows
get DELETEd.  Otherwise the row stays.

### gen-tool-stats / gen-tool-tops / gen-tool-toplists

Operates on `<hub>.sessionlog` and `<hub>.joblog`, not on `web`.
This particular log line was a resource download, not a tool launch,
so it doesn't contribute to tool stats.  See [glossary.md](glossary.md)
for the tool-stats path.

### andmore-usage

Iterates the published resources in `<hub>.jos_resources`.  Resource
345 has `path = 'resources/345/'` (or similar pattern).  The
URL-match scan finds our `web` row (its `content` includes
`/resources/345/download/`), counts our `ip` as a unique user of
resource 345 during this period.

A row in `<hub>.jos_resource_stats` is written/updated:

```
resid: 345
restype: 7              -- 7 = tool / resource in jos_resources
period: 1, 12, 14       -- written for these three periods only
datetime: '2025-07-00'
users: <incremented>
```

(The legacy `xlogfix_andmore_usage.php` writes only periods 1, 12,
14 — not the full 6.  The rewrite preserves this.)

## Stage 4 — summarize

`summarize --month 2025-07` re-aggregates every cell of the summary
tables.

For our `web` row to land in a count, it has to be picked up by one
of the summary functions:

### `total_users` / `int_users` (unregistered visitors)

```sql
-- Approximate: actual query is in hzmetrics.py _summary_int_users().
SELECT COUNT(DISTINCT ip, host) FROM websessions
WHERE datetime > '2025-07-01' AND datetime < '2025-08-01'
  AND duration >= 900 AND jobs = 0
  AND ip NOT IN (SELECT ip FROM login_ips_tmp);
```

Our session's `duration` is 287 (< 900), so it's NOT counted by the
"interactive visitor" measure.  But by the "any visitor" measure
(`duration >= 0 OR jobs > 0` — used in `summary_misc_vals.rowid=4`),
it IS counted.  The visitor count for July 2025 increments by 1.

### `download_users`

```sql
-- Approximate.
SELECT COUNT(DISTINCT ws.ip, ws.host) FROM websessions ws, web w
WHERE w.sessionid = ws.id
  AND ws.datetime > '2025-07-01' AND ws.datetime < '2025-08-01'
  AND ws.duration >= 0 AND ws.duration < 900
  AND ws.jobs = 0
  AND ws.ip NOT IN (SELECT ip FROM login_ips_tmp)
  AND w.dnload = 1;
```

Our session has `web.dnload = 1` (we set it in import-apache), so the
session counts toward the download-user count.  `summary_user_vals`
rowid=8 (download users) increments by 1 in the colid=1 (total) and
colid=3 (US) buckets.

This same query runs 6 times — once per period (0, 1, 3, 12, 13, 14)
— with the date-range bounds shifted accordingly.

### Why the rewrite is faster than legacy here

The legacy `download_users()` query had `WHERE (w.content LIKE
"/resources/%/download/%" OR w.content LIKE "%.zip" OR …)` instead
of `w.dnload = 1`.  On a hub with 30M+ `web` rows, the all-time
period (no date filter) had to LIKE-scan everything.  The
`dnload` column is a regular `TINYINT(1)` with a one-row check —
the query goes from "table scan, 10+ hours, sometimes crashes
MariaDB" to "indexed lookup, minutes."

## Final state

Our one log line contributed to:

- 1 row in `<hub>_metrics.web` (with `dnload=1`, `sessionid=50001`)
- 1 row in `<hub>_metrics.websessions` (with 5 sibling requests)
- Increment of `<hub>_metrics.summary_misc_vals` rowid=4 (visitors)
  and rowid=8 (web server hits) for July 2025, colid=1
- Increment of `<hub>_metrics.summary_user_vals` rowid=8 (download
  users) for July 2025, colid 1 and 3
- Increment of `<hub>.jos_resource_stats` users count for resource
  345

That last one is what appears on the resource's "Usage" tab and
contributes to the toplists on `hub.org/usage/tools/12`.

## Same line, registered user variant

Almost identical, except:

- Apache log line has the username field filled in (not `-`).
- The CMS auth log for that day has matching `login` /
  `sim_login` events for the user, populating `userlogin`.
- `import-hub-data` joins through `<hub>.jos_xprofiles` to populate
  `jos_xprofiles_metrics` with profile fields (`orgtype`,
  `countryresident`).
- At summary time, the user's IP appears in `login_ips_tmp`, so
  their `websessions` rows are excluded from the
  unregistered/guest counts and counted instead under `reg_users`
  (rowid=6).
- Their org type / residence comes from their profile (if filled
  out), not from domain lookup.

The two paths converge at `summary_user_vals` — both contribute to
the total at `rowid=1, colid=1`, just through different rowids.
