"""Pure-Python tests for DB-backed pipeline_state.

Uses an in-memory dict in place of the real mysql_* helpers so the test
runs without a live DB.  The fake DB layer is "good enough" for verifying
state-management semantics:

  - CREATE TABLE IF NOT EXISTS is a no-op once the dict exists.
  - SELECT k, v FROM pipeline_state           → dict items.
  - SELECT COUNT(*) FROM pipeline_state       → len(dict).
  - INSERT … VALUES … ON DUPLICATE KEY UPDATE → dict.update from parsed values.

Anything else the test asks of mysql_exec raises AssertionError so we
never accidentally let unmocked SQL through.
"""
import os, sys, tempfile, unittest, re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


class FakeDB:
    """Minimal in-memory mysql replacement for read_state/update_state tests."""

    def __init__(self) -> None:
        self.table: dict[str, str] = {}
        self.exec_log: list[str] = []

    # --- the three helpers hzmetrics state code touches -------------

    def query(self, sql: str, params=None):
        self.exec_log.append(sql)
        if "SELECT k, v FROM" in sql and "pipeline_state" in sql:
            return list(self.table.items())
        raise AssertionError(f"unexpected SELECT: {sql!r}")

    def scalar(self, sql: str, params=None):
        self.exec_log.append(sql)
        if "SELECT COUNT(*) FROM" in sql and "pipeline_state" in sql:
            return len(self.table)
        raise AssertionError(f"unexpected scalar SELECT: {sql!r}")

    def execute(self, sql: str, params=None):
        self.exec_log.append(sql)
        s = " ".join(sql.split())  # squash whitespace
        if "CREATE TABLE IF NOT EXISTS" in s and "pipeline_state" in s:
            return 0
        m = re.search(r"INSERT INTO\s+\S+\.pipeline_state\s*\(k,\s*v\)\s*VALUES\s+(.+?)\s*ON DUPLICATE KEY UPDATE",
                      s, re.IGNORECASE)
        if m and params is not None:
            # We don't need to parse the VALUES clause — pymysql binds the
            # %s placeholders in order, so params is the flat key/value list.
            assert len(params) % 2 == 0, f"odd param count: {params!r}"
            for i in range(0, len(params), 2):
                self.table[str(params[i])] = str(params[i + 1])
            return 0
        raise AssertionError(f"unexpected exec: {sql!r}")


def install_fake_db(hz, fake: FakeDB) -> None:
    """Monkey-patch the four hzmetrics DB helpers + db_credentials for one test."""
    hz.mysql_query   = fake.query
    hz.mysql_scalar  = fake.scalar
    hz.mysql_exec    = fake.execute
    hz.db_credentials = lambda: ("localhost", "user", "pass", "metrics_db_x")


