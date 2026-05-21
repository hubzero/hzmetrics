"""Direct unit tests for `is_month_complete`.

A month is "complete" — i.e., safe for normal-mode summarize to fire —
when at least one of two structural signals is true:

  (1) The month's last calendar day's log file is present in imported/
      — what `is_month_fully_imported(month)` checks.
  (2) The pipeline has imported at least one row dated in the *next*
      month — what `month_has_data(next_month(month))` checks.

The second signal is the safety net for the case where logrotate
skipped the very last day of the month.  The original legacy fallback
("if we're > 5 days into the next calendar month, summarize anyway")
was calendar-based and flaky for manual ticks / tz drift / operators
catching up after vacation; the data-driven signal is replaced.

These tests pin the function's behavior so a future refactor can't
silently regress either signal.
"""
import sys, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _hz():
    import importlib, hzmetrics
    return importlib.reload(hzmetrics)


class IsMonthCompleteTests(unittest.TestCase):
    """Each test fully controls the inputs to `is_month_complete`:
       - `is_month_fully_imported` is monkeypatched to return a fixed bool
       - `month_has_data` is monkeypatched to return True for a fixed set
         of months
    The two together let us assert exactly which arm of the OR fires."""

    def setUp(self) -> None:
        self.hz = _hz()
        # Per-test state — assertions below mutate these.
        self.fully_imported_months: set = set()
        self.data_months: set = set()
        self.hz.is_month_fully_imported = lambda m: m in self.fully_imported_months
        self.hz.month_has_data = lambda m: m in self.data_months

    # --- one signal at a time ----------------------------------------

    def test_complete_via_fully_imported_signal(self):
        self.fully_imported_months.add("2026-04")
        self.assertTrue(self.hz.is_month_complete("2026-04"))

    def test_complete_via_next_month_data_signal(self):
        # Last-day file NOT in imported/, but `web` has next-month data.
        self.data_months.add("2026-05")
        self.assertTrue(self.hz.is_month_complete("2026-04"))

    def test_complete_when_both_signals_present(self):
        self.fully_imported_months.add("2026-04")
        self.data_months.add("2026-05")
        self.assertTrue(self.hz.is_month_complete("2026-04"))

    def test_incomplete_when_neither_signal(self):
        # Nothing in fully_imported_months, nothing in data_months.
        self.assertFalse(self.hz.is_month_complete("2026-04"))

    # --- next_month math is correctly wired in ----------------------

    def test_uses_next_month_not_same_month_for_data_check(self):
        # If month_has_data is True for the SAME month rather than the
        # NEXT month, the function should not return True (the signal
        # must be next-month data, not own-month data).
        self.data_months.add("2026-04")    # own month, NOT next
        self.assertFalse(self.hz.is_month_complete("2026-04"))

    def test_next_month_wraps_year(self):
        # December → January year wrap
        self.data_months.add("2026-01")
        self.assertTrue(self.hz.is_month_complete("2025-12"))

    def test_next_month_does_not_match_unrelated_future_month(self):
        # Data in 2026-07 is NOT the next month after 2026-04 (which is
        # 2026-05).  Function must not return True off an unrelated
        # later month's data.
        self.data_months.add("2026-07")
        self.assertFalse(self.hz.is_month_complete("2026-04"))


class CalendarFallbackRemovedTests(unittest.TestCase):
    """The previous implementation used `date.today().day > 5` as a
    fallback to force summarize even when the last-day file was
    missing.  Confirm that fallback is gone — both behaviorally (no
    calendar-day check leaks into is_month_complete) and structurally
    (the `_do_normal_tick` source no longer mentions `days_in`)."""

    def setUp(self) -> None:
        self.hz = _hz()

    def test_no_calendar_day_dependency(self):
        # Even with no signals and an arbitrary today, the function
        # must return False.  Patch date.today() to confirm.
        from datetime import date as real_date
        class FakeDate:
            @staticmethod
            def today(): return real_date(2026, 5, 31)  # late in next month
            @staticmethod
            def fromisoformat(s): return real_date.fromisoformat(s)
        self.hz.date = FakeDate
        self.hz.is_month_fully_imported = lambda m: False
        self.hz.month_has_data           = lambda m: False
        # Even with today = 2026-05-31, prev month 2026-04 should be
        # NOT complete — no data signal, no file signal.
        self.assertFalse(self.hz.is_month_complete("2026-04"))

    def test_source_no_longer_uses_days_in_heuristic(self):
        # Grep guard: the legacy `days_in = date.today().day` /
        # `days_in > 5` fallback must not survive in cmd_run flow.
        src = (REPO / "hzmetrics.py").read_text()
        self.assertNotIn("days_in = date.today().day", src,
                         "_do_normal_tick still uses calendar-day fallback — "
                         "should use is_month_complete() instead")
        self.assertNotIn("days_in > 5", src,
                         "_do_normal_tick still uses calendar-day fallback")


if __name__ == "__main__":
    unittest.main(verbosity=2)
