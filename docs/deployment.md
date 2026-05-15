# Deployment

How to install `hzmetrics.py` on a new HUBzero hub.

## Prerequisites

- A working HUBzero hub with two MariaDB schemas: `<hub>` (live CMS)
  and `<hub>_metrics` (analytics).  If the metrics DB doesn't exist
  yet, see "First-time install" below.
- Python 3.7+ on the host.  3.11 strongly preferred — earlier versions
  ship without modern `asyncio` and on Rocky 8 the system `python3` is
  3.6 which lacks `asyncio.run()`.
- A user with read access to the hub DB and full ownership of the
  metrics DB.  On a stock HUBzero hub this is the `apache` user; the
  cron entry is owned by `apache`.

System packages (Rocky 8 names — adapt for other distros):

```
dnf install python3.11 python3.11-PyMySQL unbound  # unbound optional
pip3.11 install --user aiodns                       # not packaged on Rocky 8
```

Versions required:
- `aiodns` >= 3.x (the c-ares-based async resolver used by
  `resolve-dns`)
- `pymysql` (any current 1.x)
- Python >= 3.7 for `resolve-dns` itself (async syntax), but the
  pipeline's `tick` self-relaunches into Python >= 3.10 when invoked
  under an older interpreter — Rocky 8's system `/usr/bin/python3` is
  3.6 which lacks `asyncio.run()`, so a separate `python3.11` install
  alongside is the supported configuration.

Test the Python version is discoverable: `hzmetrics.py` self-relaunches
into the highest-numbered `python3.N` (≥ 3.10) it can find on `PATH`,
so just having `python3.11` installed alongside the system `python3`
is enough.

## File layout (post-install)

```
/opt/hubzero/bin/hzmetrics.py                  the pipeline
/opt/hubzero/bin/hzmetrics-postrotate.sh       logrotate hook
/etc/hubzero-metrics/access.cfg                DB credentials (640, root:apache)
/etc/hubzero-metrics/hzmetrics.conf            optional runtime overrides
/etc/tmpfiles.d/hzmetrics.conf                 systemd-tmpfiles, creates /var/run/hzmetrics/
/var/spool/cron/apache                         the cron entry (single line)
/var/run/hzmetrics/hzmetrics.pid               PID lock (created at runtime)
/var/run/hzmetrics/hzmetrics.state             daily state (created at runtime)
/var/log/hubzero/metrics/hzmetrics.log         pipeline log
```

The legacy reference scripts under `tests/legacy/` are **not**
installed on a production host.  They live in this repo only as the
A/B-test parity reference.

## Install via Makefile

From a checkout of this repo:

```
sudo make -C source install
```

The Makefile copies:

- `hzmetrics.py` → `/opt/hubzero/bin/hzmetrics.py` (mode 755, owner apache)
- `conf/hzmetrics-logrotate-postrotate.sh` → `/opt/hubzero/bin/hzmetrics-postrotate.sh`
- `conf/hzmetrics.tmpfiles.conf` → `/etc/tmpfiles.d/hzmetrics.conf`
- `conf/hubzero-metrics.cron.apache` → `/var/spool/cron/apache`
- `conf/hubzero-metrics.cron.d` → `/etc/cron.d/hubzero-metrics`  *(use one or the other)*

(`source/Makefile` lives in this repo; it's the only thing left in
`source/` from the original packaging layout.)

The cron entry is a single line, every five minutes:

```
*/5 * * * * apache  python3 /opt/hubzero/bin/hzmetrics.py tick
```

Pick the format that matches your host's cron conventions:

- `/var/spool/cron/apache` — apache user crontab (no user column).
- `/etc/cron.d/hubzero-metrics` — drop-in (`user` column is the
  3rd-to-last field).

