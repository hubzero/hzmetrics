"""Pin per-stage window-boundary semantics.

Two layers of regression guard:

  1. PeriodDatesTests — `period_dates(ym, code)` returns the right
     `(start, stop)` bounds for each period code at a representative
     set of calendar positions, including year-boundary wraparound
     (Dec→Jan) and fiscal-year inflection (Sep→Oct).

  2. BoundaryInclusionTests — replays the in-DB inclusion test that
     each pipeline stage performs against `web.datetime`, by
     reconstructing the WHERE-predicate semantics in Python.  Pins
     the documented per-stage convention, including the one-second
     sliver at `YYYY-MM-01 00:00:00` that the summarize stage's
     `> AND <` window drops while andmore's `>= AND <` window keeps.

If either layer drifts (a bound moves by a day, an operator flips
from `>` to `>=`), the affected test fails immediately — no need to
wait for a downstream count to look wrong before the cause is
visible.
"""
import sys, unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _hz():
    import importlib, hzmetrics
    return importlib.reload(hzmetrics)


# ---------------------------------------------------------------------------
# Layer 1: period_dates()
# ---------------------------------------------------------------------------

class PeriodDatesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.hz = _hz()

    # PERIOD_MONTH (1) — the month itself --------------------------------

    def test_month_september(self):
        s, e = self.hz.period_dates("2025-09", self.hz.PERIOD_MONTH)
        self.assertEqual((s, e), ("2025-09-01", "2025-10-01"))

    def test_month_december_wraps_year(self):
        s, e = self.hz.period_dates("2025-12", self.hz.PERIOD_MONTH)
        self.assertEqual((s, e), ("2025-12-01", "2026-01-01"))

    def test_month_january(self):
        s, e = self.hz.period_dates("2026-01", self.hz.PERIOD_MONTH)
        self.assertEqual((s, e), ("2026-01-01", "2026-02-01"))

    # PERIOD_CAL_YEAR (0) — Jan 1 through end of <month> -----------------

    def test_calendar_year_september(self):
        s, e = self.hz.period_dates("2025-09", self.hz.PERIOD_CAL_YEAR)
        self.assertEqual((s, e), ("2025-01-01", "2025-10-01"))

    def test_calendar_year_january(self):
        # Jan: window is Jan-1 → Feb-1 (one month, same as PERIOD_MONTH).
        s, e = self.hz.period_dates("2025-01", self.hz.PERIOD_CAL_YEAR)
        self.assertEqual((s, e), ("2025-01-01", "2025-02-01"))

    # PERIOD_QUARTER (3) — calendar quarter through end of <month> --------

    def test_quarter_september(self):
        # Jul-Aug-Sep
        s, e = self.hz.period_dates("2025-09", self.hz.PERIOD_QUARTER)
        self.assertEqual((s, e), ("2025-07-01", "2025-10-01"))

    def test_quarter_february(self):
        # Jan-Feb (still in Q1)
        s, e = self.hz.period_dates("2025-02", self.hz.PERIOD_QUARTER)
        self.assertEqual((s, e), ("2025-01-01", "2025-03-01"))

    # PERIOD_ROLLING_12 (12) — 12 months ending after <month> ------------

    def test_rolling_12_september(self):
        # Oct 2024 .. Sep 2025 inclusive
        s, e = self.hz.period_dates("2025-09", self.hz.PERIOD_ROLLING_12)
        self.assertEqual((s, e), ("2024-10-01", "2025-10-01"))

    def test_rolling_12_january_wraps(self):
        # Feb 2024 .. Jan 2025 inclusive
        s, e = self.hz.period_dates("2025-01", self.hz.PERIOD_ROLLING_12)
        self.assertEqual((s, e), ("2024-02-01", "2025-02-01"))

    # PERIOD_FISCAL_YR (13) — Oct..Sep through end of <month> ------------

    def test_fiscal_year_september_is_prev_oct_anchor(self):
        # Sept is the last month of fiscal year that started Oct prev.
        s, e = self.hz.period_dates("2025-09", self.hz.PERIOD_FISCAL_YR)
        self.assertEqual((s, e), ("2024-10-01", "2025-10-01"))

    def test_fiscal_year_october_starts_new_fy(self):
        # Oct is fiscal-year start: window is just Oct (Oct-1 → Nov-1).
        s, e = self.hz.period_dates("2025-10", self.hz.PERIOD_FISCAL_YR)
        self.assertEqual((s, e), ("2025-10-01", "2025-11-01"))

    def test_fiscal_year_january_anchor_is_prev_oct(self):
        # Jan is mid-fiscal-year: anchored at prev-Oct.
        s, e = self.hz.period_dates("2025-01", self.hz.PERIOD_FISCAL_YR)
        self.assertEqual((s, e), ("2024-10-01", "2025-02-01"))

    # PERIOD_ALL_TIME (14) — 1995-01-01 through end of <month> ----------

    def test_all_time(self):
        s, e = self.hz.period_dates("2025-09", self.hz.PERIOD_ALL_TIME)
        self.assertEqual((s, e), ("1995-01-01", "2025-10-01"))


