#!/bin/bash
# Integration test: the core promise of the rebuild phase.
#
# When catchup backfills an earlier month M1, the period-14 (all-time)
# cells for every later month M2 go stale — they were computed when M1
# wasn't yet in the DB, so their windows summed wrong totals.  The
# rebuild sweep re-runs summarize on each affected M2 with all periods
# and refreshes those cells.  This test exercises that exact invariant
# on a real DB.
#
# Metric chosen: summary_misc_vals rowid=8 ("Web Server Hits") =
#   SUM(webhits.hits) WHERE datetime > dstart AND datetime < dstop
# Period-14 spans 1995-01-01 through end-of-M2, so M1's webhits row
# DOES fall inside it.  Period-1 spans only M2 itself, so it shouldn't.
#
# M1 = 2023-06, M2 = 2024-06.  webhits.hits chosen to make arithmetic
# obvious (100 + 200 = 300).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"

. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"
M1="2023-06"
M2="2024-06"
HITS_M1=100
HITS_M2=200

# Cell of interest: misc_vals rowid=8, colid=1.
cell() {
    local period="$1"
    mysql_test "$METRICS_DB" -BN -e "
        SELECT value FROM summary_misc_vals
        WHERE datetime = '${M2}-00' AND rowid = 8 AND colid = 1 AND period = $period
    "
}

clear_test_state() {
    mysql_test "$METRICS_DB" <<SQL
DELETE FROM webhits WHERE datetime IN ('${M1}-15 00:00:00','${M2}-15 00:00:00');
DELETE FROM summary_misc_vals     WHERE datetime IN ('${M1}-00','${M2}-00');
DELETE FROM summary_user_vals     WHERE datetime IN ('${M1}-00','${M2}-00');
DELETE FROM summary_simusage_vals WHERE datetime IN ('${M1}-00','${M2}-00');
DELETE FROM summary_andmore_vals  WHERE datetime IN ('${M1}-00','${M2}-00');
SQL
}

summarize_m2() {
    HZMETRICS_LOG=/tmp/hzmetrics-ab.log "$PY" <<PYEOF > "$OUT/${1}.log" 2>&1
import sys
sys.path.insert(0, "$AB/../..")
import hzmetrics as hz
hz.do_summarize("$M2")
PYEOF
}

fail=0

echo "=== fresh state ==="
clear_test_state
mysql_test "$METRICS_DB" -e "INSERT INTO webhits (datetime, hits) VALUES ('${M2}-15 00:00:00', $HITS_M2);"

echo ""
echo "=== pass 1: summarize M2 (M1 not yet backfilled) ==="
summarize_m2 pass1
v1_p14=$(cell 14)
v1_p1=$(cell 1)
echo "  period=14 value: $v1_p14  (expect $HITS_M2)"
echo "  period=1  value: $v1_p1   (expect $HITS_M2)"
if [ "$v1_p14" = "$HITS_M2" ]; then
    echo "  PASS  period-14 pre-backfill = M2 hits only"
else
    echo "  FAIL  expected period-14 = $HITS_M2, got $v1_p14"
    fail=1
fi
if [ "$v1_p1" = "$HITS_M2" ]; then
    echo "  PASS  period-1 = M2 hits"
else
    echo "  FAIL  expected period-1 = $HITS_M2, got $v1_p1"
    fail=1
fi

echo ""
echo "=== backfill M1 webhits ==="
mysql_test "$METRICS_DB" -e "INSERT INTO webhits (datetime, hits) VALUES ('${M1}-15 00:00:00', $HITS_M1);"

echo ""
echo "=== pass 2: re-summarize M2 (simulating rebuild) ==="
# Re-summarize.  Need to delete M2 summary rows first because summarize
# uses INSERT, not REPLACE (in production, the wipe step would handle this).
mysql_test "$METRICS_DB" -e "
    DELETE FROM summary_misc_vals     WHERE datetime = '${M2}-00';
    DELETE FROM summary_user_vals     WHERE datetime = '${M2}-00';
    DELETE FROM summary_simusage_vals WHERE datetime = '${M2}-00';
    DELETE FROM summary_andmore_vals  WHERE datetime = '${M2}-00';
"
summarize_m2 pass2
v2_p14=$(cell 14)
v2_p1=$(cell 1)
expected_p14=$((HITS_M1 + HITS_M2))
echo "  period=14 value: $v2_p14  (expect $expected_p14)"
echo "  period=1  value: $v2_p1   (expect $HITS_M2 — M1 outside M2's per-month window)"
if [ "$v2_p14" = "$expected_p14" ]; then
    echo "  PASS  rebuild refreshed period-14 to include M1's $HITS_M1 hits"
else
    echo "  FAIL  expected period-14 = $expected_p14, got $v2_p14"
    fail=1
fi
if [ "$v2_p1" = "$HITS_M2" ]; then
    echo "  PASS  period-1 unchanged at $HITS_M2 (M1 outside per-month window)"
else
    echo "  FAIL  expected period-1 = $HITS_M2, got $v2_p1  (regression!)"
    fail=1
fi

# Final guard: the rebuild value must differ from the pre-backfill one.
# If they happen to match by accident the test would silently pass.
if [ "$v2_p14" = "$v1_p14" ]; then
    echo "  FAIL  period-14 did not change across the rebuild (was $v1_p14, still $v2_p14)"
    fail=1
fi

echo ""
echo "=== cleanup ==="
clear_test_state

if [ "$fail" -eq 0 ]; then
    echo ""
    echo "PASS"
    exit 0
else
    echo ""
    echo "FAIL"
    exit 1
fi
