"""Pure-Python tests for cmd_audit and the audit helpers.

cmd_audit's behavior is purely a function of what mysql_query returns,
so we monkey-patch that to feed in scripted per-(table, column) results
and assert against the findings the audit emits.

Coverage targets:

  * `_next_yyyymm` — month arithmetic used by the range-collapse
    logic.  Easy to break around the December → January boundary.

  * The median-based threshold rule: a month is flagged only when its
    %missing exceeds max(median × 5, floor).  Tested both shapes —
    one outlier in an otherwise-clean table, and a uniformly-high
    table where no individual month is exceptional.

  * Range collapse: consecutive flagged months for the same check
    must emit one ranged backfill command, not N separate ones.

  * Return codes: 0 if no findings, 1 if any (cron-friendly).

The actual SQL is matched by substring against the table name in the
FROM clause so the mock can route queries to the right scripted
result; the structural-check queries that don't match anything
default to "no findings".
"""
import argparse
import sys
import unittest
from datetime import datetime as dt
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
import hzmetrics


class NextYyyymmTest(unittest.TestCase):
    def test_within_year(self):
        self.assertEqual(hzmetrics._next_yyyymm("2025-03"), "2025-04")

    def test_year_boundary(self):
        self.assertEqual(hzmetrics._next_yyyymm("2025-12"), "2026-01")

    def test_leap_year_february(self):
        # _next_yyyymm doesn't care about leap years (it just bumps the
        # YYYY-MM tag), but the boundary into March still needs to be
        # correct regardless of whether it was a leap year.
        self.assertEqual(hzmetrics._next_yyyymm("2024-02"), "2024-03")


