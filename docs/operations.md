# Operations runbook

Common ops tasks for a deployed `hzmetrics.py` pipeline.  This is the
practical follow-on to [deployment.md](deployment.md) — once it's
running, this is what to do when something looks off.

The conventional sanity-check pattern goes back to the original
HUBzero pipeline circa 2014–2016 (Sperhac's "Basic new-month checks
for Usage Metrics Processing" runbook from the / era).
The mechanics are different now but the questions to ask are the
same.

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

## The PID lock is stuck

If `hzmetrics.py status` says a pipeline is running but nothing seems
to be progressing:

```bash
# Is the process actually alive?
cat /var/run/hzmetrics/hzmetrics.pid
ps -p $(cat /var/run/hzmetrics/hzmetrics.pid)

# If the PID is dead but the file is still there:
sudo rm /var/run/hzmetrics/hzmetrics.pid

# Next tick will pick up where it left off.
```

The pipeline writes the PID file on entry and removes it on exit; an
abrupt kill (SIGKILL, OOM, host reboot) leaves a stale file.  Safe to
remove if the PID is gone.

## DNS resolution looks slow

The pipeline uses `aiodns` with default `concurrency=100` against the
system resolver.  Symptoms of DNS trouble in
`/var/log/hubzero/metrics/hzmetrics.log`:

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

This happens periodically — a crawler that retains the Joomla session
cookie is logged per-page-visit instead of once per visitor.
the largest hub's October 2024 unique-visitor count went to 1.1M (typical:
~250k) for this exact reason.

The pipeline doesn't fix the bot problem at runtime.  Mitigation is
via the `exclude_list` table in the metrics DB:

```sql
-- Add an IP/host/useragent/domain entry:
INSERT INTO <hub>_metrics.exclude_list (filter, type, notes)
  VALUES ('128.210.12.34', 'ip', 'Purdue internal scanner');

-- Available types: ip, host, useragent, domain, url
-- See PR  for the schema.
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
/var/log/hubzero/metrics/hzmetrics.log
```

Each pipeline stage prints `[<stage>] start` and `[<stage>] done`
markers.  Searching for `FAIL`, `ERROR`, or `unrecognized` surfaces
recoverable problems (e.g., a malformed log line that didn't match
either apache regex).

`hzmetrics.py status` is the structured view of the same data.

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
