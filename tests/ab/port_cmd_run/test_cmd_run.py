"""Pure-Python tests for the three-mode cmd_run state machine.

Strategy: monkey-patch the heavy DB + filesystem helpers with recording
fakes that simulate just enough state to walk cmd_run through each mode.
Then assert that cmd_run picks the right tick handler, applies the right
decision-matrix branch, and writes the right transitions to state.

The actual import / analyze / summarize work is stubbed out — we're
testing the orchestrator, not the workers.  Workers are covered by their
own port_* tests.
"""
import sys, tempfile, unittest, re
from pathlib import Path
from unittest.mock import MagicMock
from datetime import date

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


class FakeState:
    """In-memory pipeline_state."""
    def __init__(self) -> None:
        self.values: dict = {}

    def read(self) -> dict:
        return dict(self.values)

    def update(self, **kwargs) -> None:
        self.values.update({k: str(v) for k, v in kwargs.items()})


class FakeDB:
    """Tiny DB simulator for the helpers cmd_run touches indirectly.

    Tests configure high-level state via attributes; SQL is loosely
    pattern-matched and dispatched to the model."""
    def __init__(self) -> None:
        self.base_table_rows: dict = {}      # (month_str, table) -> bool
        self.summary_period_rows: dict = {}  # (month_str, table, period) -> bool
        self.summary_user_period1_months: set = set()  # quick is_month_summarized check
        self.web_months: list = []           # ordered list of months in `web` for _backlog_months
        self.orphaned_stamp_months: set = set()  # months where month_has_orphaned_stamps → True
        self.incomplete_period_months: set = set()  # months returned by period_incomplete_months' GROUP BY

    _BASE_RE = re.compile(
        r"SELECT 1 FROM \S+\.(\w+)\s+WHERE datetime >=", re.IGNORECASE,
    )
    _SUM_PER_RE = re.compile(
        r"SELECT COUNT\(\*\) FROM \S+\.(\w+)\s+WHERE datetime = %s AND period = (?:%s|(\d+))",
        re.IGNORECASE,
    )
    _BACKLOG_RE = re.compile(
        r"SELECT DISTINCT DATE_FORMAT\(datetime, '%%Y-%%m'\) AS ym\s+FROM \S+\.web",
        re.IGNORECASE,
    )
    _ORPHAN_RE = re.compile(
        # Matches the two NOT-EXISTS probes inside month_has_orphaned_stamps:
        # the `web w` and `toolstart t` variants.  Both compare a base-table
        # sessionid stamp against websessions.id.
        r"SELECT 1 FROM \S+\.(?:web|toolstart) \w\s+WHERE \w\.datetime >=.*NOT EXISTS",
        re.IGNORECASE | re.DOTALL,
    )
    _INCOMPLETE_RE = re.compile(
        r"SELECT DATE_FORMAT\(datetime, '%%Y-%%m'\) AS ym\s+FROM \S+\.(\w+)\s+"
        r"GROUP BY ym HAVING COUNT\(DISTINCT period\) <",
        re.IGNORECASE,
    )

    def scalar(self, sql: str, params=None):
        m = self._SUM_PER_RE.search(sql)
        if m:
            table = m.group(1)
            dt = params[0]
            period = params[1] if len(params) > 1 else int(m.group(2))
            return 1 if self.summary_period_rows.get((dt[:7], table, period)) else 0
        if self._ORPHAN_RE.search(sql):
            start = params[0]
            return 1 if start[:7] in self.orphaned_stamp_months else None
        m = self._BASE_RE.search(sql)
        if m:
            table = m.group(1)
            start = params[0]
            return 1 if self.base_table_rows.get((start[:7], table)) else None
        raise AssertionError(f"unexpected scalar SQL: {sql!r}")

    def query(self, sql: str, params=None):
        if self._BACKLOG_RE.search(sql):
            # Return months that have web data but no summary_user_vals period=1
            # AND are before today_str (the params[0] cutoff).
            cutoff = params[0]  # 'YYYY-MM-01'
            return [(m,) for m in self.web_months
                    if m + "-01" < cutoff
                    and m not in self.summary_user_period1_months]
        if self._INCOMPLETE_RE.search(sql):
            # period_incomplete_months' GROUP BY — return the configured
            # incomplete months (the helper does the < today filtering).
            return [(m,) for m in sorted(self.incomplete_period_months)]
        # Other queries from read_state etc shouldn't fire — pipeline_state
        # is monkey-patched separately.
        raise AssertionError(f"unexpected query SQL: {sql!r}")

    def execute(self, sql: str, params=None):
        # DELETE / DDL: just succeed.  Tests don't assert on exact SQL.
        return 0


