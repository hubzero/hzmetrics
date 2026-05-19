"""Pure-Python tests for the catchup decision helpers:
  - month_has_source(m)
  - month_has_data(m)
  - is_month_summarized(m)        (already existed; covered for regression)
  - is_month_fully_summarized(m)  (new in Phase C)

month_has_source touches the filesystem (enumerate_log_sources) — we
build a tmpdir with fixture logs and re-point HZ paths at it.

The DB-touching helpers (month_has_data, is_month_summarized,
is_month_fully_summarized) are exercised with a monkey-patched mysql_*
layer.  The fake backs SELECTs with a tiny in-memory model so we can
construct each row of the catchup decision matrix and confirm the
return value, without needing a live DB.
"""
import sys, tempfile, unittest, re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

TEST_SITE = "testsite"


# ---------------------------------------------------------------------------
# Fake DB for the DB-touching helpers
# ---------------------------------------------------------------------------

class FakeDB:
    """In-memory model just rich enough for the decision helpers.

    Rather than parsing SQL, we let the test set high-level state:
      .base_table_rows[(month, table)] = True   → SELECT 1 FROM <t> WHERE datetime IN month → 1
      .summary_period_rows[(month, table, period)] = True
                                                → SELECT COUNT FROM <t> WHERE datetime=YYYY-MM-00 AND period=N → 1
    """
    def __init__(self) -> None:
        self.base_table_rows: dict = {}      # (month_str, table) -> bool
        self.summary_period_rows: dict = {}  # (month_str, table, period) -> bool

    # Matches: SELECT 1 FROM <db>.<table> WHERE datetime >= 'YYYY-MM-01' AND datetime < ... LIMIT 1
    _BASE_RE = re.compile(
        r"SELECT 1 FROM \S+\.(\w+)\s+WHERE datetime >=", re.IGNORECASE,
    )
    # Matches: SELECT COUNT(*) FROM <db>.<table> WHERE datetime = %s AND period = N
    _SUM_RE = re.compile(
        r"SELECT COUNT\(\*\) FROM \S+\.(\w+)\s+WHERE datetime = %s AND period = (?:%s|(\d+))",
        re.IGNORECASE,
    )

    def scalar(self, sql: str, params=None):
        # Period check (is_month_fully_summarized + is_month_summarized)
        m = self._SUM_RE.search(sql)
        if m:
            table = m.group(1)
            # period may be a placeholder (%s) or a literal in the SQL
            dt = params[0]
            period = params[1] if len(params) > 1 else int(m.group(2))
            month_str = dt[:7]  # 'YYYY-MM-00' → 'YYYY-MM'
            return 1 if self.summary_period_rows.get((month_str, table, period)) else 0
        # Base data probe (month_has_data)
        m = self._BASE_RE.search(sql)
        if m:
            table = m.group(1)
            start = params[0]
            month_str = start[:7]
            return 1 if self.base_table_rows.get((month_str, table)) else None
        raise AssertionError(f"unexpected scalar SQL: {sql!r}")


def install_fake_db(hz, fake: FakeDB) -> None:
    hz.mysql_scalar  = fake.scalar
    hz.mysql_query   = lambda sql, params=None: []  # never called by decisions
    hz.mysql_exec    = lambda sql, params=None: 0
    hz.db_credentials = lambda: ("localhost", "user", "pass", "metrics_db_x")


# ---------------------------------------------------------------------------
# month_has_source — filesystem-driven
# ---------------------------------------------------------------------------

