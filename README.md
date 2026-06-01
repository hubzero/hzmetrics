<p align="center">
  <img src="gh-pages/assets/logo.svg" alt="" width="120" height="120">
</p>

<h1 align="center">Hubzero Metrics Pipeline</h1>

<p align="center">
  <em>Apache logs &rarr; MariaDB analytics, in one Python file.</em>
</p>

<p align="center">
  <a href="https://github.com/hubzero/hzmetrics/actions/workflows/tests.yml"><img alt="tests" src="https://github.com/hubzero/hzmetrics/actions/workflows/tests.yml/badge.svg"></a>
  <a href="https://github.com/hubzero/hzmetrics/actions/workflows/docs.yml"><img alt="docs CI" src="https://github.com/hubzero/hzmetrics/actions/workflows/docs.yml/badge.svg"></a>
  <a href="https://hubzero.github.io/hzmetrics/"><img alt="documentation" src="https://img.shields.io/badge/docs-hubzero.github.io%2Fhzmetrics-2456c2?logo=github&logoColor=white"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white">
  <a href="LICENSE"><img alt="license: MIT" src="https://img.shields.io/badge/license-MIT-007ec6"></a>
  <img alt="status: beta" src="https://img.shields.io/badge/status-beta-d54a3c">
</p>

---

`hzmetrics.py` is the analytics pipeline for a HUBzero-based science
gateway. It ingests Apache access logs and CMS authentication logs,
enriches them (reverse DNS, domain classification, GeoIP, session
coalescing), and produces monthly summary statistics in a MariaDB
metrics database. Those statistics drive the hub's usage reporting
pages and grant reporting.

One Python file (~8000 lines) replaces the decade-plus accumulation
of PHP, Perl, and Bash scripts that previously lived at
`/opt/hubzero/bin/metrics/`. The legacy reference implementation is
preserved verbatim under [`tests/legacy/`](tests/legacy/) and is the
bug-for-bug parity target the A/B test harness compares against.

## Quickstart

```sh
# 1. Deps + /opt tree + scripts (root; idempotent).
sudo make install

# 2. Drop the unified per-tenant config in place (DB creds + DNS settings).
sudo install -o apache -g apache -m 0600 hzmetrics.conf \
    /opt/hubzero/metrics/conf/hzmetrics.conf

# 3. Create the metrics DB, run baseline DDL, apply migrations.
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py init

# 4. Confirm everything is healthy.
sudo -u apache python3 /opt/hubzero/metrics/bin/hzmetrics.py doctor

# 5. Register the cron line.
sudo -u apache crontab /opt/hubzero/metrics/conf/hzmetrics.cron.apache.sample
```

`make install`, `init`, and `doctor` are idempotent. The same `init`
machinery also runs automatically on the first cron tick when invoked
as `apache` / `www-data`, so if you skip step 3 the next tick will
catch up — see
[`docs/architecture.md → Self-bootstrap`](docs/architecture.md#self-bootstrap).

The cron line is one entry, every five minutes:

```
*/5 * * * * python3 /opt/hubzero/metrics/bin/hzmetrics.py tick
```

`tick` refreshes the whoisonline map every invocation; at `:30` past
each hour it also opportunistically runs the metrics pipeline under a
PID lock. The pipeline is a three-mode state machine (`normal`,
`catchup`, `rebuild`) — a multi-year backlog drains autonomously
without operator intervention.

For everything else, `hzmetrics.py --help` and the
[full documentation](https://hubzero.github.io/hzmetrics/).

## Source layout

```
.
├── hzmetrics.py                              the entire pipeline
├── Makefile                                  install / uninstall / test / lint
├── conf/                                     templates: hzmetrics.conf.sample, cron
├── docs/                                     plain-markdown documentation
├── gh-pages/                                 static-site templates + builder
└── tests/
    ├── legacy/                               pre-rewrite PHP/Perl/Bash baseline
    └── ab/                                   A/B + golden + defensive harness
                                              (44 ports — see docs/testing.md)
```

## Documentation

Start at [`docs/README.md`](docs/README.md) (or the
[rendered site](https://hubzero.github.io/hzmetrics/)). Most-touched
operational pages:

- [`docs/deployment.md`](docs/deployment.md) — install, cron,
  logrotate, hzmetrics.conf.
- [`docs/operations.md`](docs/operations.md) — runbook: catch-up,
  stuck lock, bot inflation, DNS issues, crash recovery,
  ANALYZE TABLE, etc.
- [`docs/architecture.md`](docs/architecture.md) — pipeline phases,
  tables, scheduling, the catchup state machine, self-bootstrap.
- [`docs/testing.md`](docs/testing.md) — A/B + golden + defensive
  test modes.

## Acknowledgments

The HUBzero metrics subsystem was originally written in Perl by
Swaroop Shivarajapura and later ported to PHP by Nicholas J.
Kisseberth. Long-term stewardship of the codebase has been carried
by J.M. Sperhac (SDSC), among others. This Python rewrite builds
directly on their work.