# ---------------------------------------------------------------------------
# Common test scaffolding
# ---------------------------------------------------------------------------

class CmdRunTestBase(unittest.TestCase):

    def setUp(self) -> None:
        import importlib, hzmetrics
        self.hz = importlib.reload(hzmetrics)
        # Filesystem
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.hz.SITE = "testsite"
        self.hz.APACHE_LOG_DIR = root / "httpd"
        self.hz.CMS_LOG_DIR    = root / "hubzero"
        self.hz.HTTPD_DAILY    = self.hz.APACHE_LOG_DIR / "daily"
        self.hz.HTTPD_HOLDING  = self.hz.APACHE_LOG_DIR / "daily.holding"
        self.hz.HZ_DAILY       = self.hz.CMS_LOG_DIR / "daily"
        self.hz.HZ_HOLDING     = self.hz.CMS_LOG_DIR / "daily.holding"
        self.hz.HTTPD_IMPORTED = self.hz.APACHE_LOG_DIR / "imported"
        self.hz.HZ_IMPORTED    = self.hz.CMS_LOG_DIR / "imported"
        self.root = root

        # DB
        self.fakedb = FakeDB()
        self.hz.mysql_scalar  = self.fakedb.scalar
        self.hz.mysql_query   = self.fakedb.query
        self.hz.mysql_exec    = self.fakedb.execute
        self.hz.db_credentials = lambda: ("localhost", "user", "pass", "metrics_db_x")

        # State (replace the DB-backed read/update with in-memory)
        self.state = FakeState()
        self.hz.read_state   = self.state.read
        self.hz.update_state = lambda **kw: self.state.update(**kw)

        # Lock: always succeed, no-op
        self.hz.acquire_lock = lambda: True
        self.hz.release_lock = lambda: None

        # Stub the heavy workers — record calls.  Default return is 0 so
        # callers that sum the result (e.g. _import_month → do_import_day
        # returning row counts) get a valid int.  Tests that need a
        # specific non-zero return value (e.g. crash-recovery: file
        # already imported → 0) override self.hz.do_import_day after
        # setUp.
        self.calls: list = []
        def record(name, returns=0):
            def f(*a, **kw):
                self.calls.append((name, a, kw))
                return returns
            return f
        self.hz.do_import_day  = record("do_import_day", returns=1)
        self.hz.do_analyze     = record("do_analyze")
        self.hz.do_summarize   = record("do_summarize")
        self.hz._reset_month_for_resummarize = record("_reset_month_for_resummarize")

        # Today: fix it so date.today() is deterministic.
        # cmd_run uses date.today().strftime / .isoformat / .day.
        self._patch_today(date(2026, 5, 19))

    def _patch_today(self, d: date) -> None:
        import hzmetrics
        class FakeDate:
            @staticmethod
            def today(): return d
            @staticmethod
            def fromisoformat(s): return date.fromisoformat(s)
        # Replace `date` in hzmetrics module
        hzmetrics.date = FakeDate

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _args(self, dry_run=False):
        a = MagicMock()
        a.dry_run = dry_run
        return a

    def _touch_source(self, month_str: str, day: str = "15") -> None:
        yyyymm = month_str.replace("-", "")
        p = self.root / "httpd/daily" / f"testsite-access.log-{yyyymm}{day}.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")

    def _called(self, fn_name: str) -> list:
        return [c for c in self.calls if c[0] == fn_name]


