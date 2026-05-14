#!/bin/bash
# A/B compare legacy xlogfix_andmore_usage.php vs new hzmetrics.py andmore-usage.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

MONTH="${1:-2025-07}"

run_side() {
    local label="$1" invoker="$2"; shift 2
    echo
    echo "=== $label: $* ==="
    reset_test_dbs
    load_fixture "$DIR/seed.sql"
    "$invoker" "$@" > "$OUT/${label}_stdout.log" 2>&1 || {
        echo "  $label invocation failed; log:"
        cat "$OUT/${label}_stdout.log"
        return 1
    }
    mysql_test "$HUB_DB" -BN -e "
        SELECT resid, restype, users, datetime, period
        FROM jos_resource_stats ORDER BY resid, period
    " > "$OUT/${label}_stats.tsv"
    echo "  wrote $OUT/${label}_stats.tsv"
}

run_side legacy run_legacy_php xlogfix_andmore_usage.php "$MONTH"
run_side new    run_new        andmore-usage             "$MONTH"

echo
echo "=== diff: legacy vs new ==="
if diff -u "$OUT/legacy_stats.tsv" "$OUT/new_stats.tsv"; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