class MonthHasSourceTests(unittest.TestCase):

    def setUp(self) -> None:
        import importlib, hzmetrics
        self.hz = importlib.reload(hzmetrics)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.hz.SITE           = TEST_SITE
        self.hz.APACHE_LOG_DIR = root / "httpd"
        self.hz.CMS_LOG_DIR    = root / "hubzero"
        self.hz.HTTPD_DAILY    = self.hz.APACHE_LOG_DIR / "daily"
        self.hz.HTTPD_HOLDING  = self.hz.APACHE_LOG_DIR / "daily.holding"
        self.hz.HZ_DAILY       = self.hz.CMS_LOG_DIR / "daily"
        self.hz.HZ_HOLDING     = self.hz.CMS_LOG_DIR / "daily.holding"
        self.root = root

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _touch(self, rel: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")

    def test_no_source_anywhere(self):
        self.assertFalse(self.hz.month_has_source("2026-05"))

    def test_source_in_daily(self):
        self._touch(f"httpd/daily/{TEST_SITE}-access.log-20260515.gz")
        self.assertTrue(self.hz.month_has_source("2026-05"))
        self.assertFalse(self.hz.month_has_source("2026-04"))  # different month

    def test_source_in_year_subdir(self):
        self._touch(f"httpd/daily/2022/{TEST_SITE}-access-20220615.log.gz")
        self.assertTrue(self.hz.month_has_source("2022-06"))
        self.assertFalse(self.hz.month_has_source("2022-07"))

    def test_source_in_holding(self):
        self._touch(f"httpd/daily.holding/{TEST_SITE}-access.log-20250730.gz")
        self.assertTrue(self.hz.month_has_source("2025-07"))


# ---------------------------------------------------------------------------
# month_has_data — DB probe of base tables
# ---------------------------------------------------------------------------

class MonthHasDataTests(unittest.TestCase):

    def setUp(self) -> None:
        import importlib, hzmetrics
        self.hz = importlib.reload(hzmetrics)
        self.fake = FakeDB()
        install_fake_db(self.hz, self.fake)

    def test_empty_db_returns_false(self):
        self.assertFalse(self.hz.month_has_data("2024-07"))

    def test_web_only(self):
        self.fake.base_table_rows[("2024-07", "web")] = True
        self.assertTrue(self.hz.month_has_data("2024-07"))

    def test_userlogin_only(self):
        # Even when web is empty, userlogin rows count as "has data"
        # (this is the 2025-08 TZ-bleed sliver case).
        self.fake.base_table_rows[("2025-08", "userlogin")] = True
        self.assertTrue(self.hz.month_has_data("2025-08"))

    def test_webhits_only(self):
        self.fake.base_table_rows[("2024-03", "webhits")] = True
        self.assertTrue(self.hz.month_has_data("2024-03"))

    def test_websessions_only(self):
        self.fake.base_table_rows[("2024-03", "websessions")] = True
        self.assertTrue(self.hz.month_has_data("2024-03"))

    def test_different_month_returns_false(self):
        self.fake.base_table_rows[("2024-07", "web")] = True
        self.assertFalse(self.hz.month_has_data("2024-08"))

    def test_december_rolls_over_to_next_year(self):
        # Boundary check: month_has_data must compute end = next-year-Jan-01
        self.fake.base_table_rows[("2024-12", "web")] = True
        self.assertTrue(self.hz.month_has_data("2024-12"))
        self.assertFalse(self.hz.month_has_data("2025-01"))


# ---------------------------------------------------------------------------
# is_month_summarized + is_month_fully_summarized
# ---------------------------------------------------------------------------

class IsMonthSummarizedTests(unittest.TestCase):

    def setUp(self) -> None:
        import importlib, hzmetrics
        self.hz = importlib.reload(hzmetrics)
        self.fake = FakeDB()
        install_fake_db(self.hz, self.fake)

    def _mark_full(self, month_str: str) -> None:
        """Mark month as having every (table, period) pair populated."""
        for tbl in self.hz._SUMMARY_VALS_TABLES:
            for p in self.hz._PERIOD_CODES_FOR_FULL_CHECK:
                self.fake.summary_period_rows[(month_str, tbl, p)] = True

    # --- is_month_summarized (existing, bool) -------------------------

    def test_summarized_false_when_no_rows(self):
        self.assertFalse(self.hz.is_month_summarized("2024-07"))

    def test_summarized_true_when_period1_present(self):
        self.fake.summary_period_rows[("2024-07", "summary_user_vals", 1)] = True
        self.assertTrue(self.hz.is_month_summarized("2024-07"))

    def test_summarized_only_checks_user_vals(self):
        # Other tables don't count: this used to be a foot-gun.
        self.fake.summary_period_rows[("2024-07", "summary_misc_vals", 1)] = True
        self.assertFalse(self.hz.is_month_summarized("2024-07"))

    # --- is_month_fully_summarized ------------------------------------

    def test_fully_summarized_true_when_all_populated(self):
        self._mark_full("2024-07")
        self.assertTrue(self.hz.is_month_fully_summarized("2024-07"))

    def test_fully_summarized_false_when_none(self):
        self.assertFalse(self.hz.is_month_fully_summarized("2024-07"))

    def test_fully_summarized_false_when_only_period1(self):
        # The "partial" case we saw in production for 2025-07 — some
        # rows exist (so is_month_summarized → True) but the strict
        # check correctly says False.
        for tbl in self.hz._SUMMARY_VALS_TABLES:
            self.fake.summary_period_rows[("2025-07", tbl, 1)] = True
        self.assertTrue(self.hz.is_month_summarized("2025-07"))
        self.assertFalse(self.hz.is_month_fully_summarized("2025-07"))

    def test_fully_summarized_false_when_missing_one_period(self):
        self._mark_full("2024-07")
        # Drop period=14 (all-time) for summary_simusage_vals — common
        # partial state if summarize dies during the all-time pass.
        del self.fake.summary_period_rows[("2024-07", "summary_simusage_vals", 14)]
        self.assertFalse(self.hz.is_month_fully_summarized("2024-07"))

    def test_fully_summarized_false_when_missing_whole_table(self):
        self._mark_full("2024-07")
        # Drop all of summary_misc_vals
        for p in self.hz._PERIOD_CODES_FOR_FULL_CHECK:
            del self.fake.summary_period_rows[("2024-07", "summary_misc_vals", p)]
        self.assertFalse(self.hz.is_month_fully_summarized("2024-07"))


# ---------------------------------------------------------------------------
# Decision matrix end-to-end — exercise each row from the design doc
# ---------------------------------------------------------------------------

class DecisionMatrixTests(unittest.TestCase):
    """Combine all four helpers to walk every row of the catchup matrix:

      source | data | summary  | action
      -------+------+----------+--------------------------
        ✓    |  ✗   |  none    | import + analyze + summarize
        ✓    |  ✓   |  none    | wipe + reimport
        ✓    |  ✓   |  partial | wipe + reimport
        ✗    |  ✓   |  none    | resummarize only
        ✗    |  ✓   |  partial | resummarize only
        ✗    |  ✗   |   —      | skip (true gap)
        ✓    |  ✓   |  full    | skip (done)

    Tests assert the helper-return tuple for each row.  The actual
    routing (which 'action' the orchestrator picks) lands in Phase D.
    """

    def setUp(self) -> None:
        import importlib, hzmetrics
        self.hz = importlib.reload(hzmetrics)
        self.fake = FakeDB()
        install_fake_db(self.hz, self.fake)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.hz.SITE          = TEST_SITE
        self.hz.HTTPD_DAILY   = root / "httpd/daily"
        self.hz.HTTPD_HOLDING = root / "httpd/daily.holding"
        self.hz.HZ_DAILY      = root / "hubzero/daily"
        self.hz.HZ_HOLDING    = root / "hubzero/daily.holding"
        self.root = root

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _add_source(self, month_str: str) -> None:
        yyyymm = month_str.replace("-", "")
        p = self.root / "httpd/daily" / f"{TEST_SITE}-access.log-{yyyymm}15.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")

    def _add_data(self, month_str: str) -> None:
        self.fake.base_table_rows[(month_str, "web")] = True

    def _mark_full(self, month_str: str) -> None:
        for tbl in self.hz._SUMMARY_VALS_TABLES:
            for p in self.hz._PERIOD_CODES_FOR_FULL_CHECK:
                self.fake.summary_period_rows[(month_str, tbl, p)] = True

    def _mark_period1(self, month_str: str) -> None:
        for tbl in self.hz._SUMMARY_VALS_TABLES:
            self.fake.summary_period_rows[(month_str, tbl, 1)] = True

    def _probe(self, m: str):
        return (
            self.hz.month_has_source(m),
            self.hz.month_has_data(m),
            self.hz.is_month_summarized(m),
            self.hz.is_month_fully_summarized(m),
        )

    # Row 1: source ✓, data ✗, summary none
    def test_fresh_backlog_month(self):
        self._add_source("2022-06")
        self.assertEqual(self._probe("2022-06"),
                         (True, False, False, False))

    # Row 2: source ✓, data ✓, summary none
    def test_partial_2023_12_case(self):
        # 2023-12 in production: web rows present, no summary.
        self._add_source("2023-12")
        self._add_data("2023-12")
        self.assertEqual(self._probe("2023-12"),
                         (True, True, False, False))

    # Row 3: source ✓, data ✓, summary partial  (2025-07-style)
    def test_partial_summary_with_source(self):
        self._add_source("2025-07")
        self._add_data("2025-07")
        self._mark_period1("2025-07")
        self.assertEqual(self._probe("2025-07"),
                         (True, True, True, False))

    # Row 4: source ✗, data ✓, summary none/partial  (2024-access-gap case)
    def test_no_source_data_partial_summary(self):
        # No source files anywhere, but DB has rows (2024 access months).
        self._add_data("2024-07")
        self._mark_period1("2024-07")
        self.assertEqual(self._probe("2024-07"),
                         (False, True, True, False))

    # Row 5: source ✗, data ✗, summary —
    def test_true_gap(self):
        self.assertEqual(self._probe("2024-13"[:7]),  # bogus month — proves no false positives
                         (False, False, False, False))

    # Row 6: source ✓, data ✓, summary full — already done
    def test_already_done(self):
        self._add_source("2024-09")
        self._add_data("2024-09")
        self._mark_full("2024-09")
        self.assertEqual(self._probe("2024-09"),
                         (True, True, True, True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