# ---------------------------------------------------------------------------
# Normal mode
# ---------------------------------------------------------------------------

class NormalModeTests(CmdRunTestBase):

    def test_normal_no_work_when_nothing_pending_and_summarized(self):
        # Empty filesystem, prev month already summarized → should still
        # run normal tick but it should be quick (no work).
        self.state.update(analyzed="2026-05-19")
        self.fakedb.summary_user_period1_months.add("2026-04")
        self.fakedb.summary_period_rows[("2026-04", "summary_user_vals", 1)] = True
        self.hz.cmd_run(self._args())
        # No imports, no summarize calls
        self.assertEqual(len(self._called("do_import_day")), 0)
        self.assertEqual(len(self._called("do_summarize")), 0)
        # Mode unchanged (still default normal)
        self.assertEqual(self.state.values.get("mode", "normal"), "normal")

    def test_normal_imports_current_pending(self):
        self._touch_source("2026-05", "18")
        self._touch_source("2026-05", "19")
        # prev month summarized, so we won't try to summarize it
        self.fakedb.summary_period_rows[("2026-04", "summary_user_vals", 1)] = True
        self.fakedb.summary_user_period1_months.add("2026-04")
        self.state.update(analyzed="2026-05-19")
        self.hz.cmd_run(self._args())
        # Both pending days imported
        self.assertEqual(len(self._called("do_import_day")), 2)

    def test_normal_summarizes_prev_when_complete(self):
        # No current pending; prev month fully imported but not summarized.
        # Mock is_month_fully_imported via touching last day in imported/
        last = self.hz.last_day_of_month("2026-04")
        p = self.root / "httpd/imported" / f"testsite-access.log-{last}.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
        self.state.update(analyzed="2026-05-19")
        # prev (2026-04) is_month_summarized → False (no summary rows)
        self.hz.cmd_run(self._args())
        # do_summarize called once, for 2026-04
        sum_calls = self._called("do_summarize")
        self.assertEqual(len(sum_calls), 1)
        self.assertEqual(sum_calls[0][1][0], "2026-04")
        # periods kwarg should be default (None) — full summarize
        self.assertNotIn("periods", sum_calls[0][2])

    def test_normal_current_month_analyze_skips_sessions(self):
        # Current-month daily analyze must pass sessions=False — otherwise
        # logfix-session runs daily and slices sessions at every tick
        # boundary.  Regression guard for the fix.
        self.hz.cmd_run(self._args())
        ana_calls = self._called("do_analyze")
        # First do_analyze call is for current month
        current_calls = [c for c in ana_calls if c[1][0] == "2026-05"]
        self.assertGreaterEqual(len(current_calls), 1,
                                "expected at least one do_analyze for current month")
        self.assertEqual(current_calls[0][2].get("sessions"), False,
                         "current-month analyze must pass sessions=False")

    def test_normal_prev_month_close_analyze_includes_sessions(self):
        # Month-close analyze on prev must NOT pass sessions=False — this
        # is the one run of logfix-session each month gets.
        last = self.hz.last_day_of_month("2026-04")
        p = self.root / "httpd/imported" / f"testsite-access.log-{last}.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
        self.state.update(analyzed="2026-05-19")
        self.hz.cmd_run(self._args())
        ana_calls = self._called("do_analyze")
        prev_calls = [c for c in ana_calls if c[1][0] == "2026-04"]
        self.assertGreaterEqual(len(prev_calls), 1,
                                "expected at least one do_analyze for prev month")
        # default sessions=True for month-close; assert no override to False
        self.assertNotEqual(prev_calls[0][2].get("sessions"), False,
                            "month-close analyze must not skip sessions")

    def test_prev_month_complete_via_next_month_data(self):
        # Last-day log file is NOT in imported/, but `web` has rows in
        # the next month (today_str = 2026-05).  is_month_complete must
        # return True via the data-driven signal and the prev-month
        # summarize must fire.
        self.fakedb.base_table_rows[("2026-05", "web")] = True
        self.state.update(analyzed="2026-05-19")
        self.hz.cmd_run(self._args())
        sum_calls = self._called("do_summarize")
        self.assertEqual(len(sum_calls), 1)
        self.assertEqual(sum_calls[0][1][0], "2026-04")

    def test_prev_month_defers_when_no_signal(self):
        # Neither the last-day file nor next-month data exists →
        # is_month_complete is False → summarize must not fire.
        # (Don't add anything to base_table_rows for 2026-05; don't
        # touch the imported/ file.)
        self.state.update(analyzed="2026-05-19")
        self.hz.cmd_run(self._args())
        self.assertEqual(len(self._called("do_summarize")), 0)


