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
# after the 2025-06 traffic analysis).  These are deliberate divergences
# — fixes for legacy filter holes that were dropping unwanted bot/asset
# traffic into `web`.  Filter both sides to the rows we still care about
# matching exactly, then diff.  Same pattern as port_import_auth's
# action-filter exclusion.
filter_keepers() {
    grep -vE '(/cron/tick|/pipermail/|Scrapy/|PRTG Network Monitor|PycURL/|Yeti/)' "$1"
}
filter_keepers "$OUT/legacy_web.tsv" > "$OUT/legacy_web_filtered.tsv"
filter_keepers "$OUT/new_web.tsv"    > "$OUT/new_web_filtered.tsv"

echo
echo "=== diff: legacy vs new  (intentional-divergence rows filtered) ==="
if diff -u "$OUT/legacy_web_filtered.tsv" "$OUT/new_web_filtered.tsv"; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
