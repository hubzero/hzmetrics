"""Pure-Python tests for Phase E: months_in_range, next_month,
cmd_rebuild_summaries, and the cmd_status extension."""
import sys, tempfile, unittest
from pathlib import Path
from unittest.mock import MagicMock
from datetime import date

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# months_in_range / next_month — pure functions, no DB / FS
# ---------------------------------------------------------------------------

class MonthRangeTests(unittest.TestCase):
    def setUp(self):
        import importlib, hzmetrics
        self.hz = importlib.reload(hzmetrics)

    def test_next_month_simple(self):
        self.assertEqual(self.hz.next_month("2025-06"), "2025-07")

    def test_next_month_december_rolls(self):
        self.assertEqual(self.hz.next_month("2022-12"), "2023-01")

    def test_range_inclusive_both_ends(self):
        self.assertEqual(self.hz.months_in_range("2024-01", "2024-03"),
                         ["2024-01", "2024-02", "2024-03"])

    def test_range_same_month(self):
        self.assertEqual(self.hz.months_in_range("2026-05", "2026-05"),
                         ["2026-05"])

    def test_range_across_year_boundary(self):
        self.assertEqual(self.hz.months_in_range("2022-11", "2023-02"),
                         ["2022-11", "2022-12", "2023-01", "2023-02"])

    def test_range_inverted_returns_empty(self):
        self.assertEqual(self.hz.months_in_range("2026-05", "2026-04"), [])


# ---------------------------------------------------------------------------
# cmd_rebuild_summaries — orchestrates do_summarize over a range
# ---------------------------------------------------------------------------

class RebuildSummariesCmdTests(unittest.TestCase):

    def setUp(self):
        import importlib, hzmetrics
        self.hz = importlib.reload(hzmetrics)
        # Pin today so --through defaulting is deterministic
        class FakeDate:
            @staticmethod
            def today(): return date(2026, 5, 19)
            @staticmethod
            def fromisoformat(s): return date.fromisoformat(s)
        self.hz.date = FakeDate

        self.summarize_calls: list = []
        def fake_summarize(month, dry_run=False, *, periods=None):
            self.summarize_calls.append((month, dry_run, periods))
        self.hz.do_summarize = fake_summarize

        # Replace state I/O with no-ops so we don't touch a real DB
        self.hz.read_state = lambda: {}
        self.hz.update_state = lambda **kw: None

    def _args(self, since, through=None, periods=None, dry_run=False):
        a = MagicMock()
        a.since   = since
        a.through = through
        a.periods = periods
        a.dry_run = dry_run
        return a

    def test_single_month(self):
        self.hz.cmd_rebuild_summaries(self._args(since="2024-06", through="2024-06"))
        self.assertEqual(self.summarize_calls,
                         [("2024-06", False, None)])

    def test_range_of_months(self):
        self.hz.cmd_rebuild_summaries(self._args(since="2024-01", through="2024-03"))
        months = [c[0] for c in self.summarize_calls]
        self.assertEqual(months, ["2024-01", "2024-02", "2024-03"])

    def test_through_defaults_to_prev_month(self):
        # today is 2026-05-19, so prev_month = 2026-04
        self.hz.cmd_rebuild_summaries(self._args(since="2026-03"))
        months = [c[0] for c in self.summarize_calls]
        self.assertEqual(months, ["2026-03", "2026-04"])

    def test_periods_csv_parses(self):
        self.hz.cmd_rebuild_summaries(
            self._args(since="2024-06", through="2024-06", periods="12,13,14")
        )
        self.assertEqual(self.summarize_calls[0][2], (12, 13, 14))

    def test_periods_with_whitespace(self):
        self.hz.cmd_rebuild_summaries(
            self._args(since="2024-06", through="2024-06", periods="1, 14")
        )
        self.assertEqual(self.summarize_calls[0][2], (1, 14))

    def test_periods_none_means_all(self):
        self.hz.cmd_rebuild_summaries(
            self._args(since="2024-06", through="2024-06")
        )
        self.assertIsNone(self.summarize_calls[0][2])

    def test_invalid_period_value_exits(self):
        with self.assertRaises(SystemExit):
            self.hz.cmd_rebuild_summaries(
                self._args(since="2024-06", through="2024-06", periods="99")
            )

    def test_non_integer_periods_exits(self):
        with self.assertRaises(SystemExit):
            self.hz.cmd_rebuild_summaries(
                self._args(since="2024-06", through="2024-06", periods="one,two")
            )

    def test_since_after_through_exits(self):
        with self.assertRaises(SystemExit):
            self.hz.cmd_rebuild_summaries(
                self._args(since="2024-08", through="2024-06")
            )

    def test_dry_run_propagates(self):
        self.hz.cmd_rebuild_summaries(
            self._args(since="2024-06", through="2024-06", dry_run=True)
        )
        self.assertEqual(self.summarize_calls[0][1], True)

    def test_does_not_change_pipeline_state_mode(self):
        # Track any update_state calls.  Should be none.
        update_calls = []
        self.hz.update_state = lambda **kw: update_calls.append(kw)
        self.hz.cmd_rebuild_summaries(self._args(since="2024-06", through="2024-06"))
        # No state mutation
        self.assertEqual(update_calls, [])


