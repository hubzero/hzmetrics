# hzmetrics

`hzmetrics.py` is the analytics pipeline for a HUBzero-based science
gateway. It ingests Apache access logs and CMS authentication logs,
enriches the data (reverse DNS, domain classification, GeoIP, session
coalescing), and produces monthly summary statistics stored in a
MariaDB metrics database. Those statistics drive the hub's usage
reporting pages and grant reporting.

One Python file (~6000 lines) replaces a decade-plus accumulation of
PHP, Perl, and Bash scripts that previously lived at
`/opt/hubzero/bin/metrics/`. The legacy reference implementation is
preserved verbatim under [`tests/legacy/`](tests/legacy/) and is the
bug-for-bug parity target the A/B test harness compares against.

The reference deployment is the
[](https://) hub at Purdue. Other hubs
running the same code include , , and
historically .

## Quickstart

Install on a fresh HUBzero host (see [docs/deployment.md](docs/deployment.md)
for the full procedure):

```sh
# 1. Drop the pipeline on PATH (Makefile in source/ if present, else by hand).
sudo install -o apache -m 755 hzmetrics.py /opt/hubzero/bin/hzmetrics.py
sudo install -m 644 conf/hzmetrics.tmpfiles.conf /etc/tmpfiles.d/hzmetrics.conf
sudo install -m 644 conf/hubzero-metrics.cron.d /etc/cron.d/hubzero-metrics
sudo systemd-tmpfiles --create /etc/tmpfiles.d/hzmetrics.conf

# 2. Drop the DB credentials in place.
sudo install -d -o root -g apache -m 750 /etc/hubzero-metrics
sudo install -o root -g apache -m 640 access.cfg /etc/hubzero-metrics/access.cfg

# 3. First-time DB bootstrap + migrations.
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py setup-db
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py migrate --apply

# 4. Smoke test.
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py status
sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py run --force
```

One cron entry, every five minutes, does all the rest:

```
*/5 * * * * apache  python3 /opt/hubzero/bin/hzmetrics.py tick
```

`tick` refreshes the whoisonline map on every invocation; at `:30`
past each hour it also opportunistically runs the metrics pipeline
under a PID lock. A long-stalled host drains its log backlog one
month per hourly tick without operator intervention — 12 months of
backlog is ~6 hours of unattended catch-up.

`hzmetrics.py --help` lists every subcommand. The big ones:

```
tick                       cron entry (whoisonline + metrics at :30)
run [--dry-run]            autonomous metrics run
status                     pending vs imported log state
process --next             oldest pending month, foreground
import / analyze / summarize  individual stages, --month YYYY-MM
fill-geo / backfill-dnload    one-shot backfill utilities
migrate [--apply]          schema migrations
setup-db [--dry-run]       create the metrics DB schema from scratch
whoisonline                refresh real-time session geo map
```

Every mutating subcommand supports `--dry-run`; `--force` bypasses
the daily-state-already-completed guard on `run` / `process` /
`analyze` / `summarize`.

## Source layout

```
.
├── hzmetrics.py                       the entire pipeline (one file)
├── conf/hzmetrics.conf.sample              optional runtime overrides
├── conf/hubzero-metrics.cron.d             cron entry — /etc/cron.d/ form
├── conf/hubzero-metrics.cron.apache        cron entry — apache crontab form
├── conf/hzmetrics-logrotate-postrotate.sh  logrotate hook
├── conf/hzmetrics.tmpfiles.conf            systemd-tmpfiles config
│                                      (creates /var/run/hzmetrics/ at boot)
├── README.txt                         historical hub-install notes
│                                      (superseded by docs/)
├── docs/                              markdown documentation (rendered
│                                      into gh-pages/public/)
├── gh-pages/                          static-site templates + builder
│   ├── build_site.py                  docs/ → gh-pages/public/
│   ├── site.json                      site metadata + group definitions
│   ├── templates/                     home.html, group.html, doc.html
│   ├── assets/site.css                site stylesheet
│   ├── requirements.txt               pip deps for the builder (markdown-it-py)
│   └── public/                        built static site (served by Pages)
├── tests/legacy/                      pre-rewrite PHP/Perl/Bash pipeline,
│                                      preserved as the A/B parity reference
└── tests/ab/                          A/B test harness (26 ports)
    ├── run-all.sh                     A/B mode: legacy vs new, diff outputs
    ├── run-all-golden.sh              golden mode: new vs frozen snapshots
    ├── setup_test_dbs.sh              create test DBs + load reference data
    ├── port_*/                        one per pipeline stage (see testing.md)
    └── fixtures/                      shared test fixtures
```

## Documentation site

The docs under [`docs/`](docs/) are plain markdown and are rendered
into a static HTML site under `gh-pages/public/`. The site is
published to GitHub Pages by `.github/workflows/docs.yml` via the
Actions deploy flow (Settings → Pages → Source = GitHub Actions).

Local preview:

```sh
pip3.11 install -r gh-pages/requirements.txt   # one-shot; same pip you used for aiodns
python3 gh-pages/build_site.py                 # rebuild gh-pages/public/ from docs/
```

The builder is `markdown-it-py` + a few small templates. The CI
workflow rebuilds on every PR/push and fails if the committed
`gh-pages/public/` is out of sync with what the build produces — so
reviewers see the visible-output delta in PR diffs. On `main`, the
same workflow uploads `gh-pages/public/` as a Pages artifact and
deploys it.

Start reading at [`docs/README.md`](docs/README.md) — it links every
other doc in roughly the order to read them.

## Conventions

### Schema is self-installing

A `migrations` table in the metrics DB tracks applied schema deltas.
`hzmetrics.py migrate --apply` brings any database up to current
schema. No "did we run the SQL by hand on this host?" lookups.

### Catch-up is built in

Each `tick` invocation processes at most one full month of backlog
under a PID-lock guard, and a daily-state file gates the metrics work
to once per calendar day. A stalled pipeline gradually drains its
own backlog — `tick` resumes the moment the cron entry starts firing
again, no operator intervention needed.

## Build logs / observability

The pipeline writes a single log file:

```
/var/log/hubzero/metrics/hzmetrics.log
```

Each stage prints `[<stage>] start` and `[<stage>] done` markers; a
healthy day's `tick` chain shows the full sequence inline. Searching
for `FAIL`, `ERROR`, or `unrecognized` surfaces recoverable problems.
`hzmetrics.py status` is the structured view of the same data. See
[`docs/operations.md`](docs/operations.md) for the runbook.

## Where things in this codebase came from

| Tree path | Original upstream |
|---|---|
| `hzmetrics.py` | New code for this rewrite (2026-05) |
| `tests/legacy/xlogfix_*.{php,pl,sh}` | `/opt/hubzero/bin/metrics/` on the legacy hub install, snapshot pre-aa245f7 (the TRUE pre-refactor commit used as the A/B baseline) |
| `tests/legacy/import/` | `/opt/hubzero/bin/metrics/import/` — legacy fetch/import/archive scripts |
| `tests/legacy/includes/` | shared PHP includes (`func_misc.php` and friends) |
| `tests/legacy/gen_tool_*.php` | legacy tool-stats / tops / toplists generators |
| `hubzero-metrics.cron.*` | distilled from the seven legacy cron entries into a single `tick` line |
| `docs/usage-tables.md` | adapted from J.M. Sperhac's *Hub usage data overview and table translator* (Jan 2025) |
| `docs/operations.md` | follows the 2014–2016 *Basic new-month checks* runbook (Sperhac, / era) |

The commit history is annotated: each A/B-caught divergence is
recorded as a `A/B test: <port> — caught … divergence` commit, and
the relevant Jira tickets (, , , ,
, , …) appear in the
[`docs/README.md`](docs/README.md) cross-reference list.

## Acknowledgments

The original HUBzero metrics package and its ongoing development was
supported in part by . Long-term stewardship
of the codebase has been carried by J.M. Sperhac (SDSC),
among others. This rewrite
builds directly on their work.
