#!/bin/bash
# A/B compare legacy xlogfix_middleware_{wall,cpu}.pl vs new
# hzmetrics.py middleware-{wall,cpu}.  Args: wall|cpu  (default both).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

METRIC="${1:-wall}"   # wall or cpu
TABLE=toolstart

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
    # Dump only the columns the test cares about, deterministically ordered.
    # Each side will pick an auto-increment id that differs between runs
    # (legacy and new INSERT in different orders), so we strip the id and
    # order by (datetime, user, ip).
    mysql_test "$METRICS_DB" -BN -e "
        SELECT datetime, user, ip, tool, execunit, walltime, cputime
        FROM toolstart
        ORDER BY datetime, user, ip
    " > "$OUT/${label}_after_${METRIC}.tsv"
    echo "  wrote $OUT/${label}_after_${METRIC}.tsv"
}

run_side legacy run_legacy_perl "xlogfix_middleware_${METRIC}.pl"
run_side new    run_new          "middleware-${METRIC}"

echo
echo "=== diff: legacy vs new ==="
if diff -u "$OUT/legacy_after_${METRIC}.tsv" "$OUT/new_after_${METRIC}.tsv"; then
    echo "PASS — outputs identical"
    exit 0
else
    echo "FAIL — outputs differ"
    exit 1
fi
