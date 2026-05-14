#!/bin/bash
# A/B compare legacy xlogfix_ipcountry.php vs new hzmetrics.py fill-ipcountry.
# Both hit the same external ipinfo HTTP service — if it's reachable,
# both runs should produce identical ipcountry values.  Network-dependent;
# may fail on flaky connectivity.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

TABLE="${1:-web}"
MONTH="${2:-2025-07}"

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
    dump_full web "$METRICS_DB" "datetime, ip, content" > "$OUT/${label}_web.tsv"
    echo "  wrote $OUT/${label}_web.tsv"
}

run_side legacy run_legacy_php xlogfix_ipcountry.php metrics "$TABLE" "$MONTH"
run_side new    run_new        fill-ipcountry         metrics "$TABLE" "$MONTH"

echo
echo "=== diff: legacy vs new ==="
if diff -u "$OUT/legacy_web.tsv" "$OUT/new_web.tsv"; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
