# Testing

Brief summary of the A/B test harness under `tests/ab/`.  Detail
intentionally not duplicated from individual port test READMEs — read
`tests/ab/run-all.sh` and the per-port `run.sh` files for the source
of truth.

## Test modes

The harness can run in two modes:

1. **A/B mode** (`tests/ab/run-all.sh`) — runs each port's legacy
   PHP/Perl/Bash script and the new `hzmetrics.py` equivalent side by
   side, diffs every output table.  Requires `tests/legacy/` to be
   present.
2. **Golden mode** (`tests/ab/run-all-golden.sh`) — runs only the new
   code, diffs against a frozen snapshot of legacy output captured at
   parity time (`tests/ab/port_*/golden/*.tsv`).  Does not require
   `tests/legacy/`.  Simulates the world where the legacy reference
   has been removed.
3. **Defensive mode** (`tests/ab/run-defensive.sh`) — runs the
   new-code-only tests that do not need legacy or golden snapshots:
   fuzz, idempotency, dry-run safety, empty input, determinism,
   cross-table invariants, and CLI error contracts.

The A/B and golden modes produce the same pass/fail outcome on a
current codebase.  CI runs golden plus defensive mode.

## What's tested

46 test directories under `tests/ab/port_*`:

**Per-port A/B (16):** `port_andmore_usage`, `port_clean_bots`,
`port_fill_domain`, `port_fill_ipcountry`, `port_fill_user_info`,
`port_gen_tool_stats`, `port_gen_tool_toplists`,
`port_gen_tool_tops`, `port_identify_bots`, `port_import_apache`,
`port_import_auth`, `port_import_hub_data`, `port_import_webhits`,
`port_logfix_session`, `port_middleware`, `port_whoisonline`.

**Integration (2):** `port_pipeline` (full analyze + summarize chain
on synthetic data), `port_realdata` (same chain on a captured
production-data slice — gated by snapshot presence).

**Coverage tests (3):** `port_summarize_month` (the most
metric-dense single port), `port_period_sweep` (24 anchor-port
combinations exercising period boundary arithmetic),
`port_invariants` (cross-table rules like
`summary_user_vals[rowid=1] = SUM([6,7,8])`).

**Defensive tests (6):** `port_fuzz` (4 fuzz harnesses with 2000+
randomized cases each), `port_idempotency` (re-runs analyze+summarize
on the same DB), `port_dryrun` (every `--dry-run` writes zero rows),
`port_empty_input` (each port no-ops cleanly on empty input),
`port_determinism` (two fresh-DB runs are byte-identical),
`port_cli_contracts` (invalid CLI/config paths exit non-zero).

**Orchestration (5):** `port_discovery` (source-log enumeration
across `daily/`, `daily/YYYY/`, `daily.holding/`),
`port_state` (DB-backed `pipeline_state` read/write + file→DB
bootstrap), `port_decisions` (the three Phase-C decision helpers +
every row of the catchup decision matrix), `port_cmd_run`
(three-mode state machine: mode dispatch + transitions + per-month
routing via monkey-patched DB), `port_rebuild_summaries`
(the manual-range CLI + extended `status` output).

**Catchup correctness (2):** `port_periods_filter` (`do_summarize(periods=(1,))`
writes exactly the period-1 grid and zero rows in any other period;
inverse pass with `periods=None` populates all six),
`port_rebuild_correctness` (loads month M2 fully summarized; adds
month M1 rows; resummarizes M2; asserts period-14 refreshed to
include M1 while period-1 stays unchanged — the core promise of
rebuild mode).

**Install + crash recovery (4):** `port_bootstrap` (`_self_bootstrap`
identity gate + site-name guard + `_expected_dirs` contract + the
`init` / `doctor` exit codes), `port_import_atomic` (per-file
import is transactional and the `imported_sources` marker survives
post-COMMIT crashes — `forget-import` reverses both halves cleanly),
`port_lock` (PID-file format and `init_start_epoch` stale-PID
detection across reboot / container restart), `port_month_complete`
(data-driven month-closed check that gates `logfix-session` to month
boundary).

