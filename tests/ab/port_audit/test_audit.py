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
        hzmetrics.db_credentials = self._orig_db
        hzmetrics.period_incomplete_months = self._orig_pi
        for h in list(hzmetrics.log.handlers):
            hzmetrics.log.removeHandler(h)
        for h in self._orig_handlers:
            hzmetrics.log.addHandler(h)

    def _emitted(self) -> str:
        return "\n".join(msg for _, msg in self._log_records)

    def _set_mock(self, data):
        """data: dict mapping (table, column_predicate_substring) → list of
        (ym, total, missing) tuples.  Any unmatched query (e.g.
        structural cross-consistency checks) returns []."""

        def mq(sql, params=None):
            if "SELECT MIN(d) FROM" in sql:
                # --all probe — return an old start
                return [(dt(2014, 1, 1, 0, 0, 0),)]
            if "GROUP BY ym\n            HAVING total" in sql or "HAVING total >=" in sql:
                # The audit per-check query.  Find which table + which
                # missing-column predicate by scanning _AUDIT_CHECKS.
                for table, column, pred, _r in hzmetrics._AUDIT_CHECKS:
                    if f".{table} " in sql and pred in sql:
                        return list(data.get((table, column), []))
                return []
            # Structural / cross-consistency queries default to empty.
            return []

        hzmetrics.mysql_query = mq

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
