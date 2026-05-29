"""Per-file atomic-import + crash recovery via imported_sources.

The pipeline used to call do_import_apache / do_import_auth /
do_import_webhits in sequence and only move the source file to
imported/ after ALL of them succeeded.  That left a window where:

  - import-apache committed web rows, then
  - import-auth was killed mid-INSERT
  - retry re-imported BOTH (duplicating web rows)

Or worse:

  - import-apache committed web AND webhits, then
  - process was killed BEFORE moving the source file to imported/
  - retry tried to re-import, hitting the same data again

The fix wraps each source file's import in a single transaction
guarded by INSERT IGNORE on imported_sources(filename, target).
Then:

  - Crash mid-INSERT → InnoDB rolls back, file stays in daily/, retry
    sees no imported_sources row, imports cleanly.
  - Crash post-COMMIT pre-move → imported_sources row exists, retry
    sees rowcount=0 on INSERT IGNORE, skips data INSERT, retries move.
  - Per-table independence: web (with its inline-derived webhits)
    and userlogin commit on different files; a crash during auth
    doesn't unwind web/webhits.

webhits is no longer a separately-tracked import target — it's
populated inline by do_import_apache as a derived count of kept rows
per day, in the same transaction that commits web.  The forget-import
tests still cover the legacy 'webhits'-target rows that may exist in
imported_sources on hubs imported before the refactor.

These tests pin the new semantics and the forget-import escape hatch
that lets an operator deliberately re-import a file.
"""
import sys, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _hz():
    import importlib, hzmetrics
    return importlib.reload(hzmetrics)


# ---------------------------------------------------------------------------
# Source-code shape: confirm the atomic helpers exist and the import
# functions accept a `conn=` kwarg (so the wrapping transaction is the
# caller's).  A future refactor that drops the kwarg loses crash safety.
# ---------------------------------------------------------------------------

class StructuralTests(unittest.TestCase):

    def setUp(self) -> None:
        self.src = (REPO / "hzmetrics.py").read_text()

    def test_imported_sources_migration_present(self):
        # Migration #44 creates the table.
        self.assertIn("id=44,", self.src)
        self.assertIn("imported_sources", self.src)
        self.assertIn("uniq_filename_target", self.src)

    def test_setup_db_includes_imported_sources(self):
        # Fresh setup-db must create the table too — otherwise a new
        # install would lack it until the migrations step runs.
        self.assertIn("`imported_sources` (", self.src)

    def test_do_import_apache_accepts_conn_kwarg(self):
        self.assertIn(
            "def do_import_apache(input_file, *, batch_size=5000, dry_run=False, conn=None)",
            self.src,
            "do_import_apache must accept conn= for transaction-owned imports")

    def test_do_import_auth_accepts_conn_kwarg(self):
        self.assertIn(
            "def do_import_auth(input_file, *, batch_size=5000, dry_run=False, conn=None)",
            self.src)

    def test_webhits_emitted_inline_by_do_import_apache(self):
        # After the refactor that removed do_import_webhits, webhits
        # is populated by a per-day counter inside do_import_apache's
        # filter loop, then INSERTed in the same transaction.  Pin
        # both the counter wiring and the INSERT call so a future
        # refactor that breaks the inline path gets caught.
        self.assertIn("daily_hits[datestamp] += 1", self.src,
            "do_import_apache must accumulate webhits daily counts inline")
        self.assertIn("INSERT INTO webhits (datetime, hits)", self.src,
            "do_import_apache must INSERT webhits rows in the same txn")
        # The standalone parser must NOT come back as a hidden re-import.
        self.assertNotIn("def do_import_webhits(", self.src,
            "do_import_webhits should be removed — webhits is derived "
            "from web by do_import_apache (inline) and do_rebuild_webhits "
            "(operator-driven regen)")

    def test_atomic_helpers_defined(self):
        self.assertIn("def _import_apache_file_atomic(", self.src)
        self.assertIn("def _import_auth_file_atomic(",  self.src)
        self.assertIn("def _record_imported_source(",   self.src)

    def test_do_import_day_calls_atomic_helpers(self):
        # Regression guard: the day-level function must dispatch through
        # the atomic per-file helpers, not the legacy fetch+stage+import
        # path that had the post-commit-pre-move window.
        self.assertIn("_import_apache_file_atomic(src", self.src)
        self.assertIn("_import_auth_file_atomic(src",   self.src)

    def test_forget_import_cli_defined(self):
        self.assertIn("def cmd_forget_import(args)", self.src)
        # And it deletes by PK range, not by date:
        squashed = " ".join(self.src.split())
        self.assertIn("WHERE id BETWEEN %s AND %s", squashed,
                      "forget-import must DELETE BY PK range, not by date")


