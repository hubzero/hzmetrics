"""Regression tests for `_filter_apache_row` — the shared per-row
filter chain that both do_import_apache and do_import_webhits invoke.

Background: a 3-month audit (2026-02 .. 2026-05) found `webhits` was
applying only the legacy `exclude_list` substring filters, while
`web` had grown bot_useragents exact-match + MSIE-Trident +
_is_excluded_url + _is_referer_spam + _is_archive_events_crawl on top.
Summarize reads SUM(hits) from webhits for the "Web server hits"
cell (rowid=8 of summary_misc_vals — see xlogfix_summary.php), so
the drift inflated that dashboard cell relative to everything else
in the same row-set (sessions, downloads, etc., all derived from
websessions which is built from filtered web rows).

The fix is structural: the filter chain lives in one place
(_filter_apache_row) and both importers call it.  These tests
pin the per-row contract AND the source-grep wiring on both
importers, so a future commit that adds a filter to one but not
the other will be caught.
"""
import sys, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _hz():
    import importlib, hzmetrics
    return importlib.reload(hzmetrics)


def _row(hz, **overrides):
    """Build a `_filter_apache_row` kwargs dict with sensible defaults
    (a normal, would-be-kept row) and apply overrides."""
    defaults = dict(
        return_code='200', bytes_str='1000', method='GET',
        ip='1.2.3.4', useragent='Mozilla/5.0 Chrome/120',
        url='/resources/123', referrer='https://geodynamics.org/',
        datestamp='2026-05-29',
        ip_filters=[], ua_filters=[], url_filters=[],
        bot_uas=set(),
    )
    defaults.update(overrides)
    return hz._filter_apache_row(**defaults)


class FilterChainKeepTests(unittest.TestCase):
    """Rows that should pass through cleanly — `(True, None)`."""

    def setUp(self) -> None:
        self.hz = _hz()

    def test_normal_resource_view_with_referer_is_kept(self):
        self.assertEqual(_row(self.hz), (True, None))

    def test_post_method_is_kept(self):
        self.assertEqual(_row(self.hz, method='POST'), (True, None))

    def test_under_resources_overrides_excluded_suffix(self):
        # /resources/ has a special override in the chain — even when
        # the URL would otherwise match _is_excluded_url's suffix
        # rules, /resources/* paths are kept (download tracking).
        self.assertEqual(_row(self.hz, url='/resources/123/download/file.pdf'),
                         (True, None))


class FilterChainDropTests(unittest.TestCase):
    """Each filter reason produces a distinct, named drop signal so the
    apache-side caller can bucket skip counts."""

    def setUp(self) -> None:
        self.hz = _hz()

    # --- status / method ----------------------------------------------

    def test_non_200_is_status_drop(self):
        self.assertEqual(_row(self.hz, return_code='404'), (False, 'status'))

    def test_zero_bytes_is_status_drop(self):
        self.assertEqual(_row(self.hz, bytes_str='0'), (False, 'status'))

    def test_negative_bytes_is_status_drop(self):
        self.assertEqual(_row(self.hz, bytes_str='-1'), (False, 'status'))

    def test_non_numeric_bytes_is_status_drop(self):
        self.assertEqual(_row(self.hz, bytes_str='abc'), (False, 'status'))

    def test_put_method_is_status_drop(self):
        self.assertEqual(_row(self.hz, method='PUT'), (False, 'status'))

    # --- exclude_list substring filters -------------------------------

    def test_ip_substring_filter_is_filter_drop(self):
        self.assertEqual(_row(self.hz, ip='5.6.7.8', ip_filters=['5.6.7.']),
                         (False, 'filter'))

    def test_useragent_substring_filter_is_filter_drop(self):
        self.assertEqual(_row(self.hz, useragent='Mozilla/5.0 SomeBot/1.0',
                              ua_filters=['SomeBot']),
                         (False, 'filter'))

    def test_url_substring_filter_is_filter_drop(self):
        self.assertEqual(_row(self.hz, url='/some/blocked/path',
                              url_filters=['/blocked/']),
                         (False, 'filter'))

    # --- bot_useragents exact match -----------------------------------

    def test_bot_useragent_exact_match_is_bot_drop(self):
        self.assertEqual(_row(self.hz, useragent='KnownBot/1.0',
                              bot_uas={'KnownBot/1.0'}),
                         (False, 'bot'))

    def test_dash_useragent_skips_bot_check(self):
        # '-' is the apache "no UA" sentinel; bot_useragents exact match
        # should not be applied to it (would false-positive if '-' were
        # ever in the bot table).
        self.assertEqual(_row(self.hz, useragent='-', bot_uas={'-'}),
                         (True, None))

    # --- MSIE-Trident date-bound regex --------------------------------

    def test_msie_in_2026_log_is_msie_drop(self):
        self.assertEqual(_row(
            self.hz,
            useragent='Mozilla/5.0 (compatible; MSIE 9.0; Trident/5.0)',
            datestamp='2026-05-29'),
            (False, 'msie'))

    def test_msie_in_pre_2022_log_is_kept(self):
        # Date-bound: archival backfills of pre-EOL access logs are NOT
        # retroactively filtered.
        ok, _ = _row(
            self.hz,
            useragent='Mozilla/5.0 (compatible; MSIE 9.0; Trident/5.0)',
            datestamp='2021-12-31')
        self.assertTrue(ok)

    # --- _is_excluded_url --------------------------------------------

    def test_admin_path_is_url_drop(self):
        self.assertEqual(_row(self.hz, url='/administrator/manager'),
                         (False, 'url'))

    def test_pipermail_is_url_drop(self):
        self.assertEqual(_row(self.hz, url='/pipermail/list-name/2024-01/'),
                         (False, 'url'))

    # --- referer-spam (login/browse/citations/register) --------------

    def test_login_return_empty_referer_is_ref_drop(self):
        self.assertEqual(_row(self.hz, url='/login?return=x', referrer=''),
                         (False, 'ref'))

    def test_register_empty_referer_is_ref_drop(self):
        self.assertEqual(_row(self.hz, url='/register', referrer=''),
                         (False, 'ref'))

    def test_citations_browse_empty_referer_is_ref_drop(self):
        self.assertEqual(_row(self.hz, url='/citations/browse?p=2', referrer=''),
                         (False, 'ref'))

    def test_register_with_referer_is_kept(self):
        # Real signup arrives via /login redirect — Referer set, keep.
        self.assertEqual(_row(self.hz, url='/register',
                              referrer='https://geodynamics.org/login'),
                         (True, None))

    # --- archive-events crawl (date-bound) ---------------------------

    def test_old_events_in_2026_log_is_events_drop(self):
        self.assertEqual(_row(self.hz, url='/events/2007/',
                              referrer='', datestamp='2026-05-29'),
                         (False, 'events'))

    def test_current_events_in_2026_log_is_kept(self):
        ok, _ = _row(self.hz, url='/events/2026/',
                     referrer='', datestamp='2026-05-29')
        self.assertTrue(ok)


