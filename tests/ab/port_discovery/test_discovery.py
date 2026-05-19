"""Pure-Python tests for the source-log discovery layer.

Builds a temp tree mirroring the real-world variations we've seen on
production hosts:

  daily/<site>-access.log-YYYYMMDD[.gz]     (current convention)
  daily/YYYY/<site>-access-YYYYMMDD.log.gz  (sysadmin year-subdir)
  daily.holding/<site>-access.log-YYYYMMDD.gz  (logrotate alternate dest)

…then exercises enumerate_log_sources / oldest_pending_month /
pending_days_for_month against it.  Run from run.sh — exits non-zero on
any failure.
"""
import os, sys, tempfile, unittest
from pathlib import Path

# Repo root is two levels up from this file.
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

# Force a clean SITE before importing — the module reads /etc/hubzero.conf
# at import time.  Use a dedicated site name so we never collide with the
# real layout if the test ever leaks paths.
TEST_SITE = "testsite"


def _make_tree(root: Path, layout: dict) -> None:
    """Materialise a {relpath: bytes_or_None} dict under root."""
    for relpath, content in layout.items():
        p = root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            content = b""
        p.write_bytes(content)


def _patch_hz_paths(hz, root: Path) -> None:
    """Re-point hzmetrics's path constants at our tmp root."""
    hz.SITE           = TEST_SITE
    hz.APACHE_LOG_DIR = root / "httpd"
    hz.CMS_LOG_DIR    = root / "hubzero"
    hz.HTTPD_DAILY    = hz.APACHE_LOG_DIR / "daily"
    hz.HTTPD_HOLDING  = hz.APACHE_LOG_DIR / "daily.holding"
    hz.HZ_DAILY       = hz.CMS_LOG_DIR / "daily"
    hz.HZ_HOLDING     = hz.CMS_LOG_DIR / "daily.holding"
    hz.HTTPD_IMPORTED = hz.APACHE_LOG_DIR / "imported"
    hz.HZ_IMPORTED    = hz.CMS_LOG_DIR / "imported"