# ---------------------------------------------------------------------------
# Normal → catchup transition
# ---------------------------------------------------------------------------

class NormalToCatchupTests(CmdRunTestBase):

    def test_pending_log_in_old_month_triggers_catchup(self):
        # A 2022 log in the source dir → normal should flip to catchup.
        self._touch_source("2022-06", "15")
        # Mark today's prev month as already-summarized so normal would
        # otherwise do nothing.
        self.fakedb.summary_period_rows[("2026-04", "summary_user_vals", 1)] = True
        self.state.update(analyzed="2026-05-19")

        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values["mode"], "catchup")
        # catchup_started should be recorded
        self.assertEqual(self.state.values["catchup_started"], "2022-06")
        # And the catchup tick should have done a fresh import for 2022-06
        self.assertGreaterEqual(len(self._called("do_import_day")), 1)
        ana = self._called("do_analyze")
        sum_ = self._called("do_summarize")
        self.assertEqual(ana[0][1][0], "2022-06")
        self.assertEqual(sum_[0][1][0], "2022-06")
        self.assertEqual(sum_[0][2].get("periods"), self.hz._CATCHUP_PERIODS)


# ---------------------------------------------------------------------------
# Catchup mode — decision matrix routing
# ---------------------------------------------------------------------------

class CatchupRoutingTests(CmdRunTestBase):

    def setUp(self) -> None:
        super().setUp()
        self.state.update(mode="catchup", catchup_started="2022-01")

    def test_source_only_imports(self):
        self._touch_source("2022-06", "15")
        self.hz.cmd_run(self._args())
        # Fresh-import branch: no derived-state reset (nothing prior to reset).
        self.assertEqual(len(self._called("_reset_month_for_resummarize")), 0)
        self.assertGreaterEqual(len(self._called("do_import_day")), 1)
        sum_calls = self._called("do_summarize")
        self.assertEqual(sum_calls[0][2].get("periods"), self.hz._CATCHUP_PERIODS)

    def test_source_and_data_imports_additively_and_resets_derived(self):
        # 2023-12 case: source ✓, web rows present, no summary.
        # Provenance principle: base rows must NOT be deleted by datetime.
        # Branch is: additive import + reset derived state + analyze + summarize.
        self._touch_source("2023-12", "15")
        self.fakedb.base_table_rows[("2023-12", "web")] = True
        self.hz.cmd_run(self._args())
        # Import runs (additive — INSERT IGNORE on imported_sources keeps it safe).
        self.assertGreaterEqual(len(self._called("do_import_day")), 1)
        # Derived state is reset exactly once for this month.
        reset_calls = self._called("_reset_month_for_resummarize")
        self.assertEqual(len(reset_calls), 1)
        self.assertEqual(reset_calls[0][1][0], "2023-12")
        # Analyze + summarize run for the target month.
        self.assertEqual(self._called("do_summarize")[0][1][0], "2023-12")

    def test_data_only_resummarizes(self):
        # 2024-07 case: no source anywhere, web rows present, no summary
        self.fakedb.base_table_rows[("2024-07", "web")] = True
        self.fakedb.web_months = ["2024-07"]  # _backlog_months DB query picks this up
        self.hz.cmd_run(self._args())
        self.assertEqual(len(self._called("do_import_day")), 0)  # no imports
        # Clean data-only branch: no reset (data is consistent, just unsummarized).
        self.assertEqual(len(self._called("_reset_month_for_resummarize")), 0)
        # Did analyze + resummarize (period=1 only)
        ana = self._called("do_analyze")
        sum_ = self._called("do_summarize")
        self.assertEqual(ana[0][1][0], "2024-07")
        self.assertEqual(sum_[0][1][0], "2024-07")
        self.assertEqual(sum_[0][2].get("periods"), self.hz._CATCHUP_PERIODS)

    def test_catchup_analyze_runs_sessions(self):
        # Catchup processes complete historical months; logfix-session
        # must run (sessions defaults to True, no override to False).
        self.fakedb.base_table_rows[("2024-07", "web")] = True
        self.fakedb.web_months = ["2024-07"]
        self.hz.cmd_run(self._args())
        ana = self._called("do_analyze")
        self.assertEqual(ana[0][1][0], "2024-07")
        self.assertNotEqual(ana[0][2].get("sessions"), False,
                            "catchup must not pass sessions=False — complete-month "
                            "logfix-session is required for correct websessions")

    def test_dirty_marker_triggers_reset_then_resummarize(self):
        # Month is fully summarized but operator marked it dirty after
        # bulk DELETE on web — orchestrator must reset derived tables
        # and resummarize, ignoring the fully-summ signal.
        self.fakedb.base_table_rows[("2025-05", "web")] = True
        self.state.update(dirty_months="2025-05")
        # Mark it fully summarized so the "fully_summ → skip" path would
        # normally win — the dirty marker must override.
        for table in self.hz._SUMMARY_VALS_TABLES:
            for period in self.hz._PERIOD_CODES_FOR_FULL_CHECK:
                self.fakedb.summary_period_rows[("2025-05", table, period)] = True
        self.fakedb.summary_user_period1_months.add("2025-05")
        self.hz.cmd_run(self._args())
        self.assertEqual(len(self._called("_reset_month_for_resummarize")), 1)
        self.assertEqual(self._called("_reset_month_for_resummarize")[0][1][0], "2025-05")
        # Reset path runs analyze + summarize after the wipe
        self.assertEqual(self._called("do_analyze")[0][1][0], "2025-05")
        self.assertEqual(self._called("do_summarize")[0][1][0], "2025-05")
        # Dirty marker is auto-cleared after a successful pass
        self.assertEqual(self.state.values.get("dirty_months", ""), "")

    def test_orphaned_stamps_triggers_reset(self):
        # No dirty marker, but the consistency check finds web.sessionid
        # values pointing at deleted websessions rows — same reset path.
        self.fakedb.base_table_rows[("2025-06", "web")] = True
        self.fakedb.web_months = ["2025-06"]
        self.fakedb.orphaned_stamp_months.add("2025-06")
        self.hz.cmd_run(self._args())
        self.assertEqual(len(self._called("_reset_month_for_resummarize")), 1)
        self.assertEqual(self._called("_reset_month_for_resummarize")[0][1][0], "2025-06")

    # --- rebuild cascade origin -------------------------------------------

    def test_import_branch_records_cascade_origin(self):
        # A fresh import (source ✓, data ✗) changes base data, so it must
        # record rebuild_cascade_from — the rebuild walk will start here.
        self._touch_source("2022-06", "15")
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values.get("rebuild_cascade_from"), "2022-06")

    def test_source_and_data_records_cascade_origin(self):
        # Source ✓ + data ✓ → additive import + reset-derived.  The import
        # adds new base rows, so cascade origin must still be recorded.
        self._touch_source("2023-12", "15")
        self.fakedb.base_table_rows[("2023-12", "web")] = True
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values.get("rebuild_cascade_from"), "2023-12")

    def test_resummarize_only_does_not_record_cascade_origin(self):
        # Data-only resummarize (no source) changes NO base rows, so it
        # must NOT set a cascade origin — there's nothing to invalidate
        # downstream; the completeness sweep will handle this month.
        self.fakedb.base_table_rows[("2024-07", "web")] = True
        self.fakedb.web_months = ["2024-07"]
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values.get("rebuild_cascade_from", ""), "",
                         "resummarize-only must not trigger an invalidation cascade")

    def test_source_and_data_with_zero_new_rows_skips_cascade_marker(self):
        # Crash-recovery scenario: source file is back in daily/ but
        # its `imported_sources` row is still present (atomic helper
        # committed the row but crashed before move-to-imported/).  The
        # atomic helper re-runs idempotently, inserts ZERO new rows,
        # do_import_day returns 0.  Cascade marker must NOT be lowered
        # — no new base data means no downstream invalidation.
        self._touch_source("2022-06", "15")
        self.fakedb.base_table_rows[("2022-06", "web")] = True
        # Override the default stub to simulate the no-op import.
        def import_day_noop(*a, **kw):
            self.calls.append(("do_import_day", a, kw))
            return 0
        self.hz.do_import_day = import_day_noop
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values.get("rebuild_cascade_from", ""), "",
                         "zero-insert crash-recovery must not lower the "
                         "cascade marker — the rebuild walk it triggers "
                         "would be wasted work across years of months")

    def test_cascade_origin_keeps_oldest_imported_month(self):
        # _record_rebuild_cascade_from only lowers, never raises.
        st = {"rebuild_cascade_from": "2023-05"}
        self.hz._record_rebuild_cascade_from("2024-01", st, dry_run=True)
        self.assertEqual(st["rebuild_cascade_from"], "2023-05")
        self.hz._record_rebuild_cascade_from("2022-09", st, dry_run=True)
        self.assertEqual(st["rebuild_cascade_from"], "2022-09")


