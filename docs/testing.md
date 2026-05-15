# Testing

Brief summary of the A/B test harness under `tests/ab/`.  Detail
intentionally not duplicated from individual port test READMEs — read
`tests/ab/run-all.sh` and the per-port `run.sh` files for the source
of truth.

## Two test modes

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

Both modes produce the same pass/fail outcome on a current codebase.
Both modes pass in the current tree.

## What's tested

26 test directories under `tests/ab/port_*`:

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

**Defensive tests (5):** `port_fuzz` (4 fuzz harnesses with 2000+
randomized cases each), `port_idempotency` (re-runs analyze+summarize
on the same DB), `port_dryrun` (every `--dry-run` writes zero rows),
`port_empty_input` (each port no-ops cleanly on empty input),
`port_determinism` (two fresh-DB runs are byte-identical).

## Running

```bash
# Bootstrap once per host (creates test DBs, loads reference data)
tests/ab/setup_test_dbs.sh --bootstrap

# Run the full A/B suite
tests/ab/run-all.sh

# Or the golden-mode round (no legacy needed)
tests/ab/run-all-golden.sh

# Run a single port
tests/ab/port_fill_domain/run.sh
tests/ab/port_fill_domain/run_golden.sh
```

`setup_test_dbs.sh --reset` truncates everything and reloads
reference data — used between tests.

## When the harness catches things

Real bugs surfaced during the port and fixed in the process:

- **`summary_misc_vals` rowid=3 NULL handling** —
  `SUM(duration)` returns NULL on an empty period; legacy writes
  empty string but the Python port was writing `"0"`.  Caught by
  `port_period_sweep` at anchor months with no data.
- **`xlogfix_middleware_cpu.pl` semantics** — three divergences
  caught by `port_middleware`: banker's rounding vs round-half-up in
  ROUND() on DOUBLE columns; `cpu.pl` does insert-only, doesn't
  update; `cpu.pl` doesn't filter `event = '[waiting]'`; `cpu.pl`
  uses `<=` not `<` on its update check.
- **`fill-domain` day-before-month-start** — off-by-one on the lower
  bound of the date window.

Documented in commit history under `A/B test: <port> — caught …` and
`A/B: …` messages.

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
