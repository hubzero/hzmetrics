"""Regression tests for the empty-Referer crawler-pattern filter.

Background: import-apache drops a row when its URL matches one of two
patterns AND the Referer header is empty/dash/null:

  - /login?return=<base64>  (auth-redirect spam)
  - /resources/browse?<q>   (catalog crawl)

Slash variants (`/login/?return=`, `/resources/browse/?...`) route to
the same CMS actions and are hit by the same crawlers, so the regexes
allow an optional trailing slash before the `?`.  The 2023-12 audit
found ~200 k slash-variant rows that the no-slash form had been
missing; these tests pin the broader regex so the variant gap can't
silently come back.
"""
import sys, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def _hz():
    import importlib, hzmetrics
    return importlib.reload(hzmetrics)


class IsRefererSpamTests(unittest.TestCase):

    def setUp(self) -> None:
        self.hz = _hz()

    # --- login?return= positive cases (empty referer) ----------------

    def test_login_no_slash_empty_referer_is_spam(self):
        self.assertTrue(self.hz._is_referer_spam(
            "/login?return=L3Jlc291cmNlcw==", referrer=""))

    def test_login_with_slash_empty_referer_is_spam(self):
        # The variant the 2023-12 audit caught — must now be filtered.
        self.assertTrue(self.hz._is_referer_spam(
            "/login/?return=L3Jlc291cmNlcw==", referrer=""))

    def test_login_null_referer_is_spam(self):
        self.assertTrue(self.hz._is_referer_spam(
            "/login?return=x", referrer=None))

    def test_login_dash_referer_is_spam(self):
        # Apache logs use "-" as the no-Referer sentinel.
        self.assertTrue(self.hz._is_referer_spam(
            "/login?return=x", referrer="-"))

    # --- /resources/browse positive cases ----------------------------

    def test_browse_no_slash_empty_referer_is_spam(self):
        self.assertTrue(self.hz._is_referer_spam(
            "/resources/browse?tag=foo", referrer=""))

    def test_browse_with_slash_empty_referer_is_spam(self):
        # Same slash-variant issue, caught by the 2023-12 audit.
        self.assertTrue(self.hz._is_referer_spam(
            "/resources/browse/?tag=foo", referrer=""))

    def test_browse_with_multiple_params_with_slash(self):
        self.assertTrue(self.hz._is_referer_spam(
            "/resources/browse/?limit=50&sortby=date&tag=cig",
            referrer=""))

    # --- negative cases: real users with Referer ---------------------

    def test_login_with_referer_is_not_spam(self):
        self.assertFalse(self.hz._is_referer_spam(
            "/login?return=x",
            referrer="https://geodynamics.org/resources/123"))

    def test_browse_with_referer_is_not_spam(self):
        self.assertFalse(self.hz._is_referer_spam(
            "/resources/browse?tag=foo",
            referrer="https://geodynamics.org/resources"))

    def test_browse_slash_variant_with_referer_is_not_spam(self):
        self.assertFalse(self.hz._is_referer_spam(
            "/resources/browse/?tag=foo",
            referrer="https://geodynamics.org/"))

    # --- negative cases: URL doesn't match either pattern ------------

    def test_resource_page_view_is_not_spam(self):
        # /resources/123 (not the browse search) — keep regardless of
        # referer state.
        self.assertFalse(self.hz._is_referer_spam(
            "/resources/123", referrer=""))

    def test_login_without_return_param_is_not_spam(self):
        # Bare /login or /login/ — actual login page hits should not
        # be filtered.
        self.assertFalse(self.hz._is_referer_spam("/login",  referrer=""))
        self.assertFalse(self.hz._is_referer_spam("/login/", referrer=""))

    def test_browse_without_query_is_not_spam(self):
        # Bare /resources/browse — the catalog landing page hit by
        # real users, not the tag-spam form.
        self.assertFalse(self.hz._is_referer_spam(
            "/resources/browse",  referrer=""))
        self.assertFalse(self.hz._is_referer_spam(
            "/resources/browse/", referrer=""))

    # --- structural: regexes themselves --------------------------------

    def test_login_re_accepts_both_slash_variants(self):
        # Direct test on the compiled regex so a hand-edit that
        # accidentally pins the no-slash form gets caught.
        self.assertTrue(self.hz._LOGIN_RETURN_RE.match("/login?return=x"))
        self.assertTrue(self.hz._LOGIN_RETURN_RE.match("/login/?return=x"))

    def test_browse_re_accepts_both_slash_variants(self):
        self.assertTrue(self.hz._BROWSE_QUERY_RE.match("/resources/browse?tag=x"))
        self.assertTrue(self.hz._BROWSE_QUERY_RE.match("/resources/browse/?tag=x"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