After install, kick `systemd-tmpfiles` so `/var/run/hzmetrics/`
exists right now (it'll be recreated automatically on every reboot):

```
sudo systemd-tmpfiles --create /etc/tmpfiles.d/hzmetrics.conf
```

## access.cfg

The mandatory config file.  Bare `$var = 'value';` syntax (no
`<?php`).  Read by both `hzmetrics.py` and the legacy Perl scripts:

```
$hub_dir    = '/var/www/<hub>';
$hub_db     = '<hub>';
$metrics_db = '<hub>_metrics';
$db_host    = 'localhost';
$db_user    = '<hub>';
$db_pass    = '<secret>';
$db_prefix  = 'jos_';
```

```
sudo install -d -o root -g apache -m 750 /etc/hubzero-metrics
sudo install -o root -g apache -m 640 <your-cfg> /etc/hubzero-metrics/access.cfg
```

The harness uses `HZMETRICS_ACCESS_CFG=<path>` to point at a test
config — useful for one-off catch-up against a copy of production.

## hzmetrics.conf (optional)

Runtime tuning.  See [`conf/hzmetrics.conf.sample`](../conf/hzmetrics.conf.sample)
for the documented form.  The two settings that matter:

```
[dns]
nameserver = system        # or 127.0.0.1 with local unbound
concurrency = 100          # raise to 500 with unbound in front
timeout = 2.0
```

Precedence (lowest to highest): built-in defaults → this file →
`HZMETRICS_DNS_*` env vars → CLI flags.

If you don't deploy this file, the built-in defaults are fine —
`concurrency=100` against the system resolver is benchmarked clean
against Purdue's resolvers and produces ~4 ms/IP cold.

## First-time install

If `<hub>_metrics` doesn't exist yet:

```
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py setup-db
```

Creates the metrics database, every table, and seeds the static
reference tables (`continents`, `countries`, `domainclass`,
`classes`, etc.).  Run with `--dry-run` first to see what statements
will execute.

The CMS-side tables created by metrics
(`jos_resource_stats_tools_topvals`, `jos_session_geo`, etc.) are
created by the hub's own CMS migrations and shouldn't need
anything from `hzmetrics.py`.  If they're missing, see the
`exclude_list` schema work from PR  and check the hub's
migration state.

Apply any pending schema migrations:

```
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py migrate --apply
```

`migrate` without `--apply` shows what would change.

## Verifying the install

```
# What's the pipeline see?
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py status

# Does DNS work?
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py resolve-dns \
    metrics web --dry-run 2025-07

# Does the daily run actually do anything?
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py run --force
```

Watch `/var/log/hubzero/metrics/hzmetrics.log` while the run is in
progress.  Each pipeline phase prints `[<phase>] …` start and end
markers.

## Logrotate

The pipeline writes to a single log file in
`/var/log/hubzero/metrics/`.  Add a logrotate stanza that invokes the
postrotate hook so the pipeline picks up the new file without
restarting (it reopens on the next `tick`):

```
/var/log/hubzero/metrics/hzmetrics.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    create 640 apache apache
    postrotate
        /opt/hubzero/bin/hzmetrics-postrotate.sh
    endscript
}
```

## Catch-up after a stalled host

The pipeline is built to drain log backlogs on its own.  Once the
cron entry is running:

```
# What's pending?
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py status

# Either wait — `tick` will process one month per hourly :30 tick.
# 12 months of backlog → about 6 hours of unattended catch-up.

# Or do it explicitly, one month at a time, in the foreground:
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py process --next
```

See [operations.md](operations.md) for the runbook on common
catch-up scenarios.

## Coexistence with the legacy pipeline

If you're migrating a hub that's still running the legacy
`hubzero-metrics` package, disable its cron entries before enabling
the new one — they write to the same tables and concurrent runs
will deadlock on the summary tables.  The legacy crontab looks like:

```
*/15 * * * * /opt/hubzero/bin/metrics/xlogfix_whoisonline.php
10   0 * * * /opt/hubzero/bin/metrics/import/__fetch_apache_and_auth_log.sh
15   0 * * * /opt/hubzero/bin/metrics/import/__import_apache_and_auth_log.sh
30   0 * * * /opt/hubzero/bin/metrics/import/__archive_apache_and_auth_log.sh
40   0 * * * /opt/hubzero/bin/metrics/__process_tool_metrics.sh
50   0 * * * /opt/hubzero/bin/metrics/__process_usage_metrics.sh
50   1 1 * * /opt/hubzero/bin/metrics/__process_usage_metrics_summary.sh
```

Comment all seven out before deploying the `hzmetrics.py tick` entry.

The data shape is unchanged, so there's no migration step beyond
running `migrate --apply` to pick up the indexed `dnload` column.
The first `summarize-month` after deployment will rewrite all six
period cells for the target month — no in-place data conversion.