# ---------------------------------------------------------------------------
# _record_imported_source semantics: monkey-patched DB layer.
# ---------------------------------------------------------------------------

class RecordImportedSourceTests(unittest.TestCase):

    def setUp(self) -> None:
        self.hz = _hz()
        # Stub db_credentials so the function knows the metrics_db name.
        self.hz.db_credentials = lambda: ("localhost", "user", "pass", "mdb")

    def _make_cur(self, *, first_insert_succeeds: bool):
        """A minimal cursor stub that records the SQL it sees.  rowcount
        is 1 for the first INSERT (if first_insert_succeeds) else 0 (a
        UNIQUE collision — the row already exists).  Subsequent UPDATE
        always reports rowcount=1."""
        class FakeCur:
            def __init__(self):
                self.calls = []
                self.rowcount = 0
                self.lastrowid = 0
                self._first_insert_done = False

            def execute(self, sql, params=None):
                self.calls.append((sql, params))
                if "INSERT IGNORE" in sql and "imported_sources" in sql:
                    if first_insert_succeeds and not self._first_insert_done:
                        self.rowcount = 1
                        self.lastrowid = 42
                        self._first_insert_done = True
                    else:
                        self.rowcount = 0
                elif "UPDATE" in sql and "imported_sources" in sql:
                    self.rowcount = 1

        return FakeCur()

    def test_first_time_returns_true_and_updates_pk_range(self):
        cur = self._make_cur(first_insert_succeeds=True)
        out = self.hz._record_imported_source(
            cur, "x.gz", "web", 100, 250, 151)
        self.assertTrue(out)
        # Both INSERT IGNORE and UPDATE pk_range should have run
        kinds = [("INSERT IGNORE" in c[0], "UPDATE" in c[0]) for c in cur.calls]
        self.assertIn((True, False), kinds)
        self.assertIn((False, True), kinds)

    def test_already_imported_returns_false_and_skips_update(self):
        cur = self._make_cur(first_insert_succeeds=False)
        out = self.hz._record_imported_source(
            cur, "x.gz", "web", 100, 250, 151)
        self.assertFalse(out)
        # Only the INSERT IGNORE should have run; no UPDATE
        kinds = [("UPDATE" in c[0]) for c in cur.calls]
        self.assertNotIn(True, kinds)


# ---------------------------------------------------------------------------
# forget-import semantics.
# ---------------------------------------------------------------------------

class ForgetImportTests(unittest.TestCase):

    def setUp(self) -> None:
        self.hz = _hz()
        self.hz.db_credentials = lambda: ("localhost", "user", "pass", "mdb")
        self.exec_calls = []
        self.hz.mysql_exec = lambda sql, params=None: (
            self.exec_calls.append((sql, params)) or 0)

    def _stub_query(self, rows):
        self.hz.mysql_query = lambda sql, params=None: rows

    def _make_args(self, filename, table="all", dry_run=False):
        from unittest.mock import MagicMock
        a = MagicMock()
        a.filename = filename
        a.table = table
        a.dry_run = dry_run
        return a

    def test_nonexistent_filename_returns_1(self):
        self._stub_query([])
        rc = self.hz.cmd_forget_import(self._make_args("missing.gz"))
        self.assertEqual(rc, 1)
        self.assertEqual(self.exec_calls, [])

    def test_web_record_deletes_pk_range_then_source_row(self):
        self._stub_query([(5, "web", 1000, 1500, 501)])
        rc = self.hz.cmd_forget_import(self._make_args("x.gz"))
        self.assertEqual(rc, 0)
        # First exec must be the data DELETE BY PK range, second the
        # imported_sources cleanup.
        self.assertEqual(len(self.exec_calls), 2)
        self.assertIn("DELETE FROM mdb.web", self.exec_calls[0][0])
        self.assertEqual(self.exec_calls[0][1], (1000, 1500))
        self.assertIn("imported_sources", self.exec_calls[1][0])

    def test_webhits_record_skips_data_delete_with_warning(self):
        # pk_start/pk_end NULL → no auto-DELETE; just clear the marker.
        self._stub_query([(7, "webhits", None, None, 30)])
        rc = self.hz.cmd_forget_import(self._make_args("x.gz"))
        self.assertEqual(rc, 0)
        # Only the imported_sources DELETE happened
        self.assertEqual(len(self.exec_calls), 1)
        self.assertIn("imported_sources", self.exec_calls[0][0])
        # No data DELETE
        self.assertFalse(any("FROM mdb.webhits" in c[0] for c in self.exec_calls))

    def test_table_filter_scopes_to_one_target(self):
        # Two records for same filename — one for web, one for webhits.
        # --table web should affect only the web record.
        self._stub_query([
            (5, "web",     1000, 1500, 501),
            (7, "webhits", None, None, 30),
        ])
        rc = self.hz.cmd_forget_import(
            self._make_args("x.gz", table="web"))
        self.assertEqual(rc, 0)
        # Only the web target's DELETEs ran (data + source row)
        self.assertEqual(len(self.exec_calls), 2)
        for sql, _ in self.exec_calls:
            self.assertNotIn("webhits", sql)

    def test_dry_run_makes_no_writes(self):
        self._stub_query([(5, "web", 1000, 1500, 501)])
        rc = self.hz.cmd_forget_import(
            self._make_args("x.gz", dry_run=True))
        self.assertEqual(rc, 0)
        # Nothing actually executed
        self.assertEqual(self.exec_calls, [])

    def test_all_table_filter_default_matches_every_record(self):
        self._stub_query([
            (5, "web",       1000, 1500, 501),
            (6, "userlogin", 9000, 9200, 201),
            (7, "webhits",   None, None, 30),
        ])
        rc = self.hz.cmd_forget_import(
            self._make_args("x.gz", table="all"))
        self.assertEqual(rc, 0)
        # web: 2 calls (data DELETE + source DELETE)
        # userlogin: 2 calls
        # webhits: 1 call (no data DELETE; just source DELETE)
        self.assertEqual(len(self.exec_calls), 5)


