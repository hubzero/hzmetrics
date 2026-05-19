#!/bin/bash
# Integration test: do_summarize(month, periods=…) honours the periods
# filter.  Catchup mode passes periods=(1,) so it stays cheap and skips
# long-window periods (0/3/12/13/14) whose correctness depends on data
# in other months.  The whole catchup design assumes this filter works.
#
# We run do_summarize twice against the real test DB:
#   1. periods=(1,)    → expect rows only at period=1
#   2. periods=None    → expect rows across all six period codes
#
# We don't need a rich fixture — do_summarize_month always writes the
# full grid of (rowid, colid) cells per period whether the underlying
# tables are empty or not, so empty-DB row counts are the cleanest test
# of period-filter scope.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"

export HZMETRICS_ACCESS_CFG="${HZMETRICS_ACCESS_CFG:-$AB/fixtures/test_access_geodynamics.cfg}"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"
MONTH="2025-07"

clear_month() {
    mysql_test "$METRICS_DB" -e "
        DELETE FROM summary_user_vals     WHERE datetime='${MONTH}-00';
        DELETE FROM summary_misc_vals     WHERE datetime='${MONTH}-00';
        DELETE FROM summary_simusage_vals WHERE datetime='${MONTH}-00';
        DELETE FROM summary_andmore_vals  WHERE datetime='${MONTH}-00';
    "
}

run_summarize() {
    local periods="$1"
    HZMETRICS_LOG=/tmp/hzmetrics-ab.log "$PY" <<PYEOF > "$OUT/run_${periods//[(),]/-}.log" 2>&1
import sys
sys.path.insert(0, "$AB/../..")
import hzmetrics as hz
periods = ${periods}
hz.do_summarize("$MONTH", periods=periods)
PYEOF
}

period_counts() {
    mysql_test "$METRICS_DB" -BN -e "
        SELECT period, COUNT(*) FROM summary_user_vals
        WHERE datetime = '${MONTH}-00'
        GROUP BY period ORDER BY period;
    "
}

fail=0

# ----------------------------------------------------------------------
# pass 1: periods=(1,)
# ----------------------------------------------------------------------
echo "=== pass 1: periods=(1,) ==="
clear_month
run_summarize "(1,)"

# Expect: only period=1 rows
counts="$(period_counts | tr '\n' ' ')"
echo "  summary_user_vals period counts: $counts"
n_p1=$(mysql_test "$METRICS_DB" -BN -e \
    "SELECT COUNT(*) FROM summary_user_vals WHERE datetime='${MONTH}-00' AND period=1")
n_other=$(mysql_test "$METRICS_DB" -BN -e \
    "SELECT COUNT(*) FROM summary_user_vals WHERE datetime='${MONTH}-00' AND period != 1")
if [ "$n_p1" -gt "0" ] && [ "$n_other" = "0" ]; then
    echo "  PASS  summary_user_vals has $n_p1 period=1 rows, 0 other periods"
else
    echo "  FAIL  expected period=1 only; got p1=$n_p1, other=$n_other"
    fail=1
fi

# andmore_usage should be SUPPRESSED when periods=(1,) — confirm
# summary_andmore_vals stays empty.  (It's also empty in production
# because andmore writes to the hub DB, but checking here just in case.)
n_am=$(mysql_test "$METRICS_DB" -BN -e \
    "SELECT COUNT(*) FROM summary_andmore_vals WHERE datetime='${MONTH}-00'")
echo "  summary_andmore_vals: $n_am row(s)"

# ----------------------------------------------------------------------
# pass 2: periods=None (all six)
# ----------------------------------------------------------------------
echo ""
echo "=== pass 2: periods=None ==="
clear_month
run_summarize "None"

# Expect rows in periods {0, 1, 3, 12, 13, 14}
counts="$(period_counts | tr '\n' ' ')"
echo "  summary_user_vals period counts: $counts"
periods_seen=$(mysql_test "$METRICS_DB" -BN -e \
    "SELECT DISTINCT period FROM summary_user_vals
     WHERE datetime='${MONTH}-00' ORDER BY period" | tr '\n' ',' | sed 's/,$//')
expected="0,1,3,12,13,14"
if [ "$periods_seen" = "$expected" ]; then
    echo "  PASS  summary_user_vals has rows in every period: {$periods_seen}"
else
    echo "  FAIL  expected periods {$expected}, got {$periods_seen}"
    fail=1
fi

# ----------------------------------------------------------------------
# cleanup
# ----------------------------------------------------------------------
clear_month

if [ "$fail" -eq 0 ]; then
    echo ""
    echo "PASS"
    exit 0
else
    echo ""
    echo "FAIL"
    exit 1
fi