# ---------------------------------------------------------------------------
# Layer 2: per-stage inclusion semantics
# ---------------------------------------------------------------------------

# Each stage's WHERE clause uses a known operator pair.  Greppable as
# call sites in hzmetrics.py at the line numbers below; mirrored here
# so a flip from `>` to `>=` (or vice versa) gets caught by these
# tests directly without waiting for a downstream count to look wrong.
STAGE_OPS = {
    # do_andmore_usage (4493): `datetime >= start AND datetime < stop`
    "andmore":   (">=", "<"),
    # _summary_build_* and the bulk of summarize SQL (60xx): `>` / `<`
    "summarize": (">",  "<"),
    # do_clean_bots (3164/3170): `>` / `<=`
    "cleanbots": (">",  "<="),
    # logfix-session / resolve-dns / fill-domain / fill-ipcountry: `>=` / `<`
    "halfopen":  (">=", "<"),
}


def _to_dt(s: str) -> datetime:
    # Accept 'YYYY-MM-DD' (MariaDB implicit-casts to midnight) or full.
    if " " not in s:
        s = s + " 00:00:00"
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _in_window(ts: str, start: str, stop: str, ops) -> bool:
    """Replay the SQL predicate in Python."""
    t = _to_dt(ts)
    b_start = _to_dt(start)
    b_stop  = _to_dt(stop)
    op_start, op_stop = ops
    ok_start = (t > b_start) if op_start == ">" else (t >= b_start)
    ok_stop  = (t < b_stop)  if op_stop  == "<" else (t <= b_stop)
    return ok_start and ok_stop


class BoundaryInclusionTests(unittest.TestCase):
    """For a curated grid of timestamps around the Aug/Sep/Oct 2025
    month boundaries, assert which month's PERIOD_MONTH window each
    timestamp falls into under each stage's operator convention.

    The 'DROPPED' expectations on the exact `YYYY-MM-01 00:00:00`
    ticks for the summarize stage are the documented bug:
    `> start AND < stop` excludes the start tick.  The neighboring
    August window also excludes it (`stop` of Aug is the same tick).
    """

    def setUp(self) -> None:
        self.hz = _hz()
        self.aug_start, self.aug_stop = self.hz.period_dates("2025-08", self.hz.PERIOD_MONTH)
        self.sep_start, self.sep_stop = self.hz.period_dates("2025-09", self.hz.PERIOD_MONTH)
        self.oct_start, self.oct_stop = self.hz.period_dates("2025-10", self.hz.PERIOD_MONTH)
        # Sanity: month bounds align as expected
        self.assertEqual((self.aug_start, self.aug_stop), ("2025-08-01", "2025-09-01"))
        self.assertEqual((self.sep_start, self.sep_stop), ("2025-09-01", "2025-10-01"))
        self.assertEqual((self.oct_start, self.oct_stop), ("2025-10-01", "2025-11-01"))

    def _membership(self, ts: str, stage: str) -> str:
        """Return 'aug' / 'sep' / 'oct' / 'DROPPED' for this ts under
        stage's operators."""
        ops = STAGE_OPS[stage]
        hits_aug = _in_window(ts, self.aug_start, self.aug_stop, ops)
        hits_sep = _in_window(ts, self.sep_start, self.sep_stop, ops)
        hits_oct = _in_window(ts, self.oct_start, self.oct_stop, ops)
        hits = [name for name, h in (("aug", hits_aug),
                                     ("sep", hits_sep),
                                     ("oct", hits_oct)) if h]
        if len(hits) == 1:
            return hits[0]
        if len(hits) == 0:
            return "DROPPED"
        # An exact-stop tick can land in both halves under `<=` operator
        # (clean-bots).  Surface that explicitly.
        return "+".join(hits)

    # --- exact-tick boundary rows: the bug surface --------------------

    def test_aug_sep_tick_andmore_counts_september(self):
        # 2025-09-01 00:00:00 — exact start of September.
        # andmore (>=, <): start tick included → September.
        self.assertEqual(self._membership("2025-09-01 00:00:00", "andmore"), "sep")

    def test_aug_sep_tick_summarize_drops_it(self):
        # Same tick under summarize (>, <): start tick excluded from
        # September AND excluded from August (since Aug's stop is the
        # same tick).  Row vanishes from both summaries.
        self.assertEqual(self._membership("2025-09-01 00:00:00", "summarize"), "DROPPED")

    def test_aug_sep_tick_cleanbots_counts_august(self):
        # clean-bots (>, <=): exact stop tick of August is included
        # in August.  September's start tick is excluded from Sept.
        self.assertEqual(self._membership("2025-09-01 00:00:00", "cleanbots"), "aug")

    def test_sep_oct_tick_andmore_counts_october(self):
        self.assertEqual(self._membership("2025-10-01 00:00:00", "andmore"), "oct")

    def test_sep_oct_tick_summarize_drops_it(self):
        # Same DROPPED behavior at the Sep→Oct boundary.
        self.assertEqual(self._membership("2025-10-01 00:00:00", "summarize"), "DROPPED")

    # --- one second on either side of the boundary -------------------

    def test_one_second_before_sep_is_august_everywhere(self):
        ts = "2025-08-31 23:59:59"
        self.assertEqual(self._membership(ts, "andmore"),   "aug")
        self.assertEqual(self._membership(ts, "summarize"), "aug")
        self.assertEqual(self._membership(ts, "cleanbots"), "aug")

    def test_one_second_after_sep_start_is_september_everywhere(self):
        ts = "2025-09-01 00:00:01"
        self.assertEqual(self._membership(ts, "andmore"),   "sep")
        self.assertEqual(self._membership(ts, "summarize"), "sep")
        self.assertEqual(self._membership(ts, "cleanbots"), "sep")

    def test_last_second_of_september_is_september(self):
        ts = "2025-09-30 23:59:59"
        self.assertEqual(self._membership(ts, "andmore"),   "sep")
        self.assertEqual(self._membership(ts, "summarize"), "sep")
        self.assertEqual(self._membership(ts, "cleanbots"), "sep")

    def test_first_second_after_september_is_october(self):
        ts = "2025-10-01 00:00:01"
        self.assertEqual(self._membership(ts, "andmore"),   "oct")
        self.assertEqual(self._membership(ts, "summarize"), "oct")
        self.assertEqual(self._membership(ts, "cleanbots"), "oct")

    # --- mid-month rows (sanity: nothing weird here) -----------------

    def test_mid_september_is_september(self):
        for ts in ("2025-09-01 00:00:01",
                   "2025-09-15 12:34:56",
                   "2025-09-30 23:59:59"):
            for stage in ("andmore", "summarize", "cleanbots"):
                self.assertEqual(self._membership(ts, stage), "sep",
                                 f"{ts} under {stage}")

    def test_mid_august_is_august(self):
        for ts in ("2025-08-01 00:00:01",
                   "2025-08-15 12:34:56",
                   "2025-08-31 23:59:59"):
            for stage in ("andmore", "summarize", "cleanbots"):
                self.assertEqual(self._membership(ts, stage), "aug",
                                 f"{ts} under {stage}")


