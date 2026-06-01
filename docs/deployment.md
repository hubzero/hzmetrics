# Deployment

How to install `hzmetrics.py` on a new HUBzero hub.

## Prerequisites

- A working HUBzero hub with two MariaDB schemas: `<hub>` (live CMS)
  and `<hub>_metrics` (analytics).  If the metrics DB doesn't exist
  yet, see "First-time install" below.
- Python 3.10+ on the host.  3.11 strongly preferred.  On Rocky 8 the
  system `python3` is 3.6, which fails `hzmetrics.py`'s minimum check;
  install `python3.11` alongside (the pipeline self-relaunches into
  the highest `python3.N` >= 3.10 it finds on `PATH`).
- A user with read access to the hub DB and full ownership of the
  metrics DB.  On a stock HUBzero hub this is the `apache` user; the
  cron entry is owned by `apache`.

Production hosts get the deps as system packages (Rocky 8 names —
adapt for other distros).  `sudo make install` runs both of the
commands below as its first step; do them by hand if you'd rather
see each step:

```
sudo dnf install python3.11 python3.11-PyMySQL unbound  # unbound optional
sudo bash -c 'umask 022 && python3.11 -m pip install aiodns'
```

Notes:
- `aiodns` has no `python3.11-*` RPM in any reachable repo, so pip is
  unavoidable for that one dep.  `umask 022` keeps the installed
  files world-readable; on RHEL the parent `/usr/local/lib/python3.11`
  and its `site-packages/` are sometimes pre-created `0700 root:root`
  by an earlier root pip run — verify they're `0755` after install or
  apache's `site.py` will silently skip the dir and `import aiodns`
  will fail.
- Don't use `pip install --user` — `--user` puts files under the
  invoking user's `~/.local`, which apache can't import from.

Versions required:
- `aiodns` >= 3.x (the c-ares-based async resolver used by
  `resolve-dns`)
- `pymysql` (any current 1.x)
- Python >= 3.10 is enforced by `hzmetrics.py` itself (see
  `_MIN_PYTHON` at the top of the file).  On Rocky 8 the system
  `/usr/bin/python3` is 3.6, so a separate `python3.11` install
  alongside is the supported configuration.

Test the Python version is discoverable: `hzmetrics.py` self-relaunches
into the highest-numbered `python3.N` (≥ 3.10) it can find on `PATH`,
so just having `python3.11` installed alongside the system `python3`
is enough.

### Dev installs (not for production)

For a development machine where you'll run tests or hack on the
code, `pyproject.toml` declares the same deps and wires a
`hzmetrics` console-script entry point:

```
pip install --user --break-system-packages -e .   # PEP 668-friendly
```

This is **not** the production install path — production hosts still
get `hzmetrics.py` dropped on `PATH` by the Makefile + cron pulled
in via the cron.d / spool drop-in, exactly as above.  `pyproject.toml`
is purely a dev / CI convenience so `pip install` resolves the dep
set instead of relying on system packages being present.

## File layout (post-install)

Everything lives under `/opt/hubzero/metrics/`, a self-contained tree
owned by the apache user.  Only the operator-facing log file lives
outside, at the conventional `/var/log/` location:

```
/opt/hubzero/metrics/bin/hzmetrics.py            the pipeline
/opt/hubzero/metrics/conf/hzmetrics.conf         unified per-tenant config (mode 600, apache)
/opt/hubzero/metrics/conf/hzmetrics.conf.sample  reference config (the documented template)
/opt/hubzero/metrics/conf/hzmetrics.cron.apache.sample            crontab template (apache crontab)
/opt/hubzero/metrics/state/hzmetrics.pid         PID lock (created at runtime)
/var/log/hubzero/metrics/manage.log              pipeline log (apache-writable)
```

Tree is owned `apache:apache`, mode 0750 — only the apache user (and
root) can see inside.  No files in `/etc/`, no `/var/run/` tmpfiles
dance, no `/var/spool/cron/` writing.  Orchestrator state lives in
the `pipeline_state` DB table; the only on-disk state is the lock
file.

