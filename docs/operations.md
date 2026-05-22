# Operations runbook

Common ops tasks for a deployed `hzmetrics.py` pipeline.  This is the
practical follow-on to [deployment.md](deployment.md) — once it's
running, this is what to do when something looks off.

The conventional sanity-check pattern goes back to the original
HUBzero pipeline circa 2014–2016 (Sperhac's "Basic new-month checks
for Usage Metrics Processing" runbook).
The mechanics are different now but the questions to ask are the
same.

## Knobs the pipeline honors

A few ops-relevant defaults are overridable without editing
`/opt/hubzero/metrics/conf/`:

| Env var | What it overrides | Default |
|---|---|---|
| `HZMETRICS_LOG` | Pipeline log file path | `/var/log/hubzero/metrics/manage.log` |
| `HZMETRICS_ACCESS_CFG` | DB credentials cfg path | `/opt/hubzero/metrics/conf/access.cfg` |
| `HZMETRICS_DNS_NAMESERVER` | resolve-dns nameserver | from `[dns]` section of hzmetrics.conf, then `system` |
| `HZMETRICS_DNS_CONCURRENCY` | aiodns concurrency | 100 |
| `HZMETRICS_DNS_TIMEOUT` | aiodns per-IP timeout (seconds) | 2.0 |

Useful for one-off runs that shouldn't touch the production log file
(e.g. a catch-up against a snapshot DB), or for the A/B harness.

Exit codes: `main()` propagates the handler's return code.  A daily
`cron` entry that's checking for success can rely on exit status; a
non-zero exit indicates an operational error (missing cfg, missing
hub_dir, mysql_exec failure, etc.).

## Is the install healthy?

```bash
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py doctor
```

Walks four phases and reports each:

  - `/etc/hubzero.conf` resolved a `site = <hubname>` line
  - `conf/access.cfg` present, parseable, and naming a metrics DB
  - every directory the pipeline writes into exists and is writable
    by the invoking user
  - MySQL reachable, `<hub>_metrics` exists, every known migration
    applied

`doctor` is a pure diagnostic by default — it logs `OK` / `FAIL`
lines and returns non-zero if anything failed.  Pass `--fix` to make
it call the same `_self_bootstrap` repair helpers cron uses on
startup: `mkdir -p` the missing dirs, `CREATE DATABASE IF NOT EXISTS`,
and apply every pending migration.  Things `doctor` can't fix from
its own privileges (missing `/etc/hubzero.conf` line, root-owned
parent of `/opt/hubzero/metrics`, MySQL down, bad DB credentials) are
reported but not attempted.

The same machinery also runs automatically at the top of `cmd_tick`
and `cmd_run` (gated on `os.geteuid()` mapping to `apache` /
`www-data`), so a freshly-installed hub with `access.cfg` in place can
go from cron-not-running to working pipeline on the next tick without
any manual setup-db / migrate step.

## Pipeline is running — am I getting fresh data?

```bash
# What does the pipeline think the world looks like?
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py status
```

Outputs the most-recent imported day, the most-recent summarized
month, and any pending log files in `daily/`.

```bash
# Latest data actually in the tables?
mysql -e "SELECT MAX(datetime) FROM web;"          # last imported log row
mysql -e "SELECT MAX(datetime) FROM userlogin;"    # last login event
mysql -e "SELECT MAX(datetime) FROM webhits;"      # last hourly aggregate

# Summary table coverage:
mysql -e "
  SELECT datetime, COUNT(*) FROM summary_user_vals
  GROUP BY datetime ORDER BY datetime DESC LIMIT 5;"
```

A healthy pipeline shows MAX dates close to yesterday in the import
tables, and a complete month's worth of summary rows for the most
recent completed month.

## Catching up from a backlog

The pipeline drains backlogs autonomously via the three-mode state
machine in `cmd_run` (see
[architecture.md → Catchup orchestration](architecture.md#catchup-orchestration-state-machine)
for the design).  Each `tick` checks `pipeline_state.mode` and
dispatches one tick's worth of work.  No operator intervention is
needed for routine backfill — just let the cron run.

```bash
# Where is the orchestrator?
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py status
```

`status` prints the mode (`normal` / `catchup` / `rebuild`), the
`catchup_started` anchor, and (in rebuild mode) the cursor plus
remaining-month count.  Example mid-catchup output:

```
=== orchestrator state ===
  mode             : catchup
  last analyzed    : 2026-05-19
  catchup_started  : 2022-01
=== pending import (all source dirs) ===
  httpd access: 924  (20220501 .. 20260518)
  cmsauth     : 1330 (20201030 .. 20260518)
```

Each tick processes one backlog month.  Drive ticks faster by
invoking manually:

```bash
# One tick — process oldest backlog month, foreground:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py run

# Time it to spot slow stages:
time sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py run

# Loop until catchup completes (state.mode flips to rebuild then normal):
while :; do
    sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py run || break
    mode=$(mysql -BN -e "SELECT v FROM <hub>_metrics.pipeline_state WHERE k='mode'")
    [ "$mode" = "normal" ] && break
done
```

`run` is the same code path `tick` invokes at `:30`; the orchestrator
is idempotent and resumes from `pipeline_state` on the next call.

### Driving rebuild manually

Once catchup completes, `tick` automatically enters `rebuild` mode and
re-summarizes one month per tick (all six periods).  To drive that
range manually:

```bash
# Catchup wrote period=1 cells only.  Resummarize a range with all
# six periods to fix long-window (0/3/12/13/14) cells:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py rebuild-summaries \
    --since 2022-01 --through 2024-12

# Or narrow to just specific periods (e.g. only the long ones):
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py rebuild-summaries \
    --since 2022-01 --periods 0,3,12,13,14

# Dry-run first:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py rebuild-summaries \
    --since 2022-01 --through 2022-03 --dry-run
```

`rebuild-summaries` does NOT modify `pipeline_state.mode` — so it's
safe to use alongside an in-flight rebuild.  Useful for batched
operator-driven rebuilds when "one month per tick" is too slow.

### Re-entering rebuild after a code or data fix

When a code change (new summary formula, fixed bug) or a data fix
(backfill of a missing column, mass cleanup) affects the inputs that
summarize reads, the existing summary cells are stale.  Use
`rebuild-from` to atomically reset both the cursor and the mode:

```bash
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py rebuild-from 2022-01
```

Equivalent to editing `pipeline_state` by hand to set
`mode='rebuild'` and `rebuild_cursor='2022-01'`, but validated
(YYYY-MM format, refuses future months, warns if before
`catchup_started`) and atomic (one UPDATE).  The next tick sees
mode=rebuild and walks the cursor forward through prev_month with all
6 periods per month.

Typical sequence after a data fix:

```bash
# 1. Verify the fix went in (e.g., backfill-dnload after the dnload bug)
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py status
# 2. Reset the cursor
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py rebuild-from 2022-01
# 3. Let the orchestrator chew through it (one tick per month)
# Or run manually:
while true; do
    mode=$(mysql ... -BN -e "SELECT v FROM pipeline_state WHERE k='mode'")
    [ "$mode" != "rebuild" ] && break
    sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py run
done
```

## My log files aren't where the pipeline looks

The discovery layer scans every place a source log may live, in this
order:

  - `/var/log/httpd/daily/<hub>-access*log*` (current standard)
  - `/var/log/httpd/daily/<YYYY>/<hub>-access*log*` (sysadmin year-subdir)
  - `/var/log/httpd/daily.holding/<hub>-access*log*` (alternate logrotate target)

Same three for cmsauth under `/var/log/hubzero/`.  If a log file lives
anywhere else, the pipeline can't see it — move or symlink it into one
of those.  Duplicates across locations resolve toward higher priority
(daily/ wins over daily.holding/) with a warning.

Year-subdirs and `daily.holding/` are sysadmin organizational
conventions, not pipeline policy — the orchestrator cleans up empty
ones after archiving the last file out.

```bash
# Apache's stock layout (access_log-YYYYMMDD.gz under /var/log/httpd/)
# isn't a recognized source.  Move + rename:
sudo mv /var/log/httpd/access_log-*.gz /var/log/httpd/daily/
cd /var/log/httpd/daily
for f in access_log-*.gz; do
    date=${f#access_log-}; date=${date%.gz}
    sudo mv "$f" "<hubname>-access.log-${date}.gz"
done
```

`hzmetrics.py status` will then see them on the next invocation.

If the underlying logrotate is wrong (not putting files in `daily/`
at all), check `/etc/logrotate.d/httpd` and `/etc/logrotate.d/hubzero`
and compare against `conf/hzmetrics-logrotate-postrotate.sh`.

## A tick says "still running" but nothing's progressing

The lock is a `fcntl.flock` on `/opt/hubzero/metrics/state/hzmetrics.pid` —
the file's contents are the holder's PID (purely diagnostic), but the
lock itself is the kernel-managed flock, not the file's existence.

```bash
# Who holds it?
cat /opt/hubzero/metrics/state/hzmetrics.pid       # the holder's PID (diagnostic only)
ps -p $(cat /opt/hubzero/metrics/state/hzmetrics.pid)   # is it alive?
```

If the process is alive: it's genuinely doing work (large catch-up
import, slow DNS round) — give it time, or follow it with `tail -F`
on the log to see what stage it's in.

If the process is dead: the kernel has already released the flock
(`fcntl` cleans up on process exit), and the next `tick` will acquire
cleanly.  The file may still be on disk if the process was SIGKILL'd
before `release_lock()` ran, but a leftover unlocked file is harmless
— do NOT `rm` a file that a running process might be holding (you'd
unlink the inode out from under their flock and let another process
race in).

If you must force-unstick something a `tick` later: `ps` for the PID,
confirm it's truly gone, then `rm` is safe.

## DNS resolution looks slow

The pipeline uses `aiodns` with default `concurrency=100` against the
system resolver.  Symptoms of DNS trouble in
`/var/log/hubzero/metrics/manage.log`:

```
[resolve-dns] 1234/5000 ... 0.12 IP/sec  ← should be 100+
```

Check:

```bash
# Is the system resolver responding?
time host 1.1.1.1
# Should be < 100ms.

# Is unbound deployed and listening (if hzmetrics.conf points to it)?
sudo systemctl status unbound
ss -tnlp | grep :53
```

If you've deployed local unbound, the `[dns]` block in
`/opt/hubzero/metrics/conf/hzmetrics.conf` should read:

```
nameserver = 127.0.0.1
concurrency = 500
```

Without unbound, leave the defaults — direct-to-system at
concurrency=500 has been benchmarked unfavorably and regressed
against Purdue's resolvers.

### "?" rows are accumulating in web.host / websessions.host

The `?` value is the persisted sentinel for "PTR lookup tried and
returned NXDOMAIN / SERVFAIL / no answer."  It's intentional: the
next `resolve-dns` run filters `WHERE host IS NULL OR host = ''`,
so `?` rows are skipped instead of being re-queried every tick —
which would beat up the resolver on IPs that have no PTR for
infrastructural reasons (CDN backends, cellular NAT pools, hosts
without reverse zones).  This matches the legacy Perl behavior.

If you genuinely want to re-resolve some `?` rows (DNS was
unavailable during the original pass, the resolver is now fixed):

```sql
-- Scope tightly — re-resolving every '?' on a busy hub is
-- expensive and usually re-produces the same '?'.
UPDATE web SET host = NULL
  WHERE host = '?' AND datetime >= '2025-08-01' AND datetime < '2025-08-02';
```

then run `resolve-dns metrics web 2025-08-01`.

## A month's summary numbers look wrong

Each summary cell is `DELETE` + `INSERT` per `(datetime, period,
rowid, colid)`, so re-running summarize is safe and idempotent:

```bash
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py summarize --month 2025-03 --force
```

`--force` bypasses the "already summarized today" guard.  This rewrites
every cell for that month across all six period codes.

If period 14 (all-time) is way off but period 1 (month) looks right,
check that `web.dnload` is populated:

```bash
mysql -e "
  SELECT dnload, COUNT(*) FROM web
  WHERE datetime LIKE '2025-03%' GROUP BY dnload;"
```

If `dnload IS NULL` for any rows, run `backfill-dnload`:

```bash
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py backfill-dnload --start 2025-03
```

Then re-summarize.

### Download cells reading zero across the whole history

Specifically: if every month's "downloaders" and "download-sessions"
cells in `summary_misc_vals` (and downstream) read zero, the
`web.dnload` flag was never populated — neither the legacy importer
nor the pre-1018cc2-shape port set it at insert.  Confirm with:

```sql
SELECT dnload, COUNT(*) FROM web GROUP BY dnload;
```

If you see only `dnload IS NULL` (or NULL plus a small `0` count
from recent imports), this is the long-standing legacy bug.  Fix:

```bash
# 1. Confirm the import code now sets dnload at insert time —
#    look in hzmetrics.py for `INSERT INTO web (..., dnload) VALUES`
#    and `dnload = 1 if _is_download_url(url) else 0`.  If the
#    import path doesn't include those, the importer is the old
#    shape; do not skip this step.
grep -F 'dnload = 1 if _is_download_url' /opt/hubzero/metrics/bin/hzmetrics.py

# 2. Backfill historical rows.  Omit --start to walk every month
#    that has NULL rows; pass it only to scope a partial run.
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py backfill-dnload

# 3. Re-trigger a full rebuild so the download cells get rewritten:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py rebuild-from 2022-01
# then run ticks until mode flips back to normal.
```

`backfill-dnload` sets `dnload = IF(<pattern>, 1, 0)` so every NULL
row in scope ends up as 0 or 1, never left NULL.  The 1's are real
downloads; the 0's are page views.

### Current-month summary cells look truncated mid-month

That's by design.  Normal-mode ticks run `do_analyze(today_str,
sessions=False)` for the current incomplete month: row-level
enrichment (DNS, fill-domain, fill-ipcountry on web/toolstart,
clean-bots on web) runs daily, but `logfix-session` and the
websessions-bound steps are held back until month-close.  This
eliminates the daily-tick session-slicing problem (logfix-session
re-running over fresh imports would chop sessions that genuinely
spanned a tick boundary).

The current month's summary cells get written once, at month-close,
when normal-mode sees `is_month_complete(prev)` flip to True.
`is_month_complete` is the data-driven check that replaced the
calendar-based `days_in > 5` fallback: it returns True when either
the last calendar day's log file is in `imported/`, OR `web` has
at least one row dated in the *next* month (i.e., import time has
demonstrably crossed the boundary).

## A bot is inflating counts

This happens periodically — a crawler that retains the CMS session
cookie is logged per-page-visit instead of once per visitor.
One large hub's October 2024 unique-visitor count went to 1.1M
(typical: ~250k) for this exact reason.

The pipeline doesn't fix the bot problem at runtime.  Mitigation is
via the `exclude_list` table in the metrics DB:

```sql
-- Add an IP/host/useragent/domain entry:
INSERT INTO <hub>_metrics.exclude_list (filter, type, notes)
  VALUES ('128.210.12.34', 'ip', 'Purdue internal scanner');

