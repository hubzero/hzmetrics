"""Self-bootstrap + init + doctor regression tests.

Pins the contract documented in the cmd_run / cmd_tick wiring:

  - _self_bootstrap() is a no-op for any euid that doesn't map to
    apache / www-data (so dev shells, root, and the A/B harness don't
    touch shared state).
  - When the service user does run it: with site name resolved from
    /etc/hubzero.conf it runs the dir + DB bootstrap; without, it
    SystemExit(2)s with a clear message.
  - The expected-dirs list covers every directory the steady-state
    pipeline writes into.  If a new write target shows up later the
    test must be updated alongside it (defense-in-depth against
    "well, it works on my machine where I pre-created it").

These tests run without a live DB by stubbing the four DB-touching
hooks (mysql_exec, ensure_migrations_table, applied_migration_ids,
_apply_pending_migrations) so we don't depend on test-cfg credentials.
"""
import importlib
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _hz():
    import hzmetrics
    return importlib.reload(hzmetrics)


class IdentityGateTests(unittest.TestCase):
    """The first thing _self_bootstrap does is check euid.  If we're
    not running as one of the expected web-server users, everything
    else is skipped — no dirs touched, no DB queries."""

    def setUp(self) -> None:
        self.hz = _hz()
        self.calls: list = []
        # Sentinel patches — if they fire we know the gate was wrong.
        self.hz._bootstrap_dirs     = lambda: self.calls.append("dirs")
        self.hz._bootstrap_database = lambda: self.calls.append("db")

    def test_non_service_user_is_noop(self):
        self.hz._running_as_service_user = lambda: False
        self.hz._self_bootstrap()
        self.assertEqual(self.calls, [],
                         "non-service users must not trigger any "
                         "filesystem or DB side effects")

    def test_service_user_runs_full_chain(self):
        self.hz._running_as_service_user = lambda: True
        self.hz.SITE_EXPLICIT = True
        self.hz._self_bootstrap()
        self.assertEqual(self.calls, ["dirs", "db"],
                         "service users must run dirs then db, in order")

    def test_service_user_without_site_aborts(self):
        self.hz._running_as_service_user = lambda: True
        self.hz.SITE_EXPLICIT = False
        with self.assertRaises(SystemExit) as ctx:
            self.hz._self_bootstrap()
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(self.calls, [],
                         "site-name failure must abort before any "
                         "filesystem or DB work")


class ExpectedDirsTests(unittest.TestCase):
    """The list returned by _expected_dirs() is the contract between
    bootstrap and the steady-state pipeline.  Every directory the
    daily pipeline writes into must appear; if you add a new write
    target without adding it here, the next fresh-install cron tick
    silently leaves it missing."""

    def setUp(self) -> None:
        self.hz = _hz()

    def test_includes_install_tree(self):
        names = [str(d) for d in self.hz._expected_dirs()]
        self.assertTrue(any("/opt/hubzero/metrics" in n or
                            n.endswith("metrics") for n in names),
                        f"install root missing from {names!r}")
        for sub in ("bin", "conf", "state"):
            self.assertTrue(any(n.endswith(f"/{sub}") for n in names),
                            f"HZMETRICS_HOME/{sub} missing from {names!r}")

    def test_includes_log_staging_and_imports(self):
        names = [str(d) for d in self.hz._expected_dirs()]
        # Source-side: where logrotate / hub processes drop the files
        # we ingest.  The apache parent differs by distro — /var/log/
        # httpd on RHEL/Rocky vs /var/log/apache2 on Debian/Ubuntu —
        # so check for the daily/imported suffix under either parent.
        def _has(suffix):
            return any(n.endswith(suffix) for n in names)
        self.assertTrue(_has("/httpd/daily") or _has("/apache2/daily"),
                        f"apache daily dir missing from {names!r}")
        self.assertTrue(_has("/hubzero/daily"),
                        f"hubzero daily dir missing from {names!r}")
        # Sink-side: where archive-logs moves imported files to.
        self.assertTrue(_has("/httpd/imported") or _has("/apache2/imported"),
                        f"apache imported dir missing from {names!r}")
        self.assertTrue(_has("/hubzero/imported"),
                        f"hubzero imported dir missing from {names!r}")
        # Stage-side: where fetch-logs deposits the temporary working
        # copies (also the home of manage.log).
        self.assertTrue(_has("/var/log/hubzero/metrics"),
                        f"metrics staging dir missing from {names!r}")


