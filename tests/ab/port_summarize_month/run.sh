#!/bin/bash
# A/B compare legacy xlogfix_summary.php vs new hzmetrics.py summarize-month.
# Diffs all three summary_*_vals tables.  Floats rounded to 6 digits to
# absorb the PHP↔Python double-stringification noise.
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
        echo "  $label invocation failed (tail of log):"
        tail -20 "$OUT/${label}_stdout.log"
        return 1
    }
    dump_full summary_user_vals     "$METRICS_DB" "rowid, colid, period" > "$OUT/${label}_user.tsv"
    dump_full summary_simusage_vals "$METRICS_DB" "rowid, colid, period" > "$OUT/${label}_simusage.tsv"
    dump_full summary_misc_vals     "$METRICS_DB" "rowid, colid, period" > "$OUT/${label}_misc.tsv"
    echo "  wrote {user,simusage,misc} TSVs"
}

run_side legacy run_legacy_php xlogfix_summary.php "$MONTH"
run_side new    run_new        summarize-month     "$MONTH"

echo
fail=0
for t in user simusage misc; do
    echo "=== diff (summary_${t}_vals) ==="
    if diff -u "$OUT/legacy_${t}.tsv" "$OUT/new_${t}.tsv"; then
        echo "  PASS"
    else
        echo "  FAIL"
        fail=1
    fi
done

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
