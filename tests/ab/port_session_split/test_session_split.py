"""Verify `_do_usage_metrics_stage` honors the `sessions` parameter.

Context: logfix-session running daily during the still-incomplete
current month was creating a fresh sessionid boundary at every tick
(because the `WHERE sessionid IS NULL OR sessionid = '0'` predicate
hid already-stamped rows from each subsequent run, so a session
genuinely spanning two ticks got split).  The fix:

  - `do_analyze(..., sessions=False)` — for current-month daily
    analyze.  Runs all the row-level enrichment (DNS, fill-domain,
    clean-bots on web, fill-ipcountry on web/toolstart) but skips
    logfix-session and the websessions-bound steps.

  - `do_analyze(..., sessions=True)` (the default) — for complete
    months (catchup, rebuild, month-close).  Runs everything,
    including logfix-session once per month.

These tests stub every do_* worker and assert exactly which ones get
called for each value of `sessions`."""
import sys, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _hz():
    import importlib, hzmetrics
    return importlib.reload(hzmetrics)


class UsageMetricsSessionsParameterTests(unittest.TestCase):

    # The full list of do_* worker functions that _do_usage_metrics_stage
    # may invoke.  Each is stubbed to record its calls; the test then
    # asserts which ones fired.
    WORKERS = [
        "do_import_hub_data",
        "do_middleware_wall",
        "do_middleware_cpu",
        "do_resolve_dns",
        "do_fill_domain",
        "do_logfix_session",         # session-bound
        "do_clean_bots",             # called for both web and websessions
        "do_fill_user_info",
        "do_fill_ipcountry",         # called for web/websessions/toolstart
    ]

    # Subset whose calls must be GATED by sessions=True.
    SESSION_BOUND_CALLS = {
        # function name → which positional/keyword arg pinpoints the
        # session-bound call (table = "websessions") so we can
        # distinguish it from the web/toolstart calls.
        "do_logfix_session": lambda a, kw: True,            # always session-bound
        "do_clean_bots":     lambda a, kw: a and a[0] == "websessions",
        "do_fill_ipcountry": lambda a, kw: len(a) >= 2 and a[1] == "websessions",
    }

    def setUp(self) -> None:
        self.hz = _hz()
        self.calls: list = []
        def stub(name):
            def f(*a, **kw):
                self.calls.append((name, a, kw))
            return f
        for w in self.WORKERS:
            setattr(self.hz, w, stub(w))

    def _calls(self, name: str) -> list:
        return [c for c in self.calls if c[0] == name]

    def _session_bound_calls(self) -> list:
        out = []
        for name, predicate in self.SESSION_BOUND_CALLS.items():
            for c in self._calls(name):
                if predicate(c[1], c[2]):
                    out.append(c)
        return out

    # --- sessions=False (current month) ----------------------------

    def test_sessions_false_skips_logfix_session(self):
        self.hz._do_usage_metrics_stage("2026-05", dry_run=False, sessions=False)
        self.assertEqual(len(self._calls("do_logfix_session")), 0,
                         "sessions=False must not invoke logfix-session")

    def test_sessions_false_skips_websessions_clean_bots(self):
        self.hz._do_usage_metrics_stage("2026-05", dry_run=False, sessions=False)
        ws_clean = [c for c in self._calls("do_clean_bots")
                    if c[1] and c[1][0] == "websessions"]
        self.assertEqual(len(ws_clean), 0,
                         "sessions=False must not invoke clean-bots on websessions")

    def test_sessions_false_skips_websessions_fill_ipcountry(self):
        self.hz._do_usage_metrics_stage("2026-05", dry_run=False, sessions=False)
        ws_fill = [c for c in self._calls("do_fill_ipcountry")
                   if len(c[1]) >= 2 and c[1][1] == "websessions"]
        self.assertEqual(len(ws_fill), 0,
                         "sessions=False must not invoke fill-ipcountry on websessions")

    def test_sessions_false_still_runs_row_level_enrichment(self):
        # The whole point of sessions=False is to enable daily
        # row-level enrichment without the session-bound work.  These
        # must still fire.
        self.hz._do_usage_metrics_stage("2026-05", dry_run=False, sessions=False)
        self.assertGreaterEqual(len(self._calls("do_import_hub_data")), 1)
        self.assertGreaterEqual(len(self._calls("do_resolve_dns")),     1)
        self.assertGreaterEqual(len(self._calls("do_fill_domain")),     1)
        # clean-bots on web (not websessions) should run
        web_clean = [c for c in self._calls("do_clean_bots")
                     if c[1] and c[1][0] == "web"]
        self.assertGreaterEqual(len(web_clean), 1)
        # fill-ipcountry on web + toolstart should run, not websessions
        non_ws_fill = [c for c in self._calls("do_fill_ipcountry")
                       if len(c[1]) >= 2 and c[1][1] in ("web", "toolstart")]
        self.assertGreaterEqual(len(non_ws_fill), 2)

    # --- sessions=True (default; complete-month) -------------------

    def test_sessions_true_runs_logfix_session(self):
        self.hz._do_usage_metrics_stage("2026-04", dry_run=False, sessions=True)
        self.assertEqual(len(self._calls("do_logfix_session")), 1,
                         "sessions=True must invoke logfix-session once")

    def test_sessions_default_is_true(self):
        # Verify the default value of `sessions`.  Same call without
        # the keyword should match sessions=True behavior.
        self.hz._do_usage_metrics_stage("2026-04", dry_run=False)
        self.assertEqual(len(self._calls("do_logfix_session")), 1)

    def test_sessions_true_runs_websessions_steps(self):
        self.hz._do_usage_metrics_stage("2026-04", dry_run=False, sessions=True)
        ws_clean = [c for c in self._calls("do_clean_bots")
                    if c[1] and c[1][0] == "websessions"]
        ws_fill = [c for c in self._calls("do_fill_ipcountry")
                   if len(c[1]) >= 2 and c[1][1] == "websessions"]
        self.assertEqual(len(ws_clean), 1)
        self.assertEqual(len(ws_fill),  1)


class DoAnalyzeForwardsSessionsTests(unittest.TestCase):
    """`do_analyze` is the public surface that `_do_normal_tick`,
    `_do_catchup_tick`, and `_do_rebuild_tick` all call.  Verify it
    correctly forwards its `sessions` kwarg to `_do_usage_metrics_stage`."""

    def setUp(self) -> None:
        self.hz = _hz()
        self.usage_calls: list = []
        # Stub _do_usage_metrics_stage; assert the kwargs it gets.
        def stub_usage(month_str, dry_run, *, sessions=True):
            self.usage_calls.append({"month_str": month_str,
                                     "sessions": sessions})
        self.hz._do_usage_metrics_stage = stub_usage
        # Stub the tool-metrics stage too — irrelevant to this test.
        self.hz._do_tool_metrics_stage = lambda m, d: None

    def test_default_sessions_is_true(self):
        self.hz.do_analyze("2026-04")
        self.assertEqual(self.usage_calls[-1]["sessions"], True)

    def test_explicit_sessions_false_propagates(self):
        self.hz.do_analyze("2026-05", sessions=False)
        self.assertEqual(self.usage_calls[-1]["sessions"], False)

    def test_explicit_sessions_true_propagates(self):
        self.hz.do_analyze("2026-04", sessions=True)
        self.assertEqual(self.usage_calls[-1]["sessions"], True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