class CmdAuditTest(unittest.TestCase):
    """Mock mysql_query + db_credentials + period_incomplete_months,
    then drive cmd_audit with various scripted per-table data."""

    def setUp(self):
        # Save originals so tearDown can restore.
        self._orig_mq = hzmetrics.mysql_query
        self._orig_mc = hzmetrics.mysql_column
        self._orig_ms = hzmetrics.mysql_scalar
        self._orig_db = hzmetrics.db_credentials
        self._orig_pi = hzmetrics.period_incomplete_months
        # Override the heavy bits.
        hzmetrics.db_credentials = lambda: ("h", "u", "p", "testdb")
        hzmetrics.period_incomplete_months = lambda *a, **kw: []
        # Capture log output for assertions.
        self._log_records: list = []
        self._orig_handlers = list(hzmetrics.log.handlers)
        for h in self._orig_handlers:
            hzmetrics.log.removeHandler(h)
        import logging

        class _Capture(logging.Handler):
            def __init__(self, sink):
                super().__init__()
                self.sink = sink

            def emit(self, record):
                self.sink.append((record.levelname, self.format(record)))

        cap = _Capture(self._log_records)
        cap.setFormatter(logging.Formatter("%(message)s"))
        hzmetrics.log.addHandler(cap)
        hzmetrics.log.setLevel(logging.DEBUG)

    def tearDown(self):
        hzmetrics.mysql_query = self._orig_mq
        hzmetrics.mysql_column = self._orig_mc
        hzmetrics.mysql_scalar = self._orig_ms
        hzmetrics.db_credentials = self._orig_db
        hzmetrics.period_incomplete_months = self._orig_pi
        for h in list(hzmetrics.log.handlers):
            hzmetrics.log.removeHandler(h)
        for h in self._orig_handlers:
            hzmetrics.log.addHandler(h)

    def _emitted(self) -> str:
        return "\n".join(msg for _, msg in self._log_records)

    def _set_mock(self, data, *, webhits_months=None, hits_expected=None,
                  stored_hits=None, import_files=None):
        """data: dict mapping (table, column) → list of (ym, total, missing)
        tuples for the enrichment-coverage checks (via mysql_query).

        Keyword args drive the structural checks that use mysql_column /
        mysql_scalar:
          webhits_months: ['YYYY-MM', ...] present in webhits      (check H)
          hits_expected:  {dstart_str: SUM(hits)} windowed sum      (check H)
          stored_hits:    {(ym, period): stored rowid=8 value}      (check H)
          import_files:   {target_table: [filename, ...]}           (check I)

        Structural checks C–G are given finding-free defaults (fresh
        summary max-datetime; empty result sets elsewhere) so a "clean"
        scenario returns rc=0."""
        webhits_months = webhits_months or []
        hits_expected = hits_expected or {}
        stored_hits = stored_hits or {}
        import_files = import_files or {}

        def mq(sql, params=None):
            if "SELECT MIN(d) FROM" in sql:
                # --all probe — return an old start
                return [(dt(2014, 1, 1, 0, 0, 0),)]
            if "HAVING total" in sql:
                # The audit per-check query.  Find which table + which
                # missing-column predicate by scanning _AUDIT_CHECKS.
                for table, column, pred, _r in hzmetrics._AUDIT_CHECKS:
                    if f".{table} " in sql and pred in sql:
                        return list(data.get((table, column), []))
                return []
            if "MAX(datetime)" in sql:
                # Check C freshness: a far-future max keeps every summary
                # table "fresh" so the check stays silent by default.
                return [(dt(2099, 1, 1, 0, 0, 0),)]
            # All other structural queries (invariant, dirty-stuck,
            # period-14 regression, source coverage, missing period-1)
            # default to "no violations".
            return []

        def mcol(sql, params=None):
            if ".webhits" in sql and "DATE_FORMAT" in sql:
                return list(webhits_months)          # check H month list
            if ".imported_sources" in sql:
                target = params[0] if params else None
                return list(import_files.get(target, []))  # check I files
            return []

        def mscalar(sql, params=None):
            if "SUM(hits)" in sql and ".webhits" in sql:
                return hits_expected.get(params[0] if params else None)
            if "summary_misc_vals" in sql and "value" in sql:
                if params:
                    return stored_hits.get((params[1][:7], params[0]))
                return None
            return None

        hzmetrics.mysql_query = mq
        hzmetrics.mysql_column = mcol
        hzmetrics.mysql_scalar = mscalar

    def _args(self, **kw):
        defaults = dict(months=24, all=False, floor=0.05)
        defaults.update(kw)
        return argparse.Namespace(**defaults)

    # ------------------------------------------------------------------
    # Anomaly rule
    # ------------------------------------------------------------------

    def test_clean_table_returns_0(self):
        # 24 months, all with 0 missing → audit should pass.
        rows = [(f"2024-{i:02d}", 1000, 0) for i in range(1, 13)] + \
               [(f"2025-{i:02d}", 1000, 0) for i in range(1, 13)]
        self._set_mock({(t, c): rows for t, c, _p, _r in hzmetrics._AUDIT_CHECKS})
        rc = hzmetrics.cmd_audit(self._args())
        self.assertEqual(rc, 0)
        self.assertIn("all checks passed", self._emitted())

    def test_single_outlier_month_flagged(self):
        # 23 clean months + one with 50% missing.  Median is 0,
        # threshold drops to the 5% floor; 50% > 5% → flagged.
        rows = [(f"2024-{i:02d}", 1000, 0) for i in range(1, 13)] + \
               [(f"2025-{i:02d}", 1000, 0) for i in range(1, 12)] + \
               [("2025-12", 1000, 500)]
        # Apply outlier only to web.host so we know exactly which
        # check should fire; others stay clean.
        data = {(t, c): [(f"2024-{i:02d}", 1000, 0) for i in range(1, 13)] +
                        [(f"2025-{i:02d}", 1000, 0) for i in range(1, 13)]
                for t, c, _p, _r in hzmetrics._AUDIT_CHECKS}
        data[("web", "host")] = rows
        self._set_mock(data)
        rc = hzmetrics.cmd_audit(self._args())
        self.assertEqual(rc, 1)
        emitted = self._emitted()
        self.assertIn("web.host", emitted)
        # Remediation command emitted with the right month
        self.assertIn("resolve-dns metrics web 2025-12", emitted)

    def test_uniformly_high_baseline_is_not_an_anomaly(self):
        # Every month sits at 8% missing.  Median = 0.08, threshold =
        # max(0.08 * 5, 0.05) = 0.40.  No month exceeds 40% — no findings.
        rows = [(f"2024-{i:02d}", 1000, 80) for i in range(1, 13)] + \
               [(f"2025-{i:02d}", 1000, 80) for i in range(1, 13)]
        data = {(t, c): rows for t, c, _p, _r in hzmetrics._AUDIT_CHECKS}
        self._set_mock(data)
        rc = hzmetrics.cmd_audit(self._args())
        self.assertEqual(rc, 0)

    def test_small_months_skipped(self):
        # Months with < _AUDIT_MIN_ROWS=100 rows are filtered out by
        # the HAVING clause server-side.  We simulate by simply not
        # returning them — verify cmd_audit doesn't crash on an
        # otherwise-empty dataset (e.g. brand-new install).
        data = {(t, c): [] for t, c, _p, _r in hzmetrics._AUDIT_CHECKS}
        self._set_mock(data)
        rc = hzmetrics.cmd_audit(self._args())
        self.assertEqual(rc, 0)

    # ------------------------------------------------------------------
    # Range collapse
    # ------------------------------------------------------------------

    def test_consecutive_months_collapse_into_range(self):
        # Eight consecutive bad months → one range command, not eight.
        clean = [(f"2024-{i:02d}", 1000, 0) for i in range(1, 5)] + \
                [(f"2025-{i:02d}", 1000, 0) for i in range(1, 13)]
        bad   = [(f"2024-{i:02d}", 1000, 500) for i in range(5, 13)]
        data = {(t, c): clean + ([] if (t, c) != ("websessions", "domain") else bad)
                for t, c, _p, _r in hzmetrics._AUDIT_CHECKS}
        # websessions.domain: 8 bad months (2024-05..2024-12) + clean 2025
        data[("websessions", "domain")] = (
            [(f"2025-{i:02d}", 1000, 0) for i in range(1, 13)] + bad
        )
        self._set_mock(data)
        rc = hzmetrics.cmd_audit(self._args())
        self.assertEqual(rc, 1)
        emitted = self._emitted()
        # One ranged command — not eight single-month commands.
        self.assertIn("fill-domain metrics websessions 2024-05..2024-12",
                      emitted)
        self.assertNotIn("fill-domain metrics websessions 2024-05\n",
                         emitted)

    def test_non_consecutive_months_emit_separately(self):
        # 2024-05 and 2024-09 bad, others clean — no range collapse
        # because they're not consecutive.
        rows = [(f"2024-{i:02d}", 1000, 0) for i in range(1, 13)] + \
               [(f"2025-{i:02d}", 1000, 0) for i in range(1, 13)]
        # Override two months in rows to be bad.
        bad_rows = []
        for ym, total, missing in rows:
            if ym in ("2024-05", "2024-09"):
                bad_rows.append((ym, total, 500))
            else:
                bad_rows.append((ym, total, missing))
        data = {(t, c): rows for t, c, _p, _r in hzmetrics._AUDIT_CHECKS}
        data[("web", "ipcountry")] = bad_rows
        self._set_mock(data)
        rc = hzmetrics.cmd_audit(self._args())
        self.assertEqual(rc, 1)
        emitted = self._emitted()
        self.assertIn("fill-ipcountry metrics web 2024-05", emitted)
        self.assertIn("fill-ipcountry metrics web 2024-09", emitted)
        # If a range had been emitted, the 2024-06..08 months would
        # appear in the command line.  They shouldn't.
        self.assertNotIn("2024-05..2024-09", emitted)

    # ------------------------------------------------------------------
    # Check H — hits summary-cell parity (regression guard for the
    # inclusive-start `>=` webhits window).
    # ------------------------------------------------------------------

    def test_hits_cell_drift_flagged(self):
        # webhits window sum for 2025-07 is 6900, but the stored period-1
        # cell reads 6200 — exactly what a regression to strict `>` would
        # produce (day-1 dropped).  H must flag it.  (For 2025-07, period
        # 1 and 3 share the window 2025-07-01..2025-08-01.)
        data = {(t, c): [] for t, c, _p, _r in hzmetrics._AUDIT_CHECKS}
        self._set_mock(
            data,
            webhits_months=["2025-07"],
            hits_expected={"2025-07-01": 6900},
            stored_hits={("2025-07", 1): "6200", ("2025-07", 3): "6900"},
        )
        rc = hzmetrics.cmd_audit(self._args(all=True))
        self.assertEqual(rc, 1)
        emitted = self._emitted()
        self.assertIn("hits cell drift", emitted)
        self.assertIn("rebuild-webhits --month 2025-07", emitted)

    def test_hits_cell_match_no_finding(self):
        # Stored cells equal the windowed sum → H stays silent.
        data = {(t, c): [] for t, c, _p, _r in hzmetrics._AUDIT_CHECKS}
        self._set_mock(
            data,
            webhits_months=["2025-07"],
            hits_expected={"2025-07-01": 6900},
            stored_hits={("2025-07", 1): "6900", ("2025-07", 3): "6900"},
        )
        rc = hzmetrics.cmd_audit(self._args(all=True))
        self.assertEqual(rc, 0)

    # ------------------------------------------------------------------
    # Check I — sub-month import-coverage gaps (missing source-file runs).
    # ------------------------------------------------------------------

    def test_import_gap_flagged(self):
        # apache files cover 2025-07-01..05 and 09..12 — a 3-day hole at
        # 06/07/08.  I must report it.  cmsauth left empty (skipped).
        files = [f"delta-access-202507{d:02d}.log.gz" for d in
                 (1, 2, 3, 4, 5, 9, 10, 11, 12)]
        data = {(t, c): [] for t, c, _p, _r in hzmetrics._AUDIT_CHECKS}
        self._set_mock(data, import_files={"web": files})
        rc = hzmetrics.cmd_audit(self._args(all=True))
        self.assertEqual(rc, 1)
        emitted = self._emitted()
        self.assertIn("apache import gap: 2025-07-06..2025-07-08", emitted)

    def test_no_import_gap_when_contiguous(self):
        # Fully contiguous daily files → no gap finding.  (A single missing
        # day would also be below the 2-day floor.)
        files = [f"delta-access-202507{d:02d}.log.gz" for d in range(1, 13)]
        data = {(t, c): [] for t, c, _p, _r in hzmetrics._AUDIT_CHECKS}
        self._set_mock(data, import_files={"web": files})
        rc = hzmetrics.cmd_audit(self._args(all=True))
        self.assertEqual(rc, 0)

    # ------------------------------------------------------------------
    # CLI shape
    # ------------------------------------------------------------------

    def test_all_flag_uses_full_history(self):
        # --all probes MIN(datetime).  With our mock returning 2014-01,
        # the scope line should reflect 2014-01-01 (not the 24-month
        # rolling default).
        data = {(t, c): [] for t, c, _p, _r in hzmetrics._AUDIT_CHECKS}
        self._set_mock(data)
        rc = hzmetrics.cmd_audit(self._args(all=True))
        self.assertEqual(rc, 0)
        self.assertIn("2014-01-01", self._emitted())


if __name__ == "__main__":
    unittest.main()