# ---------------------------------------------------------------------------
# Layer 3: structural — verify the SQL operators in the source haven't drifted
# ---------------------------------------------------------------------------

class StageOperatorSourceTests(unittest.TestCase):
    """Grep the source for the per-stage WHERE-clause shapes.  If
    someone edits the SQL and the operator changes, the test fails
    even if the boundary-inclusion logic above keeps matching the new
    convention — they've changed the contract and need to update
    BoundaryInclusionTests + the docs accordingly."""

    def setUp(self) -> None:
        self.src = (REPO / "hzmetrics.py").read_text()

    def test_andmore_uses_gte_lt(self):
        # do_andmore_usage's COUNT(DISTINCT ...) query
        self.assertIn(
            "AND datetime >= %s AND datetime < %s",
            self.src,
            "andmore-usage SQL changed shape — update STAGE_OPS and roadmap.md",
        )

    def test_summarize_uses_gt_lt(self):
        # Strict open-open is the legacy convention; many call sites.
        # Match a representative form from _summary_build_* helpers.
        self.assertIn(
            "AND ws.datetime > %s AND ws.datetime < %s",
            self.src,
            "summarize SQL changed shape — update STAGE_OPS and roadmap.md",
        )

    def test_cleanbots_uses_gt_lte(self):
        # do_clean_bots was refactored to a two-step SELECT-id + DELETE-by-PK
        # form (commit fa0adc5) to bound the InnoDB lock-table footprint on
        # tight buffer pools.  The half-open `> start AND <= end` window
        # convention moves to the SELECT side of the pair.  Verify both
        # the domain= and host LIKE branches still use that boundary.
        self.assertIn(
            "datetime > %s AND datetime <= %s AND domain = %s",
            self.src,
            "clean-bots domain-filter window changed shape — update STAGE_OPS and roadmap.md",
        )
        self.assertIn(
            "datetime > %s AND datetime <= %s AND host LIKE %s",
            self.src,
            "clean-bots host-filter window changed shape — update STAGE_OPS and roadmap.md",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
