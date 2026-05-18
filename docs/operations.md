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
`/etc/hubzero-metrics/`:

| Env var | What it overrides | Default |
|---|---|---|
| `HZMETRICS_LOG` | Pipeline log file path | `/var/log/hubzero/metrics/manage.log` |
| `HZMETRICS_ACCESS_CFG` | DB credentials cfg path | `/etc/hubzero-metrics/access.cfg` |
| `HZMETRICS_DNS_NAMESERVER` | resolve-dns nameserver | from `[dns]` section of hzmetrics.conf, then `system` |
| `HZMETRICS_DNS_CONCURRENCY` | aiodns concurrency | 100 |
| `HZMETRICS_DNS_TIMEOUT` | aiodns per-IP timeout (seconds) | 2.0 |

Useful for one-off runs that shouldn't touch the production log file
(e.g. a catch-up against a snapshot DB), or for the A/B harness.

Exit codes: `main()` propagates the handler's return code.  A daily
`cron` entry that's checking for success can rely on exit status; a
non-zero exit indicates an operational error (missing cfg, missing
hub_dir, mysql_exec failure, etc.).

## Pipeline is running — am I getting fresh data?

```bash
# What does the pipeline think the world looks like?
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py status
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

The pipeline is built to drain backlogs autonomously — once `tick`
is running it processes one month per `:30` invocation.  A 12-month
backlog drains in ~6 hours of unattended catch-up.

But you can drive it manually if you want it to go faster:

```bash
# Process the oldest pending month, foreground:
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py process --next

# Repeat until status shows nothing pending.
```

`process --next` is a one-shot equivalent of one `tick` run — import,
analyze, summarize one month.  Safe to interrupt; resumes from where
it stopped on the next invocation.

## My log files aren't where the pipeline looks

Apache's own logrotate, if it's set up differently, may put logs
straight in `/var/log/httpd/` as `access_log-YYYYMMDD.gz` instead of
in `/var/log/httpd/daily/<hub>-access.log-YYYYMMDD`.  The pipeline
only reads from `daily/`.

```bash
# Move them where the pipeline expects:
sudo mv /var/log/httpd/access_log-*.gz /var/log/httpd/daily/
# Rename to the expected pattern:
cd /var/log/httpd/daily
for f in access_log-*.gz; do
    date=${f#access_log-}; date=${date%.gz}
    sudo mv "$f" "<hubname>-access.log-${date}.gz"
done
```

`hzmetrics.py status` will then see them on the next invocation.

CMS auth logs follow the same model — they should be in
`/var/log/hubzero/daily/cmsauth.log-YYYYMMDD`.

If the underlying logrotate is wrong (not putting files in `daily/`
at all), check `/etc/logrotate.d/httpd` and `/etc/logrotate.d/hubzero`
and compare against `conf/hzmetrics-logrotate-postrotate.sh`.

## A tick says "still running" but nothing's progressing

The lock is a `fcntl.flock` on `/var/run/hzmetrics/hzmetrics.pid` —
the file's contents are the holder's PID (purely diagnostic), but the
lock itself is the kernel-managed flock, not the file's existence.

```bash
# Who holds it?
cat /var/run/hzmetrics/hzmetrics.pid       # the holder's PID (diagnostic only)
ps -p $(cat /var/run/hzmetrics/hzmetrics.pid)   # is it alive?
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
`/etc/hubzero-metrics/hzmetrics.conf` should read:

```
nameserver = 127.0.0.1
concurrency = 500
```

Without unbound, leave the defaults — direct-to-system at
concurrency=500 has been benchmarked unfavorably and regressed
against Purdue's resolvers.

## A month's summary numbers look wrong

Each summary cell is `DELETE` + `INSERT` per `(datetime, period,
rowid, colid)`, so re-running summarize is safe and idempotent:

```bash
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py summarize --month 2025-03 --force
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
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py backfill-dnload --start 2025-03
```

Then re-summarize.

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

## Whoisonline map is stale

```bash
# Force a refresh:
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py whoisonline

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
# Drop and recreate the test DB schema:
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py setup-db

# Apply all migrations:
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py migrate --apply

# Re-process the entire pending log set:
while sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py status \
      | grep -q "pending"; do
    sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py process --next
done
```

Never do this on a production hub without backing up `<hub>_metrics`
first.

## Logs and observability

Single log file:

```
/var/log/hubzero/metrics/manage.log
```

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

## Monitoring and alerting

The pipeline doesn't ship its own monitoring stack — observability
is via log file, status command, and what you wire up around it.
Useful signals to put behind alerts:

| Signal | How to check | When to alert |
|---|---|---|
| Pipeline is behind | `hzmetrics.py status` shows pending days | >3 pending days (autonomous catch-up should resolve within hours; sustained backlog means something is wedged) |
| Daily run is failing | grep `ERROR` / `FAIL` in `/var/log/hubzero/metrics/manage.log` since midnight | any new occurrence |
| Cron `tick` is failing | non-zero exit status from the cron entry (`main()` propagates the handler's return code) | any new occurrence; cron emails the captured stderr to `MAILTO=` |
| Lock held abnormally long | `cat /var/run/hzmetrics/hzmetrics.pid` then check the PID's elapsed time with `ps -o etime= -p <pid>` | lock held >2 hours by a live PID (catch-up usually completes within minutes; multi-hour holds suggest a wedge) |
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
  `/etc/hubzero-metrics/hzmetrics.conf` for the IP-country service
  URL.