class BootstrapDirsTests(unittest.TestCase):
    """_bootstrap_dirs() must create every missing entry, log each
    fresh create exactly once, and re-running on a populated tree
    must do nothing."""

    def setUp(self) -> None:
        self.hz = _hz()
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        # Re-target every expected dir into the temp root so the test
        # has full write access.
        self.expected = [
            root / "opt/hubzero/metrics",
            root / "opt/hubzero/metrics/bin",
            root / "opt/hubzero/metrics/conf",
            root / "opt/hubzero/metrics/state",
            root / "var/log/hubzero/metrics",
            root / "var/log/hubzero/daily",
            root / "var/log/hubzero/imported",
            root / "var/log/httpd/daily",
            root / "var/log/httpd/imported",
        ]
        self.hz._expected_dirs = lambda: self.expected
        # Logger isn't the assertion vector for this test, just a quiet sink.

    def test_creates_every_missing_dir(self):
        for d in self.expected:
            self.assertFalse(d.exists(), f"precondition: {d} starts missing")
        self.hz._bootstrap_dirs()
        for d in self.expected:
            self.assertTrue(d.is_dir(), f"{d} was not created")

    def test_second_run_is_noop(self):
        self.hz._bootstrap_dirs()
        # Re-running on a fully-populated tree must not raise.
        self.hz._bootstrap_dirs()


class InitCommandTests(unittest.TestCase):
    """`hzmetrics.py init` is the manual counterpart of _self_bootstrap
    — same steps but without the apache-uid gate (operator-driven)."""

    def setUp(self) -> None:
        self.hz = _hz()
        self.calls: list = []
        self.hz._bootstrap_dirs     = lambda: self.calls.append("dirs")
        self.hz._bootstrap_database = lambda: self.calls.append("db")

    def test_with_site_runs_dirs_and_db(self):
        self.hz.SITE_EXPLICIT = True
        class A: pass
        rc = self.hz.cmd_init(A())
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls, ["dirs", "db"])

    def test_without_site_returns_2_does_no_work(self):
        self.hz.SITE_EXPLICIT = False
        class A: pass
        rc = self.hz.cmd_init(A())
        self.assertEqual(rc, 2,
                         "missing site must surface as exit code 2 — "
                         "downstream filename/DB-prefix conventions all "
                         "key on SITE")
        self.assertEqual(self.calls, [])


class DoctorCommandTests(unittest.TestCase):
    """The doctor walks four phases (site, cfg, dirs, DB).  Without
    --fix it reports problems and returns 1; with --fix it calls the
    bootstrap helpers for the dir + DB phases."""

    def setUp(self) -> None:
        self.hz = _hz()
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.expected = [
            root / "metrics",
            root / "metrics/bin",
            root / "metrics/state",
            root / "logs/daily",
        ]
        self.hz._expected_dirs = lambda: self.expected
        self.hz.SITE_EXPLICIT = True
        self.hz.db_config = lambda: {
            "db_host": "localhost", "db_user": "u",
            "db_pass": "p", "metrics_db": "mdb",
        }
        # Stub the DB layer so the test doesn't need MariaDB up.
        self.hz.mysql_scalar = lambda *a, **kw: "mdb"  # DB exists
        self.hz.ensure_migrations_table = lambda mdb: None
        self.hz.applied_migration_ids   = lambda mdb: {m.id for m in self.hz.MIGRATIONS}

    def _args(self, **kw):
        class A: pass
        a = A()
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def test_healthy_install_returns_0(self):
        # Pre-create every expected dir → no dir failures.
        for d in self.expected:
            d.mkdir(parents=True, exist_ok=True)
        rc = self.hz.cmd_doctor(self._args(fix=False))
        self.assertEqual(rc, 0)

    def test_missing_dirs_without_fix_returns_1(self):
        # No dirs created → every dir fails.
        rc = self.hz.cmd_doctor(self._args(fix=False))
        self.assertEqual(rc, 1)
        for d in self.expected:
            self.assertFalse(d.exists(),
                             f"--fix=False must not create {d}")

    def test_missing_dirs_with_fix_creates_them(self):
        rc = self.hz.cmd_doctor(self._args(fix=True))
        self.assertEqual(rc, 0,
                         "--fix should resolve every fixable issue and "
                         "exit clean")
        for d in self.expected:
            self.assertTrue(d.is_dir(),
                            f"--fix should have created {d}")

    def test_missing_site_returns_1_without_attempting_repair(self):
        self.hz.SITE_EXPLICIT = False
        for d in self.expected:
            d.mkdir(parents=True, exist_ok=True)
        rc = self.hz.cmd_doctor(self._args(fix=True))
        self.assertEqual(rc, 1,
                         "missing site is unrecoverable by --fix "
                         "(needs /etc/hubzero.conf edit)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
