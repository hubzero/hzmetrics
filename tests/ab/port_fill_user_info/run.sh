#!/bin/bash
# A/B compare legacy xlogfix_user_info.php vs new hzmetrics.py fill-user-info.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

TABLE="${1:-toolstart}"
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
    dump_table_tsv "$TABLE" "$OUT/${label}_after_${TABLE}.tsv"
    echo "  wrote $OUT/${label}_after_${TABLE}.tsv"
}

run_side legacy run_legacy_php xlogfix_user_info.php metrics "$TABLE" "$MONTH"
run_side new    run_new        fill-user-info         metrics "$TABLE" "$MONTH"

echo
echo "=== diff: legacy vs new ==="
if diff -u "$OUT/legacy_after_${TABLE}.tsv" "$OUT/new_after_${TABLE}.tsv"; then
    echo "PASS — outputs identical"
    exit 0
else
    echo "FAIL — outputs differ"
    exit 1
fi