# ---------------------------------------------------------------------------
# Atomic per-file flow: stub the DB + filesystem, verify the orchestration.
# ---------------------------------------------------------------------------

class AtomicHelperOrchestrationTests(unittest.TestCase):
    """The atomic helper opens a transaction, calls do_import_*(conn=conn),
    commits, and moves the file.  We stub the do_import_* workers and
    the DB conn to verify the call sequence and the imported_sources
    behavior."""

    def setUp(self) -> None:
        import tempfile, importlib, hzmetrics
        self.hz = importlib.reload(hzmetrics)
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.hz.HTTPD_IMPORTED = root / "imported"
        self.hz.HZ_METRICS_STAGING = root / "staging"
        self.hz.STAGED_APACHE = self.hz.HZ_METRICS_STAGING / "_hub_apache.log"
        self.hz.STAGED_AUTH = self.hz.HZ_METRICS_STAGING / "_hub_auth.log"
        self.hz.db_credentials = lambda: ("localhost", "user", "pass", "mdb")

        # Stub stream-decompress: just create an empty staged file
        self.hz._stream_decompress = (
            lambda src, out: out.write(b""))

        # Calls captured for assertion
        self.calls = []

        def stub_apache(*a, **kw):
            self.calls.append(("do_import_apache", a, kw))
            return 0, 1000, 1500, 501

        def stub_auth(*a, **kw):
            self.calls.append(("do_import_auth", a, kw))
            return 0, 9000, 9200, 201

        def stub_bots(*a, **kw):
            self.calls.append(("do_identify_bots", a, kw))

        self.hz.do_import_apache  = stub_apache
        self.hz.do_import_auth    = stub_auth
        self.hz.do_identify_bots  = stub_bots

        # Stub the DB connection + cursor
        class FakeCur:
            def __init__(self):
                self.calls = []
                self.rowcount = 0
                self.lastrowid = 0
            def execute(self, sql, params=None):
                self.calls.append(("execute", sql, params))
                if "INSERT IGNORE" in sql and "imported_sources" in sql:
                    self.rowcount = 1
                    self.lastrowid = 42
                else:
                    self.rowcount = 1
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class FakeConn:
            def __init__(self):
                self.began = False
                self.committed = False
                self.rolled_back = False
                self.closed = False
                self.cur = FakeCur()
            def cursor(self): return self.cur
            def begin(self):    self.began = True
            def commit(self):   self.committed = True
            def rollback(self): self.rolled_back = True
            def close(self):    self.closed = True

        self.fake_conn = FakeConn()
        self.open_db_calls: list = []
        def _capture_open_db(*a, **kw):
            self.open_db_calls.append((a, kw))
            return self.fake_conn
        self.hz._open_db = _capture_open_db

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_apache_file_atomic_commits_and_moves(self):
        # Create a source file to "move"
        src = Path(self.tmp.name) / "geodynamics-access.log-20260521.gz"
        src.write_bytes(b"")
        self.hz._import_apache_file_atomic(src, dry_run=False)
        # Transaction lifecycle
        self.assertTrue(self.fake_conn.began)
        self.assertTrue(self.fake_conn.committed)
        self.assertFalse(self.fake_conn.rolled_back)
        # Apache importer and identify-bots ran (first-time path).
        # webhits is no longer a separate call — do_import_apache
        # emits webhits rows inline via its own daily_hits counter.
        names = [c[0] for c in self.calls]
        self.assertIn("do_import_apache",  names)
        self.assertIn("do_identify_bots",  names)
        self.assertNotIn("do_import_webhits", names,
            "webhits is now inline in do_import_apache; the standalone "
            "function was removed in the rebuild-webhits refactor")
        # File moved to imported/
        self.assertFalse(src.exists())
        self.assertTrue((self.hz.HTTPD_IMPORTED / src.name).exists())

    def test_auth_file_atomic_commits_and_moves(self):
        # Wire HZ_IMPORTED too
        self.hz.HZ_IMPORTED = Path(self.tmp.name) / "auth_imported"
        src = Path(self.tmp.name) / "cmsauth-20260521.log.gz"
        src.write_bytes(b"")
        self.hz._import_auth_file_atomic(src, dry_run=False)
        self.assertTrue(self.fake_conn.committed)
        names = [c[0] for c in self.calls]
        self.assertIn("do_import_auth", names)
        self.assertFalse(src.exists())
        self.assertTrue((self.hz.HZ_IMPORTED / src.name).exists())

    def test_apache_dry_run_skips_everything(self):
        src = Path(self.tmp.name) / "access.log-20260521.gz"
        src.write_bytes(b"")
        self.hz._import_apache_file_atomic(src, dry_run=True)
        self.assertFalse(self.fake_conn.began)
        self.assertEqual(self.calls, [])
        # File NOT moved in dry-run
        self.assertTrue(src.exists())

    def test_apache_rolls_back_on_import_failure(self):
        # Make the apache worker raise — atomic helper should rollback.
        def boom(*a, **kw):
            raise RuntimeError("simulated apache import failure")
        self.hz.do_import_apache = boom

        src = Path(self.tmp.name) / "access.log-20260521.gz"
        src.write_bytes(b"")
        with self.assertRaises(RuntimeError):
            self.hz._import_apache_file_atomic(src, dry_run=False)
        self.assertTrue(self.fake_conn.rolled_back)
        self.assertFalse(self.fake_conn.committed)
        # File NOT moved (the txn rolled back; we never reached the move)
        self.assertTrue(src.exists())
        self.assertFalse((self.hz.HTTPD_IMPORTED / src.name).exists())

    # ------------------------------------------------------------------
    # Pin the default-schema bug fix.  do_import_apache, do_identify_bots,
    # do_import_webhits, and do_import_auth all run unqualified queries
    # like `SELECT FROM exclude_list` and `INSERT INTO web …` — they
    # rely on the caller-owned conn having a default schema selected.
    # If the atomic helper opens its conn via `_open_db()` (no database
    # arg), MariaDB returns "(1046) No database selected" on the first
    # SELECT and rolls back the whole transaction.  This was a latent
    # bug from 2026-05-21 until 2026-05-25, silently failing every cron
    # import for days.
    #
    # The fix is to pass metrics_db; these tests assert that the call
    # happened, so a future revert to bare `_open_db()` fails loudly.
    # ------------------------------------------------------------------

    def test_apache_opens_conn_with_metrics_db_as_default_schema(self):
        src = Path(self.tmp.name) / "geodynamics-access.log-20260521.gz"
        src.write_bytes(b"")
        self.hz._import_apache_file_atomic(src, dry_run=False)
        self.assertTrue(self.open_db_calls,
                        "expected _open_db to be called from the atomic helper")
        args, kwargs = self.open_db_calls[0]
        db = (args[0] if args else kwargs.get("database"))
        self.assertEqual(
            db, "mdb",
            f"_import_apache_file_atomic must pass metrics_db to _open_db so "
            f"unqualified `SELECT FROM exclude_list` / `INSERT INTO web` work; "
            f"actual call = (args={args!r}, kwargs={kwargs!r})")

    def test_auth_opens_conn_with_metrics_db_as_default_schema(self):
        self.hz.HZ_IMPORTED = Path(self.tmp.name) / "auth_imported"
        src = Path(self.tmp.name) / "cmsauth-20260521.log.gz"
        src.write_bytes(b"")
        self.hz._import_auth_file_atomic(src, dry_run=False)
        self.assertTrue(self.open_db_calls,
                        "expected _open_db to be called from the atomic helper")
        args, kwargs = self.open_db_calls[0]
        db = (args[0] if args else kwargs.get("database"))
        self.assertEqual(
            db, "mdb",
            f"_import_auth_file_atomic must pass metrics_db to _open_db; "
            f"actual call = (args={args!r}, kwargs={kwargs!r})")


if __name__ == "__main__":
    unittest.main(verbosity=2)