class DiscoveryTests(unittest.TestCase):

    def setUp(self) -> None:
        # Late import so we can patch module-level constants per test.
        import hzmetrics
        self.hz = hzmetrics
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _patch_hz_paths(self.hz, self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # ------------------------------------------------------------------
    # enumerate_log_sources
    # ------------------------------------------------------------------

    def test_flat_daily_only(self):
        _make_tree(self.root, {
            f"httpd/daily/{TEST_SITE}-access.log-20260518.gz":     None,
            f"httpd/daily/{TEST_SITE}-access.log-20260517.gz":     None,
            f"hubzero/daily/cmsauth.log-20260518":                 None,
        })
        access = self.hz.enumerate_log_sources("access")
        self.assertEqual([d for d, _ in access], ["20260517", "20260518"])
        auth = self.hz.enumerate_log_sources("auth")
        self.assertEqual([d for d, _ in auth], ["20260518"])

    def test_year_subdir(self):
        _make_tree(self.root, {
            # Old sysadmin layout: daily/YYYY/ with the older filename shape
            f"httpd/daily/2022/{TEST_SITE}-access-20220101.log.gz": None,
            f"httpd/daily/2022/{TEST_SITE}-access-20220301.log.gz": None,
            f"httpd/daily/2023/{TEST_SITE}-access-20231215.log.gz": None,
            # Current layout, latest day
            f"httpd/daily/{TEST_SITE}-access.log-20260518.gz":     None,
        })
        access = self.hz.enumerate_log_sources("access")
        dates = [d for d, _ in access]
        self.assertEqual(dates, ["20220101", "20220301", "20231215", "20260518"])
        # Confirm we picked them up from the year subdir, not the root.
        paths = {d: p for d, p in access}
        self.assertEqual(paths["20220101"].parent.name, "2022")
        self.assertEqual(paths["20231215"].parent.name, "2023")
        self.assertEqual(paths["20260518"].parent.name, "daily")

    def test_daily_holding(self):
        _make_tree(self.root, {
            f"httpd/daily.holding/{TEST_SITE}-access.log-20250605.gz": None,
            f"httpd/daily.holding/{TEST_SITE}-access.log-20250606.gz": None,
            f"hubzero/daily.holding/cmsauth.log-20250605.gz":           None,
            # No current daily/ files at all
        })
        access = self.hz.enumerate_log_sources("access")
        self.assertEqual([d for d, _ in access], ["20250605", "20250606"])
        # And no daily/ dir exists yet — code should not fault.
        self.assertEqual(
            [d for d, _ in self.hz.enumerate_log_sources("auth")],
            ["20250605"],
        )

    def test_dedup_prefers_daily_over_holding(self):
        # Same date present in both daily/ and daily.holding/.  daily/ wins.
        flat = f"httpd/daily/{TEST_SITE}-access.log-20260518.gz"
        held = f"httpd/daily.holding/{TEST_SITE}-access.log-20260518.gz"
        _make_tree(self.root, {flat: None, held: None})
        access = self.hz.enumerate_log_sources("access")
        self.assertEqual(len(access), 1)
        date_str, path = access[0]
        self.assertEqual(date_str, "20260518")
        self.assertEqual(path.parent.name, "daily")

    def test_no_sources_returns_empty(self):
        # Empty root, no dirs even.
        self.assertEqual(self.hz.enumerate_log_sources("access"), [])
        self.assertEqual(self.hz.enumerate_log_sources("auth"),   [])

    def test_full_realistic_mix(self):
        # Mirrors what we found on the geodynamics host:
        # 2022/2023 in daily/YYYY/, 2025-H2..2026 mostly in daily.holding/,
        # most recent week back in daily/.
        layout = {}
        for d in ("20220101", "20220615", "20221231"):
            layout[f"httpd/daily/2022/{TEST_SITE}-access-{d}.log.gz"] = None
        for d in ("20230615", "20231231"):
            layout[f"httpd/daily/2023/{TEST_SITE}-access-{d}.log.gz"] = None
        for d in ("20250901", "20251215", "20260315"):
            layout[f"httpd/daily.holding/{TEST_SITE}-access.log-{d}.gz"] = None
        for d in ("20260516", "20260517", "20260518"):
            layout[f"httpd/daily/{TEST_SITE}-access.log-{d}.gz"] = None
        _make_tree(self.root, layout)

        access = self.hz.enumerate_log_sources("access")
        dates = [d for d, _ in access]
        # Sorted chronological across all three locations.
        self.assertEqual(dates, [
            "20220101", "20220615", "20221231",
            "20230615", "20231231",
            "20250901", "20251215", "20260315",
            "20260516", "20260517", "20260518",
        ])

    # ------------------------------------------------------------------
    # oldest_pending_month / pending_days_for_month built on enumerate
    # ------------------------------------------------------------------

    def test_oldest_pending_month_walks_year_subdir(self):
        _make_tree(self.root, {
            # Even though daily/ root has only a current month, the
            # year-subdir contains older data — that should win.
            f"httpd/daily/{TEST_SITE}-access.log-20260518.gz":      None,
            f"httpd/daily/2022/{TEST_SITE}-access-20220301.log.gz": None,
        })
        self.assertEqual(self.hz.oldest_pending_month(), "2022-03")

    def test_oldest_pending_month_none_when_empty(self):
        # No source files anywhere.  Even the dirs don't exist.
        self.assertIsNone(self.hz.oldest_pending_month())

    def test_pending_days_for_month_includes_holding(self):
        _make_tree(self.root, {
            f"httpd/daily.holding/{TEST_SITE}-access.log-20250605.gz": None,
            f"httpd/daily.holding/{TEST_SITE}-access.log-20250606.gz": None,
            f"httpd/daily.holding/{TEST_SITE}-access.log-20250715.gz": None,
        })
        self.assertEqual(self.hz.pending_days_for_month("2025-06"),
                         ["20250605", "20250606"])
        self.assertEqual(self.hz.pending_days_for_month("2025-07"),
                         ["20250715"])
        self.assertEqual(self.hz.pending_days_for_month("2025-08"), [])

    # ------------------------------------------------------------------
    # _rmdir_if_empty
    # ------------------------------------------------------------------

    def test_rmdir_if_empty_removes_empty(self):
        d = self.root / "scratch"
        d.mkdir()
        self.hz._rmdir_if_empty(d)
        self.assertFalse(d.exists())

    def test_rmdir_if_empty_keeps_nonempty(self):
        d = self.root / "scratch"
        d.mkdir()
        (d / "file").touch()
        self.hz._rmdir_if_empty(d)
        self.assertTrue(d.exists(), "non-empty dir must not be removed")

    def test_rmdir_if_empty_ignores_missing(self):
        # Should not raise.
        self.hz._rmdir_if_empty(self.root / "does-not-exist")

    # ------------------------------------------------------------------
    # _source_files_matching (the date-filtered helper used by archive/fetch)
    # ------------------------------------------------------------------

    def test_source_files_matching_filters_to_date(self):
        _make_tree(self.root, {
            f"httpd/daily/{TEST_SITE}-access.log-20260517.gz":      None,
            f"httpd/daily/{TEST_SITE}-access.log-20260518.gz":      None,
            f"httpd/daily/2022/{TEST_SITE}-access-20220301.log.gz": None,
        })
        m18 = self.hz._source_files_matching("access", "20260518")
        self.assertEqual(len(m18), 1)
        self.assertEqual(m18[0].name, f"{TEST_SITE}-access.log-20260518.gz")
        m22 = self.hz._source_files_matching("access", "20220301")
        self.assertEqual(len(m22), 1)
        self.assertEqual(m22[0].parent.name, "2022")
        # date_filter=None returns everything pending
        every = self.hz._source_files_matching("access", None)
        self.assertEqual(len(every), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