-- Available types: ip, host, useragent, domain, url
```

`exclude_list` is consulted by:
- `import-apache` (drops rows on insert by ip / useragent / url)
- `clean-bots` (deletes already-enriched rows by domain / host)
- `whoisonline` (skips bot domains in the live map)

Add an entry and the next pipeline run will pick it up.  Already-
imported rows for that filter persist in `web` / `websessions` until
the next `clean-bots` pass.  If the inflation is recent and the
month isn't yet summarized, this catches it before it shows up in
reports.

For broader bot suppression, the `robots.txt` file and the firewall
are the layers above this — out of scope for `hzmetrics.py`.

## Mass cleanup of accumulated crawler spam

When a hub has been running months/years without effective bot
suppression, `web` accumulates millions of crawler hits that don't
look like bots (real browser User-Agent strings, varied IPs).  The
geodynamics hub hit 13.2 M `web` rows by mid-2026 with 8 M of that
being two specific crawl patterns — once cleaned, the table dropped to
4.76 M (64 % reduction) and per-tick wallclocks fell from 18 + min
to ~40 s.

### How to spot it

```sql
-- Top URL prefixes for the worst month — anything that dwarfs the
-- rest is a crawler signature:
SELECT COUNT(*) AS n, SUBSTRING(content, 1, 40) AS prefix
FROM web WHERE datetime >= '<YYYY-MM-01>' AND datetime < '<next-month>'
GROUP BY prefix ORDER BY n DESC LIMIT 25;

