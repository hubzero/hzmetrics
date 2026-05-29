"""Regression tests for the 2026-02 audit additions to the import-time
crawl-filter chain.

Background: a 3-month survey (2026-02 .. 2026-05) of `web` rows that
passed all existing filters found ~61 % were from UAs with the
distributed-bot signature (>=1000 distinct IPs each, ~1 hit/IP).
Three URL families dominated the slip-through:

  /register             629 k hits (≈21 %)  → empty-Referer drop
  /citations/browse..   113 k hits          → empty-Referer drop
                                              (pinned in port_referer_spam)
  /events/<year>/...    ~340 k hits         → empty-Referer drop, year
                                              older than log_year - 3

This file covers the /register filter and the date-bound /events/<year>
filter.  The /citations/browse case is covered by port_referer_spam.
All three rely on empty Referer as the bot signal: real users reach
these URLs through internal navigation (so the browser sets Referer),
while the distributed-bot family fires bare requests with no Referer.

The /events filter compares URL year against the **log line's
datestamp year** (not date.today()) so that a backfill of historical
logs filters consistently with their own era — a backfill of 2024
logs filters /events/<=2020 (matching what 2024-era operators would
have considered "old"), not /events/<=2023 (today's cutoff).
"""
import sys, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _hz():
    import importlib, hzmetrics
    return importlib.reload(hzmetrics)


class RegisterFilterTests(unittest.TestCase):
    """`/register` is filtered when Referer is empty/dash/null, via
    _is_referer_spam.  Real human signups arrive via /login redirect
    or homepage and carry a Referer; the distributed-bot probes don't.
    Empty-Referer gate preserves the legitimate signup signal while
    dropping the 99 % bot volume."""

    def setUp(self) -> None:
        self.hz = _hz()

    # --- positive cases: empty Referer means spam --------------------

    def test_register_bare_empty_referer_is_spam(self):
        self.assertTrue(self.hz._is_referer_spam("/register", ""))

    def test_register_with_slash_empty_referer_is_spam(self):
        self.assertTrue(self.hz._is_referer_spam("/register/", ""))

    def test_register_with_query_empty_referer_is_spam(self):
        self.assertTrue(self.hz._is_referer_spam("/register?ref=foo", ""))

    def test_register_with_slash_and_query_empty_referer_is_spam(self):
        self.assertTrue(self.hz._is_referer_spam("/register/?ref=foo", ""))

    def test_register_dash_referer_is_spam(self):
        self.assertTrue(self.hz._is_referer_spam("/register", "-"))

    def test_register_null_referer_is_spam(self):
        self.assertTrue(self.hz._is_referer_spam("/register", None))

    # --- negative cases: real-user signup attempts must be kept ------

    def test_register_with_internal_referer_is_kept(self):
        # User clicked through from the login page — real signup.
        self.assertFalse(self.hz._is_referer_spam(
            "/register", "https://geodynamics.org/login"))

    def test_register_with_external_referer_is_kept(self):
        # User clicked from a partner site or search result — real.
        self.assertFalse(self.hz._is_referer_spam(
            "/register", "https://example.org/getting-started"))

    # --- false-positive guards: similarly-named paths must NOT match -----

    def test_registered_re_does_not_match(self):
        # Hypothetical /registered or /register-info etc — must not match.
        self.assertFalse(self.hz._REGISTER_RE.match("/registeredusers"))
        self.assertFalse(self.hz._REGISTER_RE.match("/register-info"))

    def test_resources_register_re_does_not_match(self):
        # Anything under /resources/ — even if its path contained
        # "register" — must not match _REGISTER_RE.
        self.assertFalse(self.hz._REGISTER_RE.match("/resources/register-page"))

    # --- structural: regex pinned -----------------------------------------

    def test_register_re_matches_both_slash_variants(self):
        self.assertTrue(self.hz._REGISTER_RE.match("/register"))
        self.assertTrue(self.hz._REGISTER_RE.match("/register/"))
        self.assertTrue(self.hz._REGISTER_RE.match("/register?x=1"))


