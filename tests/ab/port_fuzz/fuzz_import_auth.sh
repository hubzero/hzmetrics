#!/bin/bash
# Fuzz import-auth: generate random CMS auth log lines covering both
# regex patterns and garbage, assert legacy + new parse identically.
# Usage: fuzz_import_auth.sh [iter [lines [seed_base]]]
# Defaults: 30 iter × 200 lines = 6000 cases per run.

set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

ITERS="${1:-30}"
LINES="${2:-200}"
SEED_BASE="${3:-$(date +%s)}"
OUT="$DIR/_out_auth"
mkdir -p "$OUT"

echo "fuzz_import_auth: $ITERS iter × $LINES lines each, seed_base=$SEED_BASE"
echo

passed=0
for i in $(seq 1 "$ITERS"); do
    seed=$((SEED_BASE + i))
    log_file="$OUT/seed_${seed}.log"
    "$PY" "$DIR/gen_auth_log.py" "$LINES" "$seed" > "$log_file"

    reset_test_dbs > /dev/null
    run_legacy_php import/xlogimport_authlog.php "$log_file" \
        > "$OUT/seed_${seed}_legacy.log" 2>&1
    dump_full userlogin "$METRICS_DB" "datetime, user, ip, action" \
        > "$OUT/seed_${seed}_legacy.tsv"

    reset_test_dbs > /dev/null
    run_new import-auth "$log_file" \
        > "$OUT/seed_${seed}_new.log" 2>&1
    dump_full userlogin "$METRICS_DB" "datetime, user, ip, action" \
        > "$OUT/seed_${seed}_new.tsv"

    # Same A/B-divergence handling as port_import_auth: new code
    # filters action ∈ (login, simulation) at insert time; legacy
    # keeps every action.  Pipeline only ever queries the kept
    # actions, so filter both sides to those rows before diffing.
    # dump_full userlogin columns: datetime user uidNumber ip action.
    awk -F'\t' '$5 == "login" || $5 == "simulation"' \
        "$OUT/seed_${seed}_legacy.tsv" > "$OUT/seed_${seed}_legacy_filt.tsv"
    awk -F'\t' '$5 == "login" || $5 == "simulation"' \
        "$OUT/seed_${seed}_new.tsv" > "$OUT/seed_${seed}_new_filt.tsv"

    if ! diff -q "$OUT/seed_${seed}_legacy_filt.tsv" "$OUT/seed_${seed}_new_filt.tsv" >/dev/null 2>&1; then
        echo
        echo "FAIL iteration $i  seed=$seed"
        echo "  reproduce: $PY $DIR/gen_auth_log.py $LINES $seed > /tmp/fuzz.log"
        echo "  log: $log_file"
        echo "  first 30 diff lines:"
        diff "$OUT/seed_${seed}_legacy_filt.tsv" "$OUT/seed_${seed}_new_filt.tsv" | sed -n '1,30p' || true
        echo
        echo "  $passed / $i iterations passed before failure"
        exit 1
    fi
    passed=$((passed + 1))
    [ $((passed % 10)) -eq 0 ] && printf '.'
    rm -f "$log_file" \
          "$OUT/seed_${seed}_legacy.tsv" "$OUT/seed_${seed}_new.tsv" \
          "$OUT/seed_${seed}_legacy.log" "$OUT/seed_${seed}_new.log"
done

echo
echo "PASS — all $ITERS iterations × $LINES lines ($(($ITERS * $LINES)) cases) clean"