-- Then for each candidate pattern, check Referer presence — bots
-- typically don't send a Referer header, real users have it set
-- ~50 % of the time:
SELECT
  CASE WHEN referrer IS NULL OR referrer IN ('','-') THEN 'empty' ELSE 'has ref' END AS r,
  COUNT(*) AS n
FROM web WHERE datetime >= '<...>' AND datetime < '<...>'
  AND content LIKE '<pattern>%'
GROUP BY r;
```

A pattern with >95 % empty Referer + browser UAs is almost certainly
a distributed crawl.  On geodynamics the smoking guns were:

- `/login?return=<base64>` — 99.9 % empty Referer.  Auth redirect
  targets, hit by crawlers that follow public links.  Regex covers
  the trailing-slash variant `/login/?return=…` too — the same
  crawler ran both shapes in the wild.
- `/resources/browse?<query>` — 96-97 % empty Referer.  Catalog
  pagination spam, every tag combination visited.  Also covers
  `/resources/browse/?…`.

Together: 93 % of one month's rows.

Two additional categories also land in `_is_excluded_url` and
`_is_referer_spam` and are filtered at import time without operator
action:

- **Self-identifying crawler User-Agents** — `Scrapy`, `PRTG`,
  `PycURL`, `Yeti` and similar fixed strings, kept in the
  `bot_useragents` table and matched exact-case to skip the slower
  substring scan over the full UA filter list.
- **`/pipermail/`** (mailman archive web view) — never a metrics
  signal; consumes thousands of rows per crawl.
- **`/cron/tick/...`** — the hub's own scheduled-task self-hits.
  Looked like real traffic in the legacy code; filtered out so they
  don't inflate hit counts.

### Filter at import time

Add a URL+Referer pattern to the import filter in
`hzmetrics.py:_is_referer_spam`.  Stay narrow — match only the
specific URL patterns you measured, not every empty-Referer hit.
Real users have empty Referer plenty of the time (bookmark, direct
nav, HTTPS→HTTP, privacy browsers).

This is a deliberate A/B divergence from the legacy
`xlogimport_apache.php`, which doesn't see the Referer column at
all.  Update `tests/ab/port_import_apache/run.sh` `filter_keepers`
to drop the new patterns from both sides of the diff.

### Backfill clean: the chunked-DELETE trap

The naive approach (chunked DELETE with `LIMIT 50000`) is **much**
slower than it looks.  Each chunk re-walks the same secondary index
from the start to find 50k matching rows, and each candidate row also
has to fetch the heap to check the un-indexed `referrer` column.  At
~150 s per 50 k chunk we measured ~3 hours per million rows.

Use a temp PK table instead — one scan, then delete by PK range:

```sql
SET SESSION wait_timeout=86400;
DROP TABLE IF EXISTS _spam_pks;
CREATE TABLE _spam_pks (id BIGINT PRIMARY KEY) ENGINE=InnoDB;
INSERT INTO _spam_pks (id)
SELECT id FROM web
WHERE (referrer IS NULL OR referrer IN ('','-'))
  AND (content LIKE '/login?return=%' OR content LIKE '/resources/browse?%');
