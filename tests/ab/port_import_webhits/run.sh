#!/bin/bash
# A/B compare legacy xlogimport_webhits.php vs new hzmetrics.py import-webhits.
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
    dump_full webhits "$METRICS_DB" "datetime" > "$OUT/${label}_webhits.tsv"
    echo "  wrote $OUT/${label}_webhits.tsv"
}

run_side legacy run_legacy_php "import/xlogimport_webhits.php" "$LOGFILE"
run_side new    run_new        import-webhits                  "$LOGFILE"

echo
echo "=== diff: legacy vs new ==="
if diff -u "$OUT/legacy_webhits.tsv" "$OUT/new_webhits.tsv"; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
