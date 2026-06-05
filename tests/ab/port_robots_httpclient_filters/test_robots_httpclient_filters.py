"""Regression tests for two import-time filter additions from the
2025-05 plantingscience survey of rows that passed every prior filter:

  1. /robots.txt        — bot politeness fetch, served 200-with-bytes so
                          it clears the status/size gate but carries no
                          hub-usage signal (100 % empty-Referer,
                          ~2.7 k hits/day).  Dropped unconditionally by
                          `_is_excluded_url` via `_ROBOTS_RE`; no human
                          navigates to /robots.txt, so unlike the
                          /…/browse patterns it is NOT Referer-gated.

  2. bare HTTP-client UAs — python-requests, python-urllib, curl, wget,
                          Go-http-client, node-fetch, libwww.  These are
                          automated clients with no bot/crawl/spider
                          token, so the older BOT_UA_FILTERS substrings
                          missed them.  All are unambiguous library
                          default UAs with no real-browser overlap.
                          (`okhttp` / `java` are deliberately NOT in the
                          list — they appear in legitimate mobile-app /
                          middleware traffic and need a per-hub call.)

`_is_excluded_url` is the suffix/path drop used by do_import_apache
(gated by `not _RESOURCES_RE.match`); `_ua_is_bot` is the substring
matcher that feeds do_identify_bots → metrics.bot_useragents, which the
import path then drops on exact match.  Both are pure functions, so
these tests pin behavior without touching the DB.

Note `_SLASH_COLLAPSE` folds runs of slashes to one before the filter
chain runs, so the leading-double-slash scanner variants (//robots.txt)
reduce to the canonical form these tests cover.
"""
import sys, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _hz():
    import importlib, hzmetrics
    return importlib.reload(hzmetrics)


class RobotsTxtExclusionTests(unittest.TestCase):

    def setUp(self) -> None:
        self.hz = _hz()

    def test_robots_txt_is_excluded(self):
        self.assertTrue(self.hz._is_excluded_url("/robots.txt"))

    def test_robots_txt_with_query_is_excluded(self):
        self.assertTrue(self.hz._is_excluded_url("/robots.txt?foo=1"))

    def test_robots_txt_with_fragment_is_excluded(self):
        self.assertTrue(self.hz._is_excluded_url("/robots.txt#x"))

    def test_double_slash_robots_folds_then_excluded(self):
        # Scanners hit //robots.txt; _SLASH_COLLAPSE folds it first.
        folded = self.hz._SLASH_COLLAPSE.sub("/", "//robots.txt")
        self.assertEqual(folded, "/robots.txt")
        self.assertTrue(self.hz._is_excluded_url(folded))

    def test_robots_txt_suffix_word_not_overmatched(self):
        # /robots.txtfoo is a different (hypothetical) path — must NOT
        # be swallowed by the robots rule.
        self.assertFalse(self.hz._ROBOTS_RE.match("/robots.txtfoo"))

    def test_robots_subdir_not_excluded_by_robots_rule(self):
        # Only the top-level file is the politeness fetch.
        self.assertFalse(self.hz._ROBOTS_RE.match("/foo/robots.txt"))

    def test_real_content_url_not_excluded(self):
        # Control: a normal resource page is kept.
        self.assertFalse(self.hz._is_excluded_url("/resources/12345"))

    def test_robots_re_anchors(self):
        self.assertTrue(self.hz._ROBOTS_RE.match("/robots.txt"))
        self.assertTrue(self.hz._ROBOTS_RE.match("/robots.txt?x=1"))
        self.assertFalse(self.hz._ROBOTS_RE.match("/robots.txtfoo"))


class HttpClientUaTests(unittest.TestCase):

    def setUp(self) -> None:
        self.hz = _hz()

    def test_programmatic_clients_flagged(self):
        for ua in (
            "python-requests/2.31.0",
            "Python-urllib/3.11",
            "curl/8.4.0",
            "Wget/1.21.3",
            "Go-http-client/1.1",
            "node-fetch/1.0",
            "libwww-perl/6.68",
        ):
            with self.subTest(ua=ua):
                self.assertTrue(self.hz._ua_is_bot(ua))

    def test_curl_subsumes_pycurl(self):
        # The new "curl" entry also matches the pre-existing pycurl case.
        self.assertTrue(self.hz._ua_is_bot("PycURL/7.45.2 libcurl/8.4.0"))

    def test_okhttp_deliberately_not_flagged(self):
        # Held back on purpose — legit mobile-app traffic uses OkHttp.
        self.assertFalse(self.hz._ua_is_bot("okhttp/4.9.3"))

    def test_real_browser_not_flagged(self):
        for ua in (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.1 Safari/605.1.15",
            "Mozilla/5.0 (X11; CrOS x86_64 13597.105.0) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/91.0 Safari/537.36",
        ):
            with self.subTest(ua=ua):
                self.assertFalse(self.hz._ua_is_bot(ua))


if __name__ == "__main__":
    unittest.main(verbosity=2)
