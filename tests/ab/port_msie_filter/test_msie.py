"""Regression tests for the date-bound MSIE-Trident UA filter.

Background: a long-running distributed bot family spoofs pre-2016 IE
user-agent strings (MSIE 7..11 / Trident 4..7) and pounds the hub from
~21 k rotating IPs.  On the geodynamics hub we observed:

  2022:     0 MSIE-Trident hits   (zero false positives)
  2023:     0
  2024:     69 k                  (download-endpoint open-redirect probes)
  2025-01..05: 0
  2025-06..2026-01: 0
  2026-02+: 190 k+                (same bot, new URL patterns)

— i.e. on this hub MSIE-Trident UAs are 100 % bot.  The filter is
date-bound from `_MSIE_FILTER_FROM` (= "2022-01-01") so an archival
backfill of pre-EOL access logs isn't retroactively filtered — real
MSIE traffic was still possible before Microsoft EOL'd IE in 2022.

These tests pin the regex, the date watermark, and that import-apache
applies both as a gated filter that increments its own skip counter.
"""
import sys, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _hz():
    import importlib, hzmetrics
    return importlib.reload(hzmetrics)


class MsieTridentRegexTests(unittest.TestCase):

    def setUp(self) -> None:
        self.hz = _hz()

    # --- spoofed-IE UAs that drive the bot waves -------------------------

    def test_msie_7_through_11_match(self):
        for v in ("7.0", "8.0", "9.0", "10.0", "11.0"):
            ua = f"Mozilla/5.0 (compatible; MSIE {v}; Windows NT 6.1)"
            self.assertTrue(
                self.hz._MSIE_TRIDENT_RE.search(ua),
                f"MSIE {v} UA must match the filter: {ua!r}")

    def test_trident_4_through_7_match(self):
        for v in ("4.0", "5.0", "6.0", "7.0"):
            ua = f"Mozilla/5.0 (compatible; Windows NT 6.1; Trident/{v})"
            self.assertTrue(
                self.hz._MSIE_TRIDENT_RE.search(ua),
                f"Trident/{v} UA must match: {ua!r}")

    def test_case_insensitive(self):
        # The bot lowercases / mixes case in some variants — observed in
        # production rows.  The regex is IGNORECASE.
        self.assertTrue(self.hz._MSIE_TRIDENT_RE.search(
            "mozilla/5.0 (compatible; msie 8.0; windows; trident/4.0)"))

    # --- real modern UAs must NOT match ----------------------------------

    def test_modern_browsers_do_not_match(self):
        modern = [
            # Chrome
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            # Firefox
            "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
            # Safari macOS
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.2 Safari/605.1.15",
            # Edge (Chromium)
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
            # curl, wget — not MSIE-Trident
            "curl/8.0.1",
            "Wget/1.21",
        ]
        for ua in modern:
            self.assertFalse(
                self.hz._MSIE_TRIDENT_RE.search(ua),
                f"modern UA must NOT match the MSIE filter: {ua!r}")

    # --- MSIE 6 and earlier are NOT in scope -----------------------------

    def test_msie_6_does_not_match(self):
        # MSIE 6 is older than the bot's UA pool (it sticks to 7-11).
        # Keep it out of the filter — if a real pre-EOL log ever shows
        # MSIE 6, we shouldn't drop it.
        self.assertFalse(self.hz._MSIE_TRIDENT_RE.search(
            "Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1)"))


class MsieFilterWatermarkTests(unittest.TestCase):
    """The date-bind on the filter is a contract — operators expect that
    archival backfills of pre-EOL access logs aren't retroactively
    flagged.  Pin the constant and its meaning."""

    def setUp(self) -> None:
        self.hz = _hz()

    def test_watermark_is_2022_01_01(self):
        self.assertEqual(self.hz._MSIE_FILTER_FROM, "2022-01-01")

    def test_lexicographic_compare_with_datestamp(self):
        # The filter compares the apache log's datestamp (YYYY-MM-DD)
        # against _MSIE_FILTER_FROM with `>=`.  Verify the boundaries.
        self.assertLess("2021-12-31", self.hz._MSIE_FILTER_FROM)
        self.assertGreaterEqual("2022-01-01", self.hz._MSIE_FILTER_FROM)
        self.assertGreaterEqual("2026-05-28", self.hz._MSIE_FILTER_FROM)


class ImportApacheGatedCheckTests(unittest.TestCase):
    """Structural assertion that do_import_apache applies the MSIE filter
    gated on _MSIE_FILTER_FROM and counts skips separately.  A regex
    that exists in module scope but isn't wired into the import path
    silently does nothing — the dnload/insert pattern of recent history
    shows that gap can hide.  Source-grep so the wiring can't quietly
    drift."""

    def setUp(self) -> None:
        self.src = (Path(REPO) / "hzmetrics.py").read_text()

    def test_skipped_msie_counter_present(self):
        self.assertIn("skipped_msie = 0", self.src,
                      "do_import_apache must declare skipped_msie counter")

    def test_date_gated_msie_check_in_filter_chain(self):
        # The filter check must include BOTH the date guard and the
        # regex search, and bump the counter — exact contract.
        self.assertIn("datestamp >= _MSIE_FILTER_FROM", self.src,
                      "MSIE filter must be date-bound (post-EOL only)")
        self.assertIn("_MSIE_TRIDENT_RE.search(useragent)", self.src,
                      "MSIE filter must use the regex on useragent")
        self.assertIn("skipped_msie += 1", self.src,
                      "MSIE skip must increment skipped_msie")

    def test_msie_skip_reported_in_summary_log(self):
        self.assertIn("msie={skipped_msie}", self.src,
                      "import-apache summary line must report msie skip count "
                      "so an operator sees the filter taking effect")


if __name__ == "__main__":
    unittest.main(verbosity=2)