**Filter regression guards (7):** `port_dnload_classify` (Python
`_is_download_url` covers every download-extension and download-path
shape), `port_dnload_backfill_regex` (SQL-side backfill-dnload regex
correctly handles literal-dot vs any-char — pins the silent fix in
db5d8ba), `port_referer_spam` (login/?return=, resources/browse?,
citations/browse, and /register empty-Referer crawler-spam regexes),
`port_msie_filter` (date-bound MSIE-Trident UA regex + watermark + the
import-apache wiring source-grep), `port_crawl_filters_2026`
(/register Referer-gating + date-bound /events/<old-year>/ filter that
measures from the log line's datestamp, not date.today()),
`port_filter_chain` (the shared `_filter_apache_row` helper that both
do_import_apache and do_import_webhits route through — pins the
per-row contract and asserts both importers wire into it, so a new
filter added to one side can't silently drift the other),
`port_session_split` (1800-second session boundary).

**Window-boundary semantics (1):** `port_window_boundaries` (27
assertions: period range arithmetic across month / quarter / year /
fiscal-year boundaries, leap years, DST edges).

## Running

### Prerequisites

- MariaDB running locally; an account with `CREATE DATABASE` /
  `GRANT` privileges for the bootstrap step (typically via `sudo
  mysql` using the system socket auth).
- PHP CLI on `PATH` — the legacy reference under `tests/legacy/`
  shells out to `php`, `perl`, and `bash`.
- The BIND `host(1)` utility — the legacy DNS step (`xlogfix_dns_v2.sh`
  + `xlogfix_dns_worker.php`) shells out to `/usr/bin/host`.  On
  Debian/Ubuntu: `sudo apt install bind9-host`.  Without it, 3
  DNS-dependent tests (`port_pipeline`, `port_determinism`,
  `port_whoisonline`) fail with fake mismatches where legacy reports
  `?` / `(unknown)` while the new Python's aiodns resolves cleanly.
- Python runtime deps from `pyproject.toml` (`pymysql`, `aiodns`).
- `tests/ab/fixtures/test_access.cfg` must name a real local DB user.
  The committed sample leaves `$db_user = ''`; either patch a temporary
  cfg and point `HZMETRICS_ACCESS_CFG` at it, or patch the fixture in a
  disposable checkout the way CI does.

### Commands

```bash
# Bootstrap once per host (creates test DBs, loads reference data)
tests/ab/setup_test_dbs.sh --bootstrap

# Run the full A/B suite
tests/ab/run-all.sh

# Or the golden-mode round (no legacy needed)
tests/ab/run-all-golden.sh

# New-code-only defensive checks (also no legacy needed)
tests/ab/run-defensive.sh

# Run a single port
tests/ab/port_fill_domain/run.sh
tests/ab/port_fill_domain/run_golden.sh
```

`setup_test_dbs.sh --reset` truncates everything and reloads
reference data — used between tests.  Top-level drivers report
`pass/fail/skip`; a per-port runner can exit `77` to mark a real skip
(currently used when the optional production snapshot is absent).

### Running against a non-default cfg

Both `setup_test_dbs.sh` and `conftest.sh` honor
`HZMETRICS_ACCESS_CFG=<path>` (env override).  The bootstrap reads
`hub_db`, `metrics_db`, `db_host`, `db_user`, and `db_pass` from that
cfg, creates the named test DBs, creates the DB user if needed, and
grants it access.  `TEST_USER` is accepted only as a consistency
override; it must match the cfg's `$db_user` so `mysql` and
`hzmetrics.py` connect as the same account.

## When the harness catches things

Real bugs surfaced during the port, with their commit messages
preserved in the log for reference:

- **`fill-domain` day-before-month-start** — legacy `findWeeks()`
  starts a week-chunk on the day BEFORE the month begins (so
  `2025-06-30 23:59:00` belongs to July 2025's first chunk).
  Caught by `port_fill_domain`; commit
  `A/B test: fill-domain — caught & fixed day-before-month-start
  divergence`.
- **`xlogfix_middleware_cpu.pl` — four real divergences** in one
  commit (`A/B test: middleware-{wall,cpu} — caught three real
  divergences`):
  - MariaDB `ROUND()` is banker's rounding, Perl `int($x + 0.5)` is
    round-half-up → fixed to `FLOOR(x + 0.5)`.
  - `cpu.pl` only UPDATEs existing toolstart rows, never INSERTs
    (the wall version does both).
  - `cpu.pl`'s UPDATE check is `<= 0` (includes `cputime=0`), not
    `< 0`.
  - `cpu.pl` does not filter `joblog.event = '[waiting]'`; wall
    does.  Caught when both ports were initially symmetric.
- **`andmore-usage` datetime suffix** — legacy stores `'-01'`,
  summarize uses `'-00'`; new port was using `'-00'` for both.
  Commit `A/B test: andmore-usage — caught datetime suffix
  divergence`.
- **`logfix-session` cross-week state** — Perl declares session
  state vars at script scope, so an in-flight session persists
  across the 4 week-chunks of a month.  The Python port initially
  reset state per chunk.  Commit `A/B test: logfix-session — caught
  cross-week state divergence`.
- **`summarize-month` reg_users col=1 missing-JOIN** — legacy
  queries `userlogin_lite` directly (no JOIN) for col=1; my port
  was unconditionally joining `jos_xprofiles_metrics` for every
  col, so when `xprofiles_metrics` is empty it under-counted.
  Commit `A/B test: summarize-month — caught reg_users col=1
  missing-JOIN divergence`.
- **`import-auth` bracket-strip** — `[user[sub]]` should produce
  `user`, not `user[sub]`.  PHP `ltrim($x, '[') + rtrim($x, ']')`
  use charlist semantics (strip ALL leading `[` and trailing `]`);
  the port's regex was capturing the inner-bracketed content
  literally.  Commit `A/B: deepen 5 fixtures — caught import-auth
  bracket-strip bug`.
- **`gen-tool-stats` float→int rounding** — Python float bound as
  numeric literal hits MariaDB's banker's rounding; PHP stringifies
  first and hits half-away-from-zero.  `488.5 → 488` vs `488.5 →
  489`.  Fix: stringify floats before binding.  Commit `A/B:
  deepen 5 more fixtures, caught gen-tool-stats float→int rounding
  bug`.
- **`download_users` rowid=4 vs rowid=8 filter mismatch** — the two
  rowids use DIFFERENT WHERE filters in legacy (rowid=4 doesn't
  exclude `login_ips` or cap `duration < 900`), but my port was
  reusing `dl_users_period_tmp` built for rowid=8.  Caught by
  deepening `port_summarize_month`'s fixture with a registered-user
  downloader.  Commit `A/B: deepen summarize-month, caught
  download_users rowid=4 filter mismatch`.
- **`summary_misc_vals` rowid=3 NULL handling** — `SUM(duration)`
  returns NULL on an empty period; legacy `db_fetch` returns NULL →
  `dbquote(NULL)` writes empty string; the port coerced to `0`.
  Caught by `port_period_sweep` at anchor months with no data.
  Commit `A/B: period sweep test + fix misc_usage NULL → empty-
  string parity`.

Plus the **"A/B re-baseline"** commit (`Roll back dnload-at-import
and action-filter from hzmetrics.py`) — the most important harness
catch.  An initial legacy snapshot included two **post-aa245f7**
behaviors that had been absorbed into the new port: `import-apache`
setting `dnload=1` inline, and `import-auth` filtering
`action IN ('login','simulation')` at insert time.  Re-baselining
the harness against the true pre-refactor snapshot revealed that
the port had unintentionally inherited those changes; both got
rolled back.  This is the divergence the docs talk about under
"bug-for-bug parity is hard to verify when your baseline is wrong."

Documented in commit history under `A/B test: <port> — caught …`
and `A/B: …` messages.

## What can't be tested locally

`port_realdata` requires a captured production-data snapshot
(`tests/ab/port_realdata/snapshot/*.sql.gz`).  The snapshot directory
is gitignored because the raw data contains real usernames, emails,
and IPs; the test skips gracefully when the snapshot isn't present.
See `tests/ab/port_realdata/capture.sh` for how to capture one when
you have read access to a production database.

Some tests touch network resources (`fill-ipcountry` hits
`help.hubzero.org/ipinfo/v1`, `resolve-dns` uses the local resolver
which forwards out).  These work fine offline against the cached
results in `tests/ab/fixtures/`, but require network for fresh data.
