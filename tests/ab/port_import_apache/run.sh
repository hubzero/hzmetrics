#!/bin/bash
# A/B compare legacy xlogimport_apache.php vs new hzmetrics.py import-apache.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"
LOGFILE="$AB/fixtures/sample_apache.log"

run_side() {
    local label="$1" invoker="$2"; shift 2
    echo
    echo "=== $label: $* ==="
    reset_test_dbs
    "$invoker" "$@" > "$OUT/${label}_stdout.log" 2>&1 || {
        echo "  $label invocation failed; log:"
        cat "$OUT/${label}_stdout.log"
        return 1
    }
    # Every column of web minus the auto-inc id.  Both NEW and OLD apache
    # log patterns populate (or leave empty) different columns; SELECT *
    # surfaces any format-dispatch divergence.
    dump_full web "$METRICS_DB" "datetime, ip, content" > "$OUT/${label}_web.tsv"
    echo "  wrote $OUT/${label}_web.tsv ($(wc -l < $OUT/${label}_web.tsv) row(s))"
}

run_side legacy run_legacy_php "import/xlogimport_apache.php" "$LOGFILE"
run_side new    run_new        import-apache                  "$LOGFILE"

# The new import-apache filters a handful of patterns the legacy regex
# missed (notably /cron/tick with no trailing slash, and the
# Scrapy/PRTG/PycURL/Yeti bot UAs and /pipermail/ archive paths added
# after the 2025-06 traffic analysis).  The 2025-05 survey added two
# more: /login?return=<x> and /resources/browse?<q> with empty Referer
# — legacy can't see Referer so it has no way to drop them, but the
# new code does.  These are deliberate divergences — fixes for legacy
# filter holes that were dropping unwanted bot/asset traffic into
# `web`.  Filter both sides to the rows we still care about matching
# exactly, then diff.  Same pattern as port_import_auth's action-filter
# exclusion.
filter_keepers() {
    grep -vE '(/cron/tick|/pipermail/|/login\?return=|/resources/browse\?|Scrapy/|PRTG Network Monitor|PycURL/|Yeti/)' "$1"
}
filter_keepers "$OUT/legacy_web.tsv" > "$OUT/legacy_web_filtered.tsv"
filter_keepers "$OUT/new_web.tsv"    > "$OUT/new_web_filtered.tsv"

echo
echo "=== diff: legacy vs new  (intentional-divergence rows filtered) ==="
if ! diff -u "$OUT/legacy_web_filtered.tsv" "$OUT/new_web_filtered.tsv"; then
    echo "FAIL: legacy/new diff"
    exit 1
fi

echo
echo "=== dnload sanity check on new-side import ==="
# Regression guard for the long-standing bug where web.dnload stayed NULL
# on every row because neither the legacy importer nor the pre-1018cc2-shape
# port set it at insert.  Result was silently-zero downloaders /
# download-sessions cells in summary_misc_vals.  Assert structural invariants
# now that the new importer sets dnload in-line.
download_url_rows=$(mysql_test "$METRICS_DB" -BN -e \
    "SELECT COUNT(*) FROM web WHERE content LIKE '/resources/%/download/%'")
dnload_set=$(mysql_test "$METRICS_DB" -BN -e \
    "SELECT COUNT(*) FROM web WHERE dnload = 1")
dnload_null=$(mysql_test "$METRICS_DB" -BN -e \
    "SELECT COUNT(*) FROM web WHERE dnload IS NULL")
dnload_set_but_no_match=$(mysql_test "$METRICS_DB" -BN -e \
    "SELECT COUNT(*) FROM web
       WHERE dnload = 1
         AND content NOT LIKE '/resources/%/download/%'
         AND content NOT REGEXP '^/resources/.*\\\\.(txt|png|pdf|ppt|pptx|swf|docx|jpg|doc|zip|mp3|mbtiles|xml|xlsx|webm|mp4|xls|r|csv|nc4|template|tgz|mov|ipynb|py|rar|grd|tif|nc|har)([?#]|\$)'")
echo "  /resources/X/download/ rows in web : $download_url_rows  (fixture has 2)"
echo "  rows with dnload=1                  : $dnload_set"
echo "  rows with dnload IS NULL            : $dnload_null"
echo "  dnload=1 but no download URL match  : $dnload_set_but_no_match"

fail=0
if [ "$download_url_rows" -lt 1 ]; then
    echo "FAIL: fixture is missing /resources/.../download/ rows — test is no longer meaningful"
    fail=1
fi
if [ "$dnload_set" -lt "$download_url_rows" ]; then
    echo "FAIL: only $dnload_set rows have dnload=1 but at least $download_url_rows download URLs were imported"
    fail=1
fi
if [ "$dnload_null" -ne 0 ]; then
    echo "FAIL: $dnload_null rows have dnload IS NULL — importer regressed to leaving the flag unset"
    fail=1
fi
if [ "$dnload_set_but_no_match" -ne 0 ]; then
    echo "FAIL: $dnload_set_but_no_match rows have dnload=1 but content doesn't match any download pattern"
    fail=1
fi

if [ "$fail" -eq 0 ]; then
    echo "PASS"
    exit 0
else
    exit 1
fi