class StateTests(unittest.TestCase):

    def setUp(self) -> None:
        import importlib, hzmetrics
        # Fresh module per test so the _state_bootstrapped latch resets.
        self.hz = importlib.reload(hzmetrics)
        self.fake = FakeDB()
        install_fake_db(self.hz, self.fake)
        # Point STATE_FILE at a tempfile we control.
        self.tmp = tempfile.TemporaryDirectory()
        self.state_file = Path(self.tmp.name) / "hzmetrics.state"
        self.hz.STATE_FILE = self.state_file

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ------------------------------------------------------------------
    # empty / round-trip
    # ------------------------------------------------------------------

    def test_read_empty_returns_empty_dict(self):
        self.assertEqual(self.hz.read_state(), {})

    def test_round_trip_single_key(self):
        self.hz.update_state(analyzed="2026-05-19")
        self.assertEqual(self.hz.read_state(), {"analyzed": "2026-05-19"})

    def test_round_trip_multi_key(self):
        self.hz.update_state(analyzed="2026-05-19", mode="catchup",
                             catchup_started="2022-01")
        self.assertEqual(self.hz.read_state(), {
            "analyzed":        "2026-05-19",
            "mode":            "catchup",
            "catchup_started": "2022-01",
        })

    def test_update_is_upsert(self):
        self.hz.update_state(analyzed="2026-05-18")
        self.hz.update_state(analyzed="2026-05-19", mode="normal")
        self.assertEqual(self.hz.read_state(), {
            "analyzed": "2026-05-19",
            "mode":     "normal",
        })

    def test_update_with_no_kwargs_is_noop(self):
        self.hz.update_state()
        # Should not have issued any SQL beyond the existence-check.
        self.assertEqual(self.fake.table, {})

    def test_update_stringifies_values(self):
        # int / Path / arbitrary objects: stored as their str() form.
        self.hz.update_state(retries=3, path=Path("/var/run/hzmetrics"))
        st = self.hz.read_state()
        self.assertEqual(st["retries"], "3")
        self.assertEqual(st["path"],    "/var/run/hzmetrics")

    # ------------------------------------------------------------------
    # multi-key atomicity — single SQL statement, in order
    # ------------------------------------------------------------------

    def test_multi_key_is_single_statement(self):
        before = len(self.fake.exec_log)
        self.hz.update_state(a="1", b="2", c="3")
        after = len(self.fake.exec_log)
        # exactly two SQL ops: the CREATE TABLE IF NOT EXISTS, then the INSERT.
        self.assertEqual(after - before, 2)
        last = self.fake.exec_log[-1]
        self.assertIn("INSERT INTO", last)
        # Three (%s, %s) pairs in the VALUES clause.
        self.assertEqual(last.count("(%s, %s)"), 3)

    # ------------------------------------------------------------------
    # file → DB bootstrap (the upgrade path)
    # ------------------------------------------------------------------

    def test_bootstrap_imports_legacy_file(self):
        # Operator upgraded but pipeline_state is empty; old file has values.
        self.state_file.write_text("analyzed=2026-04-15\nmode=normal\n")
        st = self.hz.read_state()
        self.assertEqual(st, {"analyzed": "2026-04-15", "mode": "normal"})

    def test_bootstrap_skips_when_table_not_empty(self):
        # DB already has state — file is ignored even if present.
        self.fake.table = {"analyzed": "2026-05-19"}
        self.state_file.write_text("analyzed=1999-12-31\nmode=catchup\n")
        st = self.hz.read_state()
        self.assertEqual(st, {"analyzed": "2026-05-19"})

    def test_bootstrap_skips_when_file_absent(self):
        # Fresh install: empty table, no file → empty result, no error.
        self.assertFalse(self.state_file.exists())
        self.assertEqual(self.hz.read_state(), {})

    def test_bootstrap_ignores_blank_and_malformed_lines(self):
        # The parser splits on "=" only, with no escape / comment syntax —
        # mirrors the legacy file format.  Blanks and lines lacking "=" are
        # skipped; lines whose key (left of first "=") is empty are skipped.
        self.state_file.write_text(
            "\n"
            "nope-no-separator-here\n"
            "analyzed=2026-05-19\n"
            "=missing-key\n"
            "\n"
        )
        st = self.hz.read_state()
        self.assertEqual(st, {"analyzed": "2026-05-19"})

    def test_bootstrap_runs_only_once_per_process(self):
        # First read imports.  Subsequent reads don't re-touch the file
        # even if it changes — the latch prevents repeated work.
        self.state_file.write_text("analyzed=2026-05-19\n")
        self.assertEqual(self.hz.read_state(), {"analyzed": "2026-05-19"})

        # Mutate file: a second read should NOT pick up the new value.
        self.state_file.write_text("analyzed=2099-01-01\n")
        self.assertEqual(self.hz.read_state(), {"analyzed": "2026-05-19"})

    def test_bootstrap_handles_unreadable_file(self):
        # File exists but permission-denied: bootstrap silently skips.
        self.state_file.write_text("analyzed=2026-05-19\n")
        os.chmod(self.state_file, 0o000)
        try:
            self.assertEqual(self.hz.read_state(), {})
        finally:
            os.chmod(self.state_file, 0o600)

    # ------------------------------------------------------------------
    # dirty_months: comma-separated list, deduped + sorted on write,
    # auto-cleared as the orchestrator processes each month.
    # ------------------------------------------------------------------

    def test_dirty_empty_by_default(self):
        self.assertEqual(self.hz.get_dirty_months(), set())

    def test_add_dirty_persists_sorted_csv(self):
        self.hz.add_dirty_months(["2025-05", "2024-09", "2025-05"])
        self.assertEqual(self.fake.table["dirty_months"], "2024-09,2025-05")
        self.assertEqual(self.hz.get_dirty_months(), {"2024-09", "2025-05"})

    def test_add_dirty_is_union(self):
        self.hz.add_dirty_months(["2025-05"])
        self.hz.add_dirty_months(["2025-06", "2025-05"])
        self.assertEqual(self.hz.get_dirty_months(), {"2025-05", "2025-06"})

    def test_add_dirty_no_op_when_already_present(self):
        self.hz.add_dirty_months(["2025-05"])
        inserts_before = sum(1 for s in self.fake.exec_log if "INSERT INTO" in s)
        self.hz.add_dirty_months(["2025-05"])  # no new months
        inserts_after = sum(1 for s in self.fake.exec_log if "INSERT INTO" in s)
        # No second INSERT — same-set early-out (read_state SELECTs are fine)
        self.assertEqual(inserts_after, inserts_before)

    def test_clear_dirty_month_removes_one(self):
        self.hz.add_dirty_months(["2024-09", "2025-05", "2025-06"])
        self.hz.clear_dirty_month("2025-05")
        self.assertEqual(self.hz.get_dirty_months(), {"2024-09", "2025-06"})

    def test_clear_dirty_month_missing_is_noop(self):
        self.hz.add_dirty_months(["2025-05"])
        inserts_before = sum(1 for s in self.fake.exec_log if "INSERT INTO" in s)
        self.hz.clear_dirty_month("2099-12")
        inserts_after = sum(1 for s in self.fake.exec_log if "INSERT INTO" in s)
        # No INSERT — month wasn't in the set
        self.assertEqual(inserts_after, inserts_before)

    def test_dirty_ignores_empty_csv_entries(self):
        # External writers (or a bug elsewhere) might leave double-commas
        # or leading/trailing commas — get_dirty_months must drop them.
        self.fake.table["dirty_months"] = ",2025-05,,2025-06,"
        self.assertEqual(self.hz.get_dirty_months(), {"2025-05", "2025-06"})

    # ------------------------------------------------------------------
    # rebuild-from: atomic mode + cursor reset for operator-driven
    # full-history resummarize after a code change or data fix.
    # ------------------------------------------------------------------

    def _rebuild_from(self, month, *, today_override=None):
        """Invoke cmd_rebuild_from with a mocked args + today."""
        from unittest.mock import MagicMock
        from datetime import date as real_date
        if today_override:
            class FakeDate:
                @staticmethod
                def today(): return real_date.fromisoformat(today_override)
                @staticmethod
                def fromisoformat(s): return real_date.fromisoformat(s)
            self.hz.date = FakeDate
        args = MagicMock()
        args.month = month
        return self.hz.cmd_rebuild_from(args)

    def test_rebuild_from_sets_mode_and_cursor(self):
        self.hz.update_state(mode="normal", rebuild_cursor="2026-05")
        rc = self._rebuild_from("2022-01", today_override="2026-05-21")
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake.table["mode"], "rebuild")
        self.assertEqual(self.fake.table["rebuild_cursor"], "2022-01")

    def test_rebuild_from_validates_ym_format(self):
        # Garbage input must be rejected with non-zero exit.
        self.hz.update_state(mode="normal", rebuild_cursor="2026-05")
        rc = self._rebuild_from("2022/01", today_override="2026-05-21")
        self.assertEqual(rc, 2)
        # State untouched
        self.assertEqual(self.fake.table["mode"], "normal")
        self.assertEqual(self.fake.table["rebuild_cursor"], "2026-05")

    def test_rebuild_from_rejects_future_month(self):
        self.hz.update_state(mode="normal", rebuild_cursor="2026-05")
        rc = self._rebuild_from("2027-01", today_override="2026-05-21")
        self.assertEqual(rc, 2)
        self.assertEqual(self.fake.table["mode"], "normal")

    def test_rebuild_from_accepts_current_month(self):
        # Same month as today is allowed (operator might want to redo
        # the current incomplete month for some reason).
        self.hz.update_state(mode="normal", rebuild_cursor="2026-05")
        rc = self._rebuild_from("2026-05", today_override="2026-05-21")
        self.assertEqual(rc, 0)
        self.assertEqual(self.fake.table["rebuild_cursor"], "2026-05")


if __name__ == "__main__":
    unittest.main(verbosity=2)