The legacy reference scripts under `tests/legacy/` are **not**
installed on a production host.  They live in this repo only as the
A/B-test parity reference.

## Install via Makefile

From a checkout of this repo:

```
sudo make install              # deps + /opt tree + scripts (idempotent)
sudo -u apache crontab /opt/hubzero/metrics/conf/hzmetrics.cron.apache.sample
sudo make uninstall            # removes only files install added; rmdir empty dirs
make help                      # list targets (lint, test, test-ab, …)
```

`make install` is one root-only step that does everything that
needs root: installs deps (`python3.11-PyMySQL` via dnf, `aiodns`
via pip), creates `/opt/hubzero/metrics/{bin,conf,state}` and
`/var/log/hubzero/metrics/` owned `apache:apache` mode 0750 if
they don't already exist (only fixing perms when the service user
can't reach the directory — preserves existing per-host groupings
like `/var/log/hubzero/metrics → apache:access-logs`), and lays
down the project-shipped files.  Re-running it on a healthy install
is a no-op for the tree and a redundant overwrite for the files.

What `install` copies (all owned `apache:apache`):

- `hzmetrics.py` → `/opt/hubzero/metrics/bin/hzmetrics.py` (mode 755)
- `conf/hzmetrics.cron.apache.sample` → `/opt/hubzero/metrics/conf/hzmetrics.cron.apache.sample` (mode 644)
- `conf/hzmetrics.conf.sample` → `/opt/hubzero/metrics/conf/hzmetrics.conf.sample` (mode 644)

