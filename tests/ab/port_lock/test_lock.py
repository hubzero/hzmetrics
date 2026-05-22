"""PID lock file format + stale-entry diagnostics.

`acquire_lock` writes three space-separated fields on the lock file:
  <pid> <init_start_epoch> <iso_acquired_timestamp>

`init_start_epoch` is the Unix epoch at which the current
environment's PID 1 started — captures host reboots AND container
restarts in one value (derived from /proc/stat btime +
/proc/1/stat starttime / CLK_TCK).  Together with PID and the
process-identity probe this disambiguates a stale entry from an
actively-held lock.  These tests pin the file format and the
diagnose-then-overwrite behavior so we don't lose the stale-
detection signal under refactor.
"""
import os, sys, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _hz():
    import importlib, hzmetrics
    return importlib.reload(hzmetrics)


class InitStartEpochTests(unittest.TestCase):

    def setUp(self) -> None:
        self.hz = _hz()

    def test_returns_plausible_epoch_on_linux(self):
        # On any Linux host with /proc, init_start_epoch is btime +
        # init_jiffies/HZ — a positive Unix epoch in the recent past.
        v = self.hz._init_start_epoch()
        if Path("/proc/stat").exists() and Path("/proc/1/stat").exists():
            self.assertIsInstance(v, int)
            self.assertGreater(v, 1_000_000_000)  # post-2001
            self.assertLess(v, 2_000_000_000)     # pre-2033
            # PID 1 starts >= host boot time, so init_start_epoch
            # must be >= btime read directly.
            btime = None
            for line in Path("/proc/stat").read_text().splitlines():
                if line.startswith("btime "):
                    btime = int(line.split()[1])
                    break
            self.assertIsNotNone(btime)
            self.assertGreaterEqual(v, btime)
        else:
            self.assertIsNone(v)

    def test_stable_within_same_environment(self):
        # Two consecutive calls in the same process / boot / container
        # return the same value — it's an environment identity, not a
        # wall-clock timestamp.
        a = self.hz._init_start_epoch()
        b = self.hz._init_start_epoch()
        self.assertEqual(a, b)


class ProcessIdentityProbeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.hz = _hz()

    def test_pid_zero_is_not_hzmetrics(self):
        # PID 0 isn't a real process; the probe must not crash on it.
        self.assertFalse(self.hz._process_looks_like_hzmetrics(0))

    def test_definitely_nonexistent_pid_is_not_hzmetrics(self):
        # No process exists with PID 2**31 - 1 on any normal kernel.
        self.assertFalse(self.hz._process_looks_like_hzmetrics(2**31 - 1))

    def test_own_pid_recognised(self):
        # The test process itself is running under a Python that loaded
        # hzmetrics — `hzmetrics` will appear in its cmdline (sys.path
        # insertion, module loads).  Use that as the positive case.
        if not Path("/proc").exists():
            self.skipTest("no /proc on this host")
        # Note: the test runner may not have "hzmetrics" in its argv,
        # only in sys.path.  Check our own /proc/<pid>/cmdline to know
        # which way to assert.
        cmdline = Path(f"/proc/{os.getpid()}/cmdline").read_bytes()
        if b"hzmetrics" in cmdline:
            self.assertTrue(self.hz._process_looks_like_hzmetrics(os.getpid()))
        else:
            self.assertFalse(self.hz._process_looks_like_hzmetrics(os.getpid()))


class LockFileFormatTests(unittest.TestCase):

    def setUp(self) -> None:
        self.hz = _hz()
        self.tmp = tempfile.TemporaryDirectory()
        # Redirect LOCK_FILE into the tempdir so acquire/release don't
        # touch the system one.
        self.hz.LOCK_FILE = Path(self.tmp.name) / "hzmetrics.pid"
        self.hz.HZMETRICS_HOME = Path(self.tmp.name)

    def tearDown(self) -> None:
        # Drop the lock if held, then clean up the tempdir.
        try:
            self.hz.release_lock()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_acquire_writes_pid_env_iso(self):
        ok = self.hz.acquire_lock()
        self.assertTrue(ok)
        body = self.hz.LOCK_FILE.read_text().strip()
        parts = body.split()
        self.assertEqual(len(parts), 3,
                         f"expected '<pid> <init_start_epoch> <iso>'; got {body!r}")
        # PID matches current
        self.assertEqual(int(parts[0]), os.getpid())
        # Env field is either a positive int or '?'
        if parts[1] != "?":
            self.assertEqual(parts[1], str(self.hz._init_start_epoch()))
        # ISO timestamp parseable
        from datetime import datetime
        datetime.fromisoformat(parts[2])

    def test_stale_file_overwritten_on_acquire(self):
        # Pre-populate the lock file with content claiming a prior holder.
        self.hz.LOCK_FILE.write_text("9999 1 2000-01-01T00:00:00.000+00:00\n")
        ok = self.hz.acquire_lock()
        self.assertTrue(ok)
        body = self.hz.LOCK_FILE.read_text().strip()
        # Old PID 9999 has been replaced by current.
        self.assertTrue(body.startswith(f"{os.getpid()} "),
                        f"stale PID 9999 not overwritten — file is {body!r}")

    def test_release_unlinks_file(self):
        self.assertTrue(self.hz.acquire_lock())
        self.assertTrue(self.hz.LOCK_FILE.exists())
        self.hz.release_lock()
        self.assertFalse(self.hz.LOCK_FILE.exists())

    def test_second_acquire_in_same_process_returns_false_or_true_consistently(self):
        # Within one process, a second acquire after we already hold it:
        # the kernel allows recursive locking on the same FD chain via
        # LOCK_EX | LOCK_NB depending on how we open it.  Our acquire
        # opens a new FD each call, so the second call should see the
        # lock held by us and return False (no double-grant).
        first = self.hz.acquire_lock()
        self.assertTrue(first)
        # Don't release — try a fresh acquire
        # NB: fcntl.flock on the same inode but a separate FD blocks /
        # returns False if another FD already holds it.  This is what
        # protects against two ticks running concurrently on one host.
        second = self.hz.acquire_lock()
        self.assertFalse(second,
                         "second acquire on same lock file unexpectedly succeeded")


class StaleDiagnosisTests(unittest.TestCase):
    """The diagnose function is purely informative — it never blocks
    the acquire.  These tests verify the parsing branches handle
    malformed / partial content without exploding."""

    def setUp(self) -> None:
        self.hz = _hz()
        self.tmp = tempfile.TemporaryDirectory()
        self.hz.LOCK_FILE = Path(self.tmp.name) / "hzmetrics.pid"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_empty_file_no_crash(self):
        self.hz.LOCK_FILE.write_text("")
        self.hz._diagnose_stale_lock_file()  # must not raise

    def test_nonexistent_file_no_crash(self):
        # File doesn't exist yet
        self.hz._diagnose_stale_lock_file()

    def test_garbage_first_field_no_crash(self):
        self.hz.LOCK_FILE.write_text("not-a-number garbage 2026-01-01\n")
        self.hz._diagnose_stale_lock_file()

    def test_pid_only_no_crash(self):
        # Old-format file from the pre-boot-epoch days
        self.hz.LOCK_FILE.write_text("12345\n")
        self.hz._diagnose_stale_lock_file()

    def test_garbage_env_field_no_crash(self):
        self.hz.LOCK_FILE.write_text("12345 not-a-number whatever\n")
        self.hz._diagnose_stale_lock_file()


if __name__ == "__main__":
    unittest.main(verbosity=2)
