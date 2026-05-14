#!/bin/bash
# A/B compare legacy xlogimport_tool_and_reg_user_data.php vs new
# hzmetrics.py import-hub-data.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

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
    dump_full sessionlog_metrics  "$METRICS_DB" "sessnum"   > "$OUT/${label}_sessionlog.tsv"
    dump_full jos_xprofiles_metrics "$METRICS_DB" "uidNumber" > "$OUT/${label}_xprofiles.tsv"
    echo "  wrote $OUT/${label}_{sessionlog,xprofiles}.tsv"
}

run_side legacy run_legacy_php xlogimport_tool_and_reg_user_data.php
run_side new    run_new        import-hub-data

echo
fail=0
for t in sessionlog xprofiles; do
    echo "=== diff ($t): legacy vs new ==="
    if diff -u "$OUT/legacy_${t}.tsv" "$OUT/new_${t}.tsv"; then
        echo "  PASS"
    else
        echo "  FAIL"
        fail=1
    fi
done

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