# ---------------------------------------------------------------------------
# cmd_status — the new orchestrator-state section
# ---------------------------------------------------------------------------

class StatusOutputTests(unittest.TestCase):

    def setUp(self):
        import importlib, hzmetrics
        self.hz = importlib.reload(hzmetrics)
        # Capture log output
        import logging
        self.records: list = []
        class CaptureHandler(logging.Handler):
            def emit(_, record): self.records.append(record.getMessage())
        # Replace handlers
        for h in list(self.hz.log.handlers):
            self.hz.log.removeHandler(h)
        self.hz.log.addHandler(CaptureHandler())
        self.hz.log.setLevel(logging.DEBUG)

        # Make discovery + dated_files harmless
        self.hz.enumerate_log_sources = lambda kind: []
        self.hz.dated_files = lambda d, p: []
        self.hz.HTTPD_IMPORTED = Path("/nonexistent/httpd")
        self.hz.HZ_IMPORTED    = Path("/nonexistent/hubzero")
        self.hz.HZMETRICS_CONF = Path("/nonexistent/conf")

        # Pin today
        class FakeDate:
            @staticmethod
            def today(): return date(2026, 5, 19)
            @staticmethod
            def fromisoformat(s): return date.fromisoformat(s)
        self.hz.date = FakeDate

    def _set_state(self, state: dict):
        self.hz.read_state = lambda: state

    def _output(self) -> str:
        return "\n".join(self.records)

    def test_normal_mode_default(self):
        self._set_state({})
        self.hz.cmd_status(MagicMock())
        out = self._output()
        self.assertIn("mode             : normal", out)
        self.assertIn("last analyzed    : (never)", out)
        self.assertNotIn("catchup_started", out)
        self.assertNotIn("rebuild_cursor", out)

    def test_catchup_mode_shows_catchup_started(self):
        self._set_state({"mode": "catchup", "catchup_started": "2022-01"})
        self.hz.cmd_status(MagicMock())
        out = self._output()
        self.assertIn("mode             : catchup", out)
        self.assertIn("catchup_started  : 2022-01", out)
        # In catchup, we don't print rebuild_cursor even if set
        self.assertNotIn("rebuild_cursor", out)

    def test_rebuild_mode_shows_progress(self):
        self._set_state({"mode": "rebuild",
                         "catchup_started": "2022-01",
                         "rebuild_cursor":  "2022-06"})
        self.hz.cmd_status(MagicMock())
        out = self._output()
        self.assertIn("mode             : rebuild", out)
        self.assertIn("rebuild_cursor   : 2022-06", out)
        # Target prev_month is 2026-04; range 2022-06..2026-04 = 47 months
        self.assertIn("47 month(s) remaining through 2026-04", out)

    def test_rebuild_cursor_past_prev_month(self):
        self._set_state({"mode": "rebuild", "rebuild_cursor": "2026-05"})
        self.hz.cmd_status(MagicMock())
        out = self._output()
        self.assertIn("past prev_month", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