```

Then loop PK windows and delete by JOIN — this hits the PK directly,
so each chunk is a constant ~25 s for 100 k rows regardless of how
many have already been deleted:

```bash
range_size=100000
range_start=$(mysql ... -BN -e "SELECT MIN(id) FROM _spam_pks")
range_top=$(mysql   ... -BN -e "SELECT MAX(id) FROM _spam_pks")
while [ "$range_start" -le "$range_top" ]; do
    range_end=$((range_start + range_size))
    mysql ... -e "DELETE w FROM web w
        JOIN _spam_pks s ON s.id = w.id
        WHERE s.id >= $range_start AND s.id < $range_end;"
    range_start=$range_end
done
```

6.24 M rows in 67 min vs an estimated 3 + hours for the naive
approach.

### The sessionid landmine

`logfix-session` builds `websessions` from `web` rows where
`sessionid IS NULL OR sessionid = '0'`.  After it runs, every event
that landed in a session has `web.sessionid` set to point at the new
session row.  This means:

**Wiping `websessions` without resetting `web.sessionid` creates ghost
stamps.**  The next `logfix-session` skips those rows (they look
already-stamped), so the months end up with vastly fewer sessions
than the data warrants.  On geodynamics this surfaced as 2025-07
emitting "0 session(s), stamped 0 web event(s)" for the first two
weeks of the month even though `web` had ~200 k rows there.

The orchestrator catches this two ways:

1. **`mark-dirty` after surgery** — operator-driven.  Tell the state
   machine which months need rework, then let it handle the rest:

   ```bash
   sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py mark-dirty \
       2024-09 2024-10 2024-11 2024-12 2025-01 2025-02 2025-03 \
       2025-04 2025-05 2025-06 2025-07
   ```

   The next catchup tick (auto-triggered if normal mode sees these in
   the backlog) processes each one through `_reset_month_for_resummarize`
   — chunked `UPDATE web SET sessionid = NULL`, `DELETE FROM
   websessions`, `DELETE FROM summary_*_vals` — then re-runs
   logfix-session + summarize.  The dirty marker auto-clears on
   success.

2. **`month_has_orphaned_stamps()` consistency check** — runs in
   `_backlog_months` on every candidate month.  Detects any `web.sessionid`
   pointing at a non-existent `websessions.id` and forces the same reset
   path regardless of the dirty marker.  This is the safety net for
   ad-hoc DELETEs that bypassed `mark-dirty`.

So the cleanup sequence after deleting spam web rows is just:

```bash
# 1. Tell the orchestrator which months need rework:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py mark-dirty \
    2024-09 2024-10 ... 2025-07