`install` deliberately does NOT drop `hzmetrics.conf` — it's an
operator-supplied secret (carries the DB password).  After `make
install`, the Makefile prints the remaining manual steps (the
`hzmetrics.conf` copy, the `crontab` registration; `setup-db` and
`migrate --apply` are now folded into `hzmetrics.py init` or the
auto-bootstrap on cron's first tick — see
[the First-time install section](#first-time-install) below).

Cron-style default is `spool` — the install registers the apache
user's crontab directly via `crontab(1)`, no `/etc/cron.d/`
drop-in.  Override with `CRON_STYLE=dropin make install` if your
hub's policy needs the global `/etc/cron.d/` flavor.

Overrides: `HZMETRICS_HOME` (install root, default `/opt/hubzero/metrics`),
`LOG_DIR` (default `/var/log/hubzero/metrics`), `INSTALL_OWNER` /
`INSTALL_GROUP` (default `apache`), and `DESTDIR` for staged installs
(`make install DESTDIR=/tmp/stage` for packaging dry-runs).

The cron entry is a single line, every five minutes, in the apache
user crontab:

```
*/5 * * * * /opt/hubzero/metrics/bin/hzmetrics.py tick
```

Register it via:

```
sudo -u apache crontab /opt/hubzero/metrics/conf/hzmetrics.cron.apache.sample
```

cronie auto-detects the user-crontab change; no daemon restart needed.

## hzmetrics.conf

The mandatory per-tenant config file.  Standard INI with three
sections.  See [`conf/hzmetrics.conf.sample`](../conf/hzmetrics.conf.sample)
for the documented form:

```ini
[hub]
site = <hub>
hub_dir = /var/www/<hub>

[db]
host = localhost
user = <hub>
password = <secret>
hub_db = <hub>
hub_db_prefix = jos_
metrics_db = <hub>_metrics

[dns]
nameserver = system        # or 127.0.0.1 with local unbound
concurrency = 100          # raise to 500 with unbound in front
timeout = 2.0
```

Drop it in place after `make install` has created the conf dir:

```
sudo install -o apache -g apache -m 0600 <your-config> \
    /opt/hubzero/metrics/conf/hzmetrics.conf
```

The pipeline resolves the config file from (high → low):

1. `hzmetrics.py -c FILE …` — explicit, used by multi-tenant
   crontabs (`-c /etc/hzmetrics/<hub>.conf` per tenant).
2. `$HZMETRICS_CONFIG` env var — handy for tests and ad-hoc
   invocations.
3. `/opt/hubzero/metrics/conf/hzmetrics.conf` — the default
   single-tenant location.

For DNS settings specifically, the chain extends further: built-in
defaults → `[dns]` section → `HZMETRICS_DNS_*` env vars → CLI flags
on `resolve-dns`.  If you don't include a `[dns]` section, the
built-in defaults are fine — `concurrency=100` against the system
resolver is benchmarked clean against Purdue's resolvers and
produces ~4 ms/IP cold.

## First-time install

After `make install` has seated `/opt/hubzero/metrics` and you've
dropped a populated `conf/hzmetrics.conf` in place, the script can
finish its own setup in one call:

```
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py init
```

`init` is idempotent and does:

  1. Asserts `site = <hubname>` is set in `/etc/hubzero.conf`
     (refuses otherwise — the site name prefixes every staged-log
     filename and a few DB conventions, so a silent "hub" fallback
     would collide on every multi-hub host).
  2. `mkdir -p` for every directory the pipeline writes to —
     `HZMETRICS_HOME/{bin,conf,state}`, `/var/log/hubzero/{daily,
     imported,metrics}`, and `/var/log/{httpd,apache2}/{daily,
     imported}`.
  3. `CREATE DATABASE IF NOT EXISTS <hub>_metrics`, then runs the
     baseline DDL and applies every pending migration.

The same machinery runs automatically on the first `cron` tick when
the process is owned by `apache` / `www-data` (see
[`_self_bootstrap` in architecture.md](architecture.md#self-bootstrap)) — so
operators who'd rather skip `init` and let cron handle it can do so,
provided `hzmetrics.conf` is in place before cron fires.

The CMS-side tables created by metrics
(`jos_resource_stats_tools_topvals`, `jos_session_geo`, etc.) are
created by the hub's own CMS migrations and shouldn't need anything
from `hzmetrics.py`.  If they're missing, see the `exclude_list`
schema work and check the hub's migration state.

The two underlying commands `init` composes (`setup-db` and `migrate
--apply`) are still individually invocable for the rare case where you
want to run one without the other.

## Verifying the install

`doctor` is the diagnostic entry point.  It walks the four phases
self-bootstrap touches and reports each:

```
# Full health check — reports every issue, fixes nothing:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py doctor

# Same, but attempt to repair the fixable ones (mkdir, CREATE
# DATABASE, run pending migrations) — same code paths self-bootstrap
# uses on cron startup:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py doctor --fix
```

Things `doctor` cannot fix from its own privileges (missing
`/etc/hubzero.conf` `site` line, root-owned parent dirs, MySQL down,
bad `hzmetrics.conf` credentials) are reported clearly so the operator
knows the next step.

The traditional smoke-tests still work after `init`:

```
# What does the pipeline see?
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py status

# Does DNS work?
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py resolve-dns \
    metrics web --dry-run 2025-07

# Does the daily run actually do anything?
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py run --force
```

Watch `/var/log/hubzero/metrics/manage.log` while the run is in
progress.  Each pipeline phase prints `[<phase>] …` start and end
markers.

## Logrotate

The pipeline writes to a single log file in
`/var/log/hubzero/metrics/manage.log`.  Each `tick` invocation creates
a fresh logger and reopens the file by name, so logrotate doesn't need
a postrotate hook — a plain stanza is sufficient:

```
/var/log/hubzero/metrics/manage.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    create 640 apache apache
}
```

## Catch-up after a stalled host

`cmd_run` is a three-mode state machine (see
[architecture.md → Catchup orchestration](architecture.md#catchup-orchestration-state-machine)):
when it detects a backlog it flips itself into `catchup` mode, drains
one month per tick using the per-month decision matrix, then enters
`rebuild` mode to refresh long-window summary cells.  All autonomous.

```
# Where is the orchestrator?  status shows mode + cursors:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py status

# Drive ticks manually if `tick` cadence is too slow:
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py run

# Resummarize a range without touching state.mode
# (useful for a one-shot rebuild after a data fix):
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py rebuild-summaries \
    --since 2022-01 --through 2024-12
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