# ---------------------------------------------------------------------------
# Catchup → rebuild transition
# ---------------------------------------------------------------------------

class CatchupToRebuildTests(CmdRunTestBase):

    def test_empty_backlog_no_import_skips_walk(self):
        # No source logs, no web months → backlog empty, nothing imported.
        # With no invalidation cascade, the rebuild cursor is set PAST
        # prev_month so the forward walk is skipped; the completeness
        # sweep alone runs.  (today=2026-05-19 → next_month=2026-06.)
        self.state.update(mode="catchup", catchup_started="2022-01")
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values["mode"], "rebuild")
        self.assertEqual(self.state.values["rebuild_cursor"], "2026-06",
                         "no new data imported → no cascade → walk skipped")

    def test_empty_backlog_with_cascade_origin_walks_from_it(self):
        # If an earlier catchup tick recorded an imported month, the
        # rebuild cursor starts there (the invalidation cascade origin),
        # not past prev.
        self.state.update(mode="catchup", catchup_started="2022-01",
                          rebuild_cascade_from="2023-01")
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values["mode"], "rebuild")
        self.assertEqual(self.state.values["rebuild_cursor"], "2023-01")


# ---------------------------------------------------------------------------
# Rebuild mode
# ---------------------------------------------------------------------------

class RebuildModeTests(CmdRunTestBase):

    def test_rebuild_processes_cursor_month_with_all_periods(self):
        self.state.update(mode="rebuild", rebuild_cursor="2022-06")
        self.hz.cmd_run(self._args())
        sum_calls = self._called("do_summarize")
        self.assertEqual(len(sum_calls), 1)
        self.assertEqual(sum_calls[0][1][0], "2022-06")
        # No periods kwarg → defaults to all 6
        self.assertNotIn("periods", sum_calls[0][2])
        # cursor advanced
        self.assertEqual(self.state.values["rebuild_cursor"], "2022-07")

    def test_rebuild_analyze_runs_sessions(self):
        # Rebuild walks historical complete months; logfix-session must
        # be part of analyze (sessions defaults to True).
        self.state.update(mode="rebuild", rebuild_cursor="2022-06")
        self.hz.cmd_run(self._args())
        ana = self._called("do_analyze")
        self.assertEqual(ana[0][1][0], "2022-06")
        self.assertNotEqual(ana[0][2].get("sessions"), False,
                            "rebuild must not pass sessions=False — historical "
                            "months are complete and need logfix-session run")

    def test_rebuild_advances_through_year_boundary(self):
        self.state.update(mode="rebuild", rebuild_cursor="2022-12")
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values["rebuild_cursor"], "2023-01")

    def test_rebuild_past_prev_transitions_to_normal(self):
        # cursor already past prev_month (2026-04), nothing incomplete
        self.state.update(mode="rebuild", rebuild_cursor="2026-05")
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values["mode"], "normal")

    # --- completeness sweep ------------------------------------------------

    def test_rebuild_sweep_fills_incomplete_month_after_walk(self):
        # Cursor walk done (past prev), but an older month is incomplete
        # (e.g. the pre-2022 cmsauth backlog: period-1 only).  Rebuild must
        # resummarize it with all periods and STAY in rebuild — not flip to
        # normal with the gap unfilled.
        self.state.update(mode="rebuild", rebuild_cursor="2026-05")
        self.fakedb.incomplete_period_months = {"2021-12"}
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values["mode"], "rebuild",
                         "must not transition to normal while an incomplete "
                         "month remains")
        sums = self._called("do_summarize")
        self.assertEqual(len(sums), 1)
        self.assertEqual(sums[0][1][0], "2021-12")
        # Full periods (no periods= restriction) — the whole point.
        self.assertNotIn("periods", sums[0][2])

    def test_rebuild_sweep_processes_oldest_incomplete_first(self):
        self.state.update(mode="rebuild", rebuild_cursor="2026-05")
        self.fakedb.incomplete_period_months = {"2021-03", "2020-10", "2021-11"}
        self.hz.cmd_run(self._args())
        sums = self._called("do_summarize")
        self.assertEqual(sums[0][1][0], "2020-10",
                         "sweep should take the oldest incomplete month first")

    def test_rebuild_sweep_clean_transitions_to_normal(self):
        # Walk done AND nothing incomplete → finally normal.
        self.state.update(mode="rebuild", rebuild_cursor="2026-05")
        self.fakedb.incomplete_period_months = set()
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values["mode"], "normal")
        self.assertEqual(self._called("do_summarize"), [])

    def test_rebuild_complete_clears_cascade_origin(self):
        # When rebuild finishes, the cascade marker is cleared so the next
        # catchup cycle starts from a clean "nothing imported yet" state.
        self.state.update(mode="rebuild", rebuild_cursor="2026-05",
                          rebuild_cascade_from="2023-01")
        self.fakedb.incomplete_period_months = set()
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values["mode"], "normal")
        self.assertEqual(self.state.values.get("rebuild_cascade_from", ""), "")

    def test_rebuild_walk_takes_priority_over_sweep(self):
        # While the forward walk still has months to do, the sweep does not
        # run — the walk's cursor month is processed first.
        self.state.update(mode="rebuild", rebuild_cursor="2024-06")
        self.fakedb.incomplete_period_months = {"2021-12"}
        self.hz.cmd_run(self._args())
        sums = self._called("do_summarize")
        self.assertEqual(len(sums), 1)
        self.assertEqual(sums[0][1][0], "2024-06",
                         "forward walk month runs before any sweep")
        self.assertEqual(self.state.values["rebuild_cursor"], "2024-07")


# ---------------------------------------------------------------------------
# catchup_started watermark
# ---------------------------------------------------------------------------

class CatchupStartedWatermarkTests(CmdRunTestBase):

    def test_catchup_started_lowered_for_older_backlog_month(self):
        # catchup_started is pinned at 2022-01 from a prior run, but an
        # older month (2020-10) is now pending — catchup_started must drop
        # to it so the rebuild cascade reaches back that far.
        self.state.update(mode="catchup", catchup_started="2022-01")
        self._touch_source("2020-10", day="30")   # auth/access source for an older month
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values["catchup_started"], "2020-10")

    def test_catchup_started_not_raised_for_newer_month(self):
        # If the only backlog is newer than the stored watermark, it must
        # NOT move forward — the cascade start must stay at the earliest.
        self.state.update(mode="catchup", catchup_started="2022-01")
        self._touch_source("2024-06")
        self.hz.cmd_run(self._args())
        self.assertEqual(self.state.values["catchup_started"], "2022-01")


if __name__ == "__main__":
    unittest.main(verbosity=2)
