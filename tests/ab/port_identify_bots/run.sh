#!/bin/bash
# A/B compare legacy xlogfix_identify_bots.php vs new hzmetrics.py identify-bots.
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
    dump_full bot_useragents "$METRICS_DB" "useragent" \
        > "$OUT/${label}_bots.tsv"
    echo "  wrote $OUT/${label}_bots.tsv ($(wc -l < $OUT/${label}_bots.tsv) row(s))"
}

run_side legacy run_legacy_php "import/xlogfix_identify_bots.php" "$LOGFILE"
run_side new    run_new        identify-bots                       "$LOGFILE"

echo
echo "=== diff: legacy vs new ==="
if diff -u "$OUT/legacy_bots.tsv" "$OUT/new_bots.tsv"; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