# 2. Let it run.  The next tick (cron or manual) detects the dirty
#    set, switches to catchup mode if needed, and processes one
#    month per tick:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py run
```

If you do need to do it by hand (e.g. operating on a hub that hasn't
been upgraded yet), the SQL equivalent of what the state machine
does is:

```sql
-- 1. Reset sessionid for the affected date ranges, in chunks
--    (1.4 M UPDATEs took 176 s on the test hub):
UPDATE web SET sessionid = NULL
  WHERE datetime >= '<start>' AND datetime < '<end>'
    AND sessionid IS NOT NULL AND sessionid <> '0'
  LIMIT 100000;
-- repeat until ROW_COUNT() = 0

-- 2. Wipe websessions for those months.
DELETE FROM websessions
  WHERE DATE_FORMAT(datetime,'%Y-%m-01') IN ('2024-09-01', ...);

-- 3. Wipe summary cells for those months.  NOTE the date format:
--    summary_*_vals.datetime uses YYYY-MM-00 (legacy convention),
--    while websessions.datetime uses real datetime values.  Mixing
--    the two formats silently wipes 0 rows.
DELETE FROM summary_user_vals     WHERE datetime IN ('2024-09-00', ...);
DELETE FROM summary_misc_vals     WHERE datetime IN ('2024-09-00', ...);
DELETE FROM summary_simusage_vals WHERE datetime IN ('2024-09-00', ...);
DELETE FROM summary_andmore_vals  WHERE datetime IN ('2024-09-00', ...);

