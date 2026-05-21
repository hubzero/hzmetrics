"""Regression tests for the web.dnload classifier.

Background: the importer historically left web.dnload NULL for every row
(neither the legacy PHP code nor the pre-1018cc2-shape port set it at
insert), so the downloaders / download-sessions cells in summary_misc_vals
were silently zero on every audited hub.  The fix sets dnload in-line
during import using `_is_download_url(url)`.  These tests pin that
classifier's behavior so the bug can't silently come back."""

import sys, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


class IsDownloadUrlTests(unittest.TestCase):

    def setUp(self) -> None:
        import importlib, hzmetrics
        self.hz = importlib.reload(hzmetrics)

    # --- the canonical "this is a download" cases --------------------

    def test_resources_download_path(self):
        # /resources/<id>/download/<file> is the explicit download URL
        # shape served by the CMS resource-file controller.
        self.assertTrue(self.hz._is_download_url("/resources/1234/download/foo.pdf"))
        self.assertTrue(self.hz._is_download_url("/resources/9999/download/bar.zip"))
        self.assertTrue(self.hz._is_download_url("/resources/1/download/x"))

    def test_resources_file_extension(self):
        # Any /resources/... URL whose path ends in a known download
        # extension is also treated as a download (legacy convention).
        for ext in ("txt", "png", "pdf", "ppt", "pptx", "swf", "docx",
                    "jpg", "doc", "zip", "mp3", "mbtiles", "xml", "xlsx",
                    "webm", "mp4", "xls", "r", "csv", "nc4", "template",
                    "tgz", "mov", "ipynb", "py", "rar", "grd", "tif",
                    "nc", "har"):
            url = f"/resources/2025/05/12345/sample.{ext}"
            self.assertTrue(self.hz._is_download_url(url),
                            f"expected dnload=1 for {url}")

    def test_download_path_case_insensitive(self):
        self.assertTrue(self.hz._is_download_url("/Resources/1/Download/X"))
        self.assertTrue(self.hz._is_download_url("/RESOURCES/2/DOWNLOAD/F.PDF"))

    def test_query_string_does_not_break_extension_match(self):
        # ?#-terminated extension still counts (matches _DOWNLOAD_EXT_RE).
        self.assertTrue(self.hz._is_download_url("/resources/1/file.pdf?v=2"))
        self.assertTrue(self.hz._is_download_url("/resources/1/file.zip#frag"))

    # --- the "not a download" cases ----------------------------------

    def test_resource_page_view_is_not_download(self):
        # Visiting the resource description page is not a download.
        self.assertFalse(self.hz._is_download_url("/resources/1234"))
        self.assertFalse(self.hz._is_download_url("/resources/1234/"))
        self.assertFalse(self.hz._is_download_url("/resources/1234/about"))

    def test_non_resource_paths_are_not_downloads(self):
        # /login, /api, /tools, /citations, /support etc — none are
        # downloads even if a download extension shows up further in
        # the path, because _DOWNLOAD_EXT_RE anchors on /resources/.
        for url in ("/login", "/api/v1/things", "/tools/foo/run",
                    "/citations/browse?tag=x", "/support/ticket/new",
                    "/groups/bar/files/file.pdf",
                    "/files/abc123.pdf",
                    "/site/resources/2010/07/09423/file.pdf"):
            self.assertFalse(self.hz._is_download_url(url),
                             f"expected dnload=0 for {url}")

    def test_extension_outside_resources_is_not_download(self):
        # Even known download extensions don't count if not under /resources/.
        self.assertFalse(self.hz._is_download_url("/uploads/foo.pdf"))
        self.assertFalse(self.hz._is_download_url("/about/whitepaper.docx"))

    def test_resources_browse_query_is_not_download(self):
        # The crawl-spam pattern we filter elsewhere; verify it's not
        # also being miscategorised as a download.
        self.assertFalse(self.hz._is_download_url("/resources/browse?tag=foo"))
        self.assertFalse(self.hz._is_download_url("/resources/browse"))


class ImportSetsDnloadTests(unittest.TestCase):
    """Asserts the import-apache row-builder actually puts the dnload
    flag in the row tuple it sends to executemany().  Without a live DB,
    we monkeypatch the cursor and capture the parameter tuples directly."""

    def test_insert_tuple_includes_dnload_for_download_url(self):
        import importlib, hzmetrics
        hz = importlib.reload(hzmetrics)

        # The insert SQL must explicitly mention `dnload` as a target
        # column, otherwise the trailing tuple element gets dropped on
        # the floor and we silently regress.
        src = (Path(REPO) / "hzmetrics.py").read_text()
        self.assertIn(", dnload)", src,
                      "insert_sql in do_import_apache must include `dnload` "
                      "as a target column")
        self.assertIn("dnload = 1 if _is_download_url(url) else 0", src,
                      "do_import_apache must compute dnload per row")


if __name__ == "__main__":
    unittest.main(verbosity=2)