class FilterChainOrderTests(unittest.TestCase):
    """The reason field is used to bucket skip counters — when a row
    would be caught by multiple filters, the earliest-firing one wins.
    Pinning the order so a refactor can't silently re-order the chain
    (e.g. by accidentally checking the URL before status code)."""

    def setUp(self) -> None:
        self.hz = _hz()

    def test_status_check_precedes_url_check(self):
        # A 404 to /administrator/ — status drop, not url drop.
        self.assertEqual(
            _row(self.hz, return_code='404', url='/administrator/x'),
            (False, 'status'))

    def test_bot_check_precedes_msie_check(self):
        # If a UA is BOTH in bot_useragents AND matches the MSIE
        # regex, it should be reported as 'bot' (the exact-match
        # check fires first).
        msie_ua = 'Mozilla/5.0 (compatible; MSIE 9.0; Trident/5.0)'
        self.assertEqual(
            _row(self.hz, useragent=msie_ua, bot_uas={msie_ua},
                 datestamp='2026-05-29'),
            (False, 'bot'))

    def test_url_check_precedes_ref_check(self):
        # /administrator/?return=x has both _is_excluded_url match
        # (administrator) and would NOT match _is_referer_spam patterns.
        # Verify 'url' wins, not 'ref'.
        self.assertEqual(
            _row(self.hz, url='/administrator/login?return=x', referrer=''),
            (False, 'url'))


class WiringTests(unittest.TestCase):
    """Both importers must call _filter_apache_row.  Source-grep so
    a future refactor that adds an inline filter to ONE side without
    going through the helper gets caught."""

    def setUp(self) -> None:
        self.src = (Path(REPO) / "hzmetrics.py").read_text()

    def test_apache_importer_calls_shared_helper(self):
        # Locate do_import_apache body and assert it calls _filter_apache_row.
        i = self.src.index("def do_import_apache(")
        j = self.src.index("\ndef ", i + 1)
        body = self.src[i:j]
        self.assertIn("_filter_apache_row(", body,
                      "do_import_apache must route per-row decisions "
                      "through _filter_apache_row")

    def test_webhits_importer_calls_shared_helper(self):
        i = self.src.index("def do_import_webhits(")
        j = self.src.index("\ndef ", i + 1)
        body = self.src[i:j]
        self.assertIn("_filter_apache_row(", body,
                      "do_import_webhits must route per-row decisions "
                      "through _filter_apache_row — the 2026-02 audit "
                      "found webhits was diverging by applying only "
                      "exclude_list substring filters")

    def test_webhits_loads_bot_useragents(self):
        # The shared helper requires bot_uas; webhits must load it.
        i = self.src.index("def do_import_webhits(")
        j = self.src.index("\ndef ", i + 1)
        body = self.src[i:j]
        self.assertIn("bot_useragents", body,
                      "do_import_webhits must load bot_useragents to "
                      "pass into _filter_apache_row")

    def test_webhits_parses_referrer(self):
        # The shared helper needs referrer for the /register, /events
        # archive, and /citations/browse filters.  webhits never parsed
        # it before this commit — pin that it does now.
        i = self.src.index("def do_import_webhits(")
        j = self.src.index("\ndef ", i + 1)
        body = self.src[i:j]
        self.assertIn("referrer = m.group(10)", body,
                      "do_import_webhits must extract referrer from "
                      "_APACHE_PAT_NEW (group 10)")
        self.assertIn("referrer = m.group(9)", body,
                      "do_import_webhits must extract referrer from "
                      "_APACHE_PAT_OLD (group 9)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