-- 4. Flip pipeline_state back to catchup so the orchestrator
--    redrives the affected months on the next tick.
UPDATE pipeline_state SET v='catchup' WHERE k='mode';
DELETE FROM pipeline_state WHERE k='analyzed';
```

`catchup` re-imports nothing (the rows are already in `web` cleanly)
— it just walks forward through every backlog month running
`logfix-session` + `summarize-month` with `periods=(1,)`.  When the
backlog drains, the state machine flips itself to `rebuild`, which
walks forward again refreshing every month's long-window summary
cells (periods 0/3/12/13/14).  No manual intervention required.

### max_allowed_packet on bulk INSERT

`_ipgeo_lookup_batch` writes the ipinfo HTTPS results back into the
hub-DB cache via a single `INSERT … VALUES (…), (…), …` with one
tuple per resolved IP.  On a busy month (212 k new IPs on a single
weekly websessions chunk) the SQL text alone exceeds 16 MB and
MariaDB rejects the packet:

```
pymysql.err.OperationalError: (1153, "Got a packet bigger than 'max_allowed_packet' bytes")
```

The pipeline now chunks that INSERT at 1000 rows per statement
(`hzmetrics.py:4685`).  If you see this error on another hub it's the
same issue — confirm by checking the server's `max_allowed_packet`
and the number of new IPs in the failing chunk.

### Make resolve-dns cheap once the table is small

After mass cleanup the `web` table is much smaller but the resolve-
dns scan still does the heavy lifting (`INSERT INTO _dns_tmp …
SELECT DISTINCT ip FROM web WHERE datetime range …`).  A covering
index helps:

```sql
CREATE INDEX web_dt_ip ON web (datetime, ip);
```

Lets InnoDB satisfy the scan from index pages without touching the
8 GB+ heap.  ~700 MB index for a 12 M-row table; built in ~2.5 min on
the geodynamics hub.

After heavy churn the planner's row-count statistics may still pick
a worse index — run `ANALYZE TABLE web` to refresh stats.  If the
planner stubbornly picks the wrong index, a `FORCE INDEX (web_dt_ip)`
hint in the resolve-dns query is the fallback.

### Verification queries

After the cleanup ticks finish, sanity-check the result:

```sql
-- Should be 0:
SELECT COUNT(*) FROM web
  WHERE (referrer IS NULL OR referrer IN ('','-'))
    AND (content LIKE '/login?return=%' OR content LIKE '/resources/browse?%');

-- Should be 0 for the affected month range:
SELECT COUNT(*) FROM web
  WHERE datetime >= '<affected-start>' AND datetime < '<affected-end>'
    AND sessionid IS NOT NULL AND sessionid <> '0'
    AND sessionid NOT IN (SELECT id FROM websessions);

-- Should show all 6 periods (1, 0, 3, 12, 13, 14) per month after
-- the rebuild phase finishes:
SELECT DATE_FORMAT(datetime,'%Y-%m') AS m,
       GROUP_CONCAT(DISTINCT period ORDER BY period) AS periods
FROM summary_user_vals
WHERE datetime >= '<start>-00'
GROUP BY m ORDER BY m;
```

## Whoisonline map is stale

```bash
# Force a refresh:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py whoisonline

# Where does the XML live?
ls -la /var/www/<hub>/app/site/stats/maps/whoisonline.xml
```

If the XML is hours old, the cron entry isn't running.  Check:

```bash
sudo crontab -u apache -l
sudo journalctl -u crond | grep hzmetrics | tail -20
```

The cron entry should fire every 5 minutes and update `jos_session_geo`
+ the XML file every time.  If `tick` is crashing on the whoisonline
path, the metrics-at-`:30` step won't run either, so this is worth
checking first.

## Re-running everything from scratch (test environments only)

```bash
# init covers setup-db + migrate in one shot:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py init

# Re-process the entire pending log set:
while sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py status \
      | grep -q "pending"; do
    sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py process --next
