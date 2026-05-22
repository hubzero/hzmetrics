#!/bin/bash
# Fuzz import-apache: generate N random apache log lines (mix of
# new-format, old-format, and garbage) and assert legacy + new parse
# them identically.  Targets the regex dispatch, URL filter chain,
# method filter, and excluded-suffix branches.
#
# Usage: fuzz_import_apache.sh [iter [lines [seed_base]]]
# Defaults: 30 iter × 200 lines = 6000 cases per run.

set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

ITERS="${1:-30}"
LINES="${2:-200}"
SEED_BASE="${3:-$(date +%s)}"
OUT="$DIR/_out_apache"
mkdir -p "$OUT"

echo "fuzz_import_apache: $ITERS iter × $LINES lines each, seed_base=$SEED_BASE"
echo

passed=0
for i in $(seq 1 "$ITERS"); do
    seed=$((SEED_BASE + i))
    log_file="$OUT/seed_${seed}.log"
    "$PY" "$DIR/gen_apache_log.py" "$LINES" "$seed" > "$log_file"

    # ── legacy ────────────────────────────────────────────────
    # Exclude `dnload` from the dump: the new import-apache sets it
    # inline at insert time, the legacy import doesn't touch it, so a
    # raw column-by-column diff always disagrees on that one field.
    # The port_import_apache test already excludes dnload for the same
    # reason; pin the same handling here.
    reset_test_dbs > /dev/null
    run_legacy_php import/xlogimport_apache.php "$log_file" \
        > "$OUT/seed_${seed}_legacy.log" 2>&1
    dump_full web "$METRICS_DB" "datetime, ip, content" "dnload" \
        > "$OUT/seed_${seed}_legacy_web.tsv"

    # ── new ───────────────────────────────────────────────────
    reset_test_dbs > /dev/null
    run_new import-apache "$log_file" \
        > "$OUT/seed_${seed}_new.log" 2>&1
    dump_full web "$METRICS_DB" "datetime, ip, content" "dnload" \
        > "$OUT/seed_${seed}_new_web.tsv"

    # ── diff ──────────────────────────────────────────────────
    if ! diff -q "$OUT/seed_${seed}_legacy_web.tsv" "$OUT/seed_${seed}_new_web.tsv" >/dev/null 2>&1; then
        echo
        echo "FAIL iteration $i  seed=$seed"
        echo "  reproduce: $PY $DIR/gen_apache_log.py $LINES $seed > /tmp/fuzz.log"
        echo "  log: $log_file"
        echo "  first 30 diff lines:"
        diff "$OUT/seed_${seed}_legacy_web.tsv" "$OUT/seed_${seed}_new_web.tsv" | sed -n '1,30p' || true
        echo
        echo "  $passed / $i iterations passed before failure"
        exit 1
    fi
    passed=$((passed + 1))
    [ $((passed % 10)) -eq 0 ] && printf '.'
    rm -f "$log_file" \
          "$OUT/seed_${seed}_legacy_web.tsv" "$OUT/seed_${seed}_new_web.tsv" \
          "$OUT/seed_${seed}_legacy.log" "$OUT/seed_${seed}_new.log"
done

echo
echo "PASS — all $ITERS iterations × $LINES lines ($(($ITERS * $LINES)) cases) clean"
