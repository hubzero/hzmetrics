#!/bin/bash
# Period sweep: invoke summarize-month at multiple anchor months and
# verify legacy + new agree on the period-boundary date math at each.
#
# The base port_summarize_month test only anchors at 2025-07.  Period 1
# (month), period 3 (quarter), period 12 (rolling 12mo) and period 13
# (fiscal year Oct-Sep) ranges all depend on the anchor month, so a bug
# in the boundary arithmetic could pass at July but fail at January or
# October.  This test exercises each boundary case:
#
#   2024-09  Q3 2024 / FY24 (Sep is last month of FY24)
#   2024-10  Q4 2024 / FY25 (Oct is first month of FY25 — boundary!)
#   2024-12  Q4 2024 / end of calendar 2024
#   2025-01  Q1 2025 / new calendar year
#   2025-04  Q2 2025 / fixture-empty month
#   2025-07  Q3 2025 (sanity — same as the base test)
#
# Reuses port_summarize_month/seed.sql which has data clustered in
# 2025-07 + a few 2024-12 rows; most anchors produce mostly-zero summary
# values, which is still a valid parity check (zeros must match zeros).
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

SEED="$AB/port_summarize_month/seed.sql"
ANCHORS=("2024-09" "2024-10" "2024-12" "2025-01" "2025-04" "2025-07")

run_side() {
    local label="$1" invoker="$2"
    echo
    echo "=== $label: ${ANCHORS[@]} ==="
    reset_test_dbs >/dev/null
    load_fixture "$SEED"
    for anchor in "${ANCHORS[@]}"; do
        case "$invoker" in
            legacy) run_legacy_php xlogfix_summary.php "$anchor" \
                        > "$OUT/${label}_${anchor}.log" 2>&1 ;;
            new)    run_new        summarize-month     "$anchor" \
                        > "$OUT/${label}_${anchor}.log" 2>&1 ;;
        esac
    done

    # Dump full summary tables (rows from every anchor live side-by-side
    # under their own datetime='YYYY-MM-00' values).
    dump_full summary_user_vals     "$METRICS_DB" "datetime, rowid, colid, period" > "$OUT/${label}_user.tsv"
    dump_full summary_simusage_vals "$METRICS_DB" "datetime, rowid, colid, period" > "$OUT/${label}_simusage.tsv"
    dump_full summary_misc_vals     "$METRICS_DB" "datetime, rowid, colid, period" > "$OUT/${label}_misc.tsv"
}

run_side legacy legacy
run_side new    new

echo
echo "=== diff legacy vs new across all anchors ==="
fail=0
for t in user simusage misc; do
    if diff -u "$OUT/legacy_${t}.tsv" "$OUT/new_${t}.tsv" >/dev/null 2>&1; then
        echo "  PASS  summary_${t}_vals"
    else
        echo "  FAIL  summary_${t}_vals"
        # Print first divergent row + the anchor whose datetime appears in it
        diff -u "$OUT/legacy_${t}.tsv" "$OUT/new_${t}.tsv" | head -30
        fail=1
    fi
done

# Per-anchor breakdown so failures pinpoint which boundary case broke.
echo
echo "=== per-anchor diff breakdown ==="
for anchor in "${ANCHORS[@]}"; do
    a_fail=0
    for t in user simusage misc; do
        # The datetime column appears as 'YYYY-MM-00 HH:MM:SS' (or
        # '0000-00-00 00:00:00' for the period-14 row, which is
        # produced once per anchor and is anchor-independent).
        legacy_slice=$(grep "^${anchor}-00 " "$OUT/legacy_${t}.tsv" 2>/dev/null || true)
        new_slice=$(   grep "^${anchor}-00 " "$OUT/new_${t}.tsv"    2>/dev/null || true)
        if [ "$legacy_slice" != "$new_slice" ]; then
            echo "  FAIL  anchor=$anchor table=summary_${t}_vals"
            diff <(printf "%s\n" "$legacy_slice") <(printf "%s\n" "$new_slice") | head -10
            a_fail=1
            fail=1
        fi
    done
    [ "$a_fail" -eq 0 ] && echo "  PASS  anchor=$anchor"
done

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