done
```

Never do this on a production hub without backing up `<hub>_metrics`
first.

## Crash recovery: forget-import

Each per-file import is wrapped in a single transaction guarded by an
`INSERT IGNORE` into `metrics.imported_sources` (`filename`,
`target_table`, `pk_start`, `pk_end`, `row_count`, `imported_at`).
The flow is:

  1. `BEGIN`
  2. `INSERT IGNORE INTO imported_sources …` — `rowcount=0` means the
     file already imported; skip data INSERT and just retry the move.
  3. Stream data into `web` / `userlogin` / `webhits`, tracking
     `pk_start` / `pk_end` (single-writer lock guarantees the range
     is contiguous to this file).
  4. `UPDATE imported_sources SET pk_start, pk_end, row_count`.
  5. `COMMIT`.
  6. Move source file to `imported/`.

Crash at any step before COMMIT → InnoDB rolls back the data INSERT
*and* the `imported_sources` row; retry sees no marker and imports
cleanly.  Crash between COMMIT and the move → marker exists, retry
skips the data INSERT and just retries the move.

To deliberately re-import a file (e.g. you discovered the parser
mis-handled a corner case and need to reprocess the day):

```bash
# Reverse the imported_sources marker AND delete the rows it tracks:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py \
    forget-import access.log-20260315.gz web

# Move the file back from imported/ to daily/ (or pull a fresh copy):
sudo -u apache mv /var/log/httpd/imported/access.log-20260315.gz \
                   /var/log/httpd/daily/
```

The next import tick will pick it up.  PK-range DELETE is bounded by
file size, not table size — works at nanoHUB scale (800 M+ rows).

## Logs and observability

Three destinations, all populated simultaneously with ISO 8601
timestamps to millisecond precision (`2026-05-21T14:05:49.123-04:00`):

```
/var/log/hubzero/metrics/manage.log     # DEBUG+ — authoritative file (HZMETRICS_LOG override)
stderr                                  # INFO+  — picked up by cron / systemd
syslog LOG_LOCAL0 facility              # INFO+  — routes wherever rsyslog/journald sends it
```

Quick views:

```bash
# File (tail-and-grep friendly, full DEBUG):
sudo tail -f /var/log/hubzero/metrics/manage.log
sudo grep -E '(FAIL|ERROR)' /var/log/hubzero/metrics/manage.log

# systemd journal (tagged hzmetrics):
sudo journalctl -t hzmetrics --since '1 hour ago'
sudo journalctl -t hzmetrics -f       # live tail

# /var/log/messages or hub-specific file (depends on rsyslog rules):
sudo grep hzmetrics /var/log/messages
```

To route the syslog facility to a dedicated file, add to your
rsyslog config (e.g. `/etc/rsyslog.d/30-hzmetrics.conf`):

```
local0.*    /var/log/hubzero/metrics/hzmetrics-syslog.log
& stop
```

Then `systemctl restart rsyslog`.  This is optional — the local
manage.log already captures everything; the syslog handler is the
hook for centralized logging or cross-host log aggregation.

Each pipeline stage prints `[<stage>] start` and `[<stage>] done`
markers.  Searching for `FAIL`, `ERROR`, or `unrecognized` surfaces
recoverable problems (e.g., a malformed log line that didn't match
either apache regex).

`hzmetrics.py status` is the structured view of the same data.

## Backup and restore

The metrics DB is downstream of the logs — anything in the summary
tables can in principle be rebuilt by re-running the pipeline.  But
that takes hours on mature hubs, and historical Apache logs may not
be available far back, so practical backups are still recommended
before any destructive operation.

```bash
# Cheap pre-flight dump before a risky operation (re-summarize a
# month, schema migrate, etc.):
DATE=$(date +%Y%m%d-%H%M)
mysqldump --no-tablespaces <hub>_metrics \
    summary_user_vals summary_simusage_vals summary_misc_vals \
    summary_andmore_vals migrations \
    | gzip > /var/backups/hzmetrics-summary-${DATE}.sql.gz