class EventsArchiveFilterTests(unittest.TestCase):
    """`/events/<year>` is filtered when:
      - referer is empty/dash/null AND
      - <year> <= datestamp_year - _EVENTS_ARCHIVE_LOOKBACK_YEARS

    Date math reads the log line's datestamp, not date.today() — so a
    backfill is consistent with its own era."""

    def setUp(self) -> None:
        self.hz = _hz()

    # --- positive cases: old year + empty Referer ---------------------

    def test_2007_log_2026_is_archive(self):
        # 2007 << 2026 - 3 = 2023 — drop.
        self.assertTrue(self.hz._is_archive_events_crawl(
            "/events/2007/", datestamp="2026-05-15", referrer=""))

    def test_archive_at_exact_cutoff_year_is_dropped(self):
        # year == log_year - 3 — boundary is inclusive (<=).
        self.assertTrue(self.hz._is_archive_events_crawl(
            "/events/2023/", datestamp="2026-05-15", referrer=""))

    def test_archive_with_query_is_dropped(self):
        self.assertTrue(self.hz._is_archive_events_crawl(
            "/events/2010?tab=schedule", datestamp="2026-05-15", referrer=""))

    def test_archive_dash_referer_is_dropped(self):
        self.assertTrue(self.hz._is_archive_events_crawl(
            "/events/2010/", datestamp="2026-05-15", referrer="-"))

    def test_archive_null_referer_is_dropped(self):
        self.assertTrue(self.hz._is_archive_events_crawl(
            "/events/2010/", datestamp="2026-05-15", referrer=None))

    # --- negative cases: recent year, current era --------------------

    def test_current_year_is_kept(self):
        self.assertFalse(self.hz._is_archive_events_crawl(
            "/events/2026/", datestamp="2026-05-15", referrer=""))

    def test_one_year_back_is_kept(self):
        self.assertFalse(self.hz._is_archive_events_crawl(
            "/events/2025/", datestamp="2026-05-15", referrer=""))

    def test_two_years_back_is_kept(self):
        # 2024 > 2026 - 3 = 2023 — keep.
        self.assertFalse(self.hz._is_archive_events_crawl(
            "/events/2024/", datestamp="2026-05-15", referrer=""))

    def test_archive_with_referer_is_kept(self):
        # Real user clicked through to old events from a current page —
        # keep.
        self.assertFalse(self.hz._is_archive_events_crawl(
            "/events/2007/", datestamp="2026-05-15",
            referrer="https://geodynamics.org/"))

    # --- date-from-log-line semantics: critical contract ----------------

    def test_backfill_uses_log_year_not_today(self):
        """A backfill of 2024 logs filters /events/<=2021, NOT /events/<=2023.
        The whole point of date-from-log: archival imports get filtered
        as a 2024-era operator would have judged "old."""""
        # In 2024-era logs, /events/2022 is recent (2022 > 2024-3=2021) — keep.
        self.assertFalse(self.hz._is_archive_events_crawl(
            "/events/2022/", datestamp="2024-03-15", referrer=""))
        # In 2024-era logs, /events/2020 is old (2020 <= 2021) — drop.
        self.assertTrue(self.hz._is_archive_events_crawl(
            "/events/2020/", datestamp="2024-03-15", referrer=""))
        # Even though 2022 today would be "old," we don't apply today's
        # cutoff to historical data — that would over-filter backfills.

    # --- false-positive guards: similar-shaped URLs --------------------

    def test_events_without_year_is_kept(self):
        # /events/details, /events/conference, etc — must NOT match the
        # YYYY regex (only 4-digit numeric).
        self.assertFalse(self.hz._is_archive_events_crawl(
            "/events/details", datestamp="2026-05-15", referrer=""))
        self.assertFalse(self.hz._is_archive_events_crawl(
            "/events/conference/", datestamp="2026-05-15", referrer=""))

    def test_non_4_digit_year_is_kept(self):
        # /events/20 or /events/202 — partial — must NOT match.
        self.assertFalse(self.hz._is_archive_events_crawl(
            "/events/20/", datestamp="2026-05-15", referrer=""))
        self.assertFalse(self.hz._is_archive_events_crawl(
            "/events/202/", datestamp="2026-05-15", referrer=""))

    def test_bad_datestamp_kept(self):
        # Malformed datestamp — fall through to "keep" rather than
        # raising.  Importer should never crash on a degenerate row.
        self.assertFalse(self.hz._is_archive_events_crawl(
            "/events/2007/", datestamp="", referrer=""))
        self.assertFalse(self.hz._is_archive_events_crawl(
            "/events/2007/", datestamp="garbage", referrer=""))

    # --- structural: constants pinned ----------------------------------

    def test_lookback_constant_is_3_years(self):
        # Bump this consciously, not silently — the cutoff drives import
        # volume and the doc-block above _EVENTS_ARCHIVE_RE encodes the
        # rationale.
        self.assertEqual(self.hz._EVENTS_ARCHIVE_LOOKBACK_YEARS, 3)

    def test_events_re_captures_year(self):
        m = self.hz._EVENTS_ARCHIVE_RE.match("/events/2017/")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "2017")

    def test_events_re_requires_4_digits(self):
        # 3-digit, 5-digit, or non-digit subpaths must NOT capture.
        self.assertIsNone(self.hz._EVENTS_ARCHIVE_RE.match("/events/202/"))
        self.assertIsNone(self.hz._EVENTS_ARCHIVE_RE.match("/events/20177/"))
        self.assertIsNone(self.hz._EVENTS_ARCHIVE_RE.match("/events/conf/"))


class ImportApacheGatedCheckTests(unittest.TestCase):
    """Structural assertion that the new filters are wired into
    do_import_apache.  A filter that exists in module scope but isn't
    called by the import path silently does nothing — source-grep so
    the wiring can't quietly drift."""

    def setUp(self) -> None:
        self.src = (Path(REPO) / "hzmetrics.py").read_text()

    def test_register_in_referer_spam(self):
        self.assertIn("_REGISTER_RE.match(url)", self.src,
                      "_is_referer_spam must call _REGISTER_RE.match "
                      "(empty-Referer gated, not unconditional)")

    def test_citations_in_referer_spam(self):
        self.assertIn("_CITATIONS_BROWSE_RE.match(url)", self.src,
                      "_is_referer_spam must call _CITATIONS_BROWSE_RE.match")

    def test_events_check_in_import_chain(self):
        self.assertIn("_is_archive_events_crawl(url, datestamp, referrer)",
                      self.src,
                      "do_import_apache must call _is_archive_events_crawl "
                      "(with the log line's datestamp, NOT date.today())")

    def test_events_skip_counter_present(self):
        self.assertIn("skipped_events = 0", self.src,
                      "do_import_apache must declare skipped_events counter")
        self.assertIn("skipped_events += 1", self.src,
                      "the events filter must bump skipped_events")
        self.assertIn("events={skipped_events}", self.src,
                      "import-apache summary line must report events count "
                      "so an operator sees the filter taking effect")


if __name__ == "__main__":
    unittest.main(verbosity=2)