```

The big tables (`web` historically can be 30M–500M rows;
`websessions` 190M+; `userlogin` likewise) dump slowly and rarely
need point-in-time recovery — they're rebuilt from logs anyway.
The summary tables, by contrast, are small (thousands of rows
total), encode all the human-readable output, and are what reports
have been built against — these are the high-value backup targets.

Restore from a summary dump is just `gunzip -c <file>.sql.gz | mysql
<hub>_metrics`.  The `DELETE + INSERT` per-cell pattern in
`summarize-month` means a re-run will rewrite cells without needing
to clear them first.

If the metrics DB has been corrupted at a deeper level, recovery
order:

1. `mysqldump` whatever is salvageable.
2. `hzmetrics.py setup-db` (idempotent — `CREATE TABLE IF NOT EXISTS`).
3. `hzmetrics.py migrate --apply` to bring schema current.
4. Re-import any preserved historical data via `mysql … < dump.sql`.
5. Re-run `summarize-month` for each month you need restored.
6. Re-run `analyze --month` if the underlying enriched data was lost
   (will require the original Apache logs from `/var/log/httpd/imported/`).

## Periodic maintenance: ANALYZE TABLE

InnoDB has `innodb_stats_auto_recalc=ON` by default — index statistics
get refreshed when ~10% of a table's rows change.  That's fine in
steady state, but after a big one-shot change the trigger threshold
may not fire and stale stats can steer the query planner onto bad
index paths.

Trigger an explicit refresh after any of:

- Bulk import (catchup processing several backfilled months at once).
- A migration that DELETEs or rewrites a large fraction of a table
  (e.g. migration #4's userlogin purge: 93 M → 4 K rows).
- An engine conversion (`ALTER … ENGINE=InnoDB` on a large MyISAM
  table — the conversion rebuilds but doesn't re-sample stats).

```bash
mysql -u <user> -p<pass> <hub>_metrics <<'SQL'
ANALYZE TABLE web;
ANALYZE TABLE websessions;
ANALYZE TABLE userlogin;
ANALYZE TABLE summary_user_vals;
ANALYZE TABLE summary_misc_vals;
ANALYZE TABLE summary_simusage_vals;
ANALYZE TABLE summary_andmore_vals;
ANALYZE TABLE webhits;
SQL
```

ANALYZE TABLE on InnoDB samples 20 pages by default and runs in
sub-second per table — safe to run while the pipeline is active.

Don't run `OPTIMIZE TABLE` on these — for InnoDB it rebuilds the
entire .ibd file (same cost as `ALTER … ENGINE=InnoDB`) and only
helps if the table has accumulated significant deleted-but-not-
reclaimed space.  After a recent ENGINE conversion the .ibd is
already fresh, so OPTIMIZE is a no-op cost.  ANALYZE is the
lightweight alternative that fixes the stats-staleness symptom
without rewriting data.

## Monitoring and alerting

The pipeline doesn't ship its own monitoring stack — observability
is via log file, status command, and what you wire up around it.
Useful signals to put behind alerts:

| Signal | How to check | When to alert |
|---|---|---|
| Pipeline is behind | `hzmetrics.py status` shows pending days | >3 pending days (autonomous catch-up should resolve within hours; sustained backlog means something is wedged) |
| Daily run is failing | grep `ERROR` / `FAIL` in `/var/log/hubzero/metrics/manage.log` since midnight | any new occurrence |
| Cron `tick` is failing | non-zero exit status from the cron entry (`main()` propagates the handler's return code) | any new occurrence; cron emails the captured stderr to `MAILTO=` |
| Lock held abnormally long | `cat /opt/hubzero/metrics/state/hzmetrics.pid` then check the PID's elapsed time with `ps -o etime= -p <pid>` | lock held >2 hours by a live PID (catch-up usually completes within minutes; multi-hour holds suggest a wedge) |
| Logrotate failing | New file in `/var/log/httpd/daily/` is not appearing | no new `<hub>-access.log-YYYYMMDD` after midnight |
| `web` row count anomaly | Compare today's `web` row count to a 7-day rolling average | row count is >3× the average (bot inflation event) |
| `webhits / web` ratio anomaly | `SELECT SUM(hits) FROM webhits` ÷ `COUNT(*) FROM web` for the same window | ratio drops below ~0.1 or above ~10 (suggests one of the two imports has stopped) |
| Summary table missing recent month | `SELECT MAX(datetime) FROM summary_user_vals` | older than 5 days into the new month |
| Whoisonline map stale | `stat /var/www/<hub>/app/site/stats/maps/whoisonline.xml` | >15 minutes old (cron runs every 5) |
| DNS service down | `aiodns` errors in the log | >10% of resolutions failing |
| IP-country service down | `fill-ipcountry` log errors | repeated HTTP errors against `help.hubzero.org/ipinfo/v1` |

Cheap implementation: a daily cron entry that runs `hzmetrics.py
status`, greps the log for `ERROR`/`FAIL` since midnight, and mails
out a summary.  The 2015-era `MAILTO=` directive in the original
crontab handled exactly this — alerts on cron-output capture.

For richer monitoring, a Grafana dashboard reading
`summary_*_vals` directly works well — both for the live usage
view and for "are the numbers in the expected range?" checks.  (The
2023 Sperhac conference talk includes screenshots
of one such Grafana setup against a hub's metrics database.)

## When to escalate

- Pipeline runs but produces 0 rows in `web` for a day → check that
  Apache is actually writing access logs and that logrotate is moving
  them to `daily/`.
- Pipeline crashes mid-summarize → look for OOM (`dmesg | tail`).
  The all-time period on a multi-million-row `web` table is the
  hot spot; check `dnload` column population.
- Numbers are dramatically different from the previous month → check
  for a bot inflation event (compare `webhits` and `web` row counts;
  if `webhits` ≈ previous month but `web` is 10× higher, you're being
  scraped).
- DNS resolves but `fill-ipcountry` errors out → the
  `help.hubzero.org/ipinfo/v1` service may be unreachable.  Check
  `/opt/hubzero/metrics/conf/hzmetrics.conf` for the IP-country service
  URL.
