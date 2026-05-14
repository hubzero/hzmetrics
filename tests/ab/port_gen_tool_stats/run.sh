#!/bin/bash
# A/B compare legacy gen_tool_stats.php vs new hzmetrics.py gen-tool-stats.
# Compares hub.jos_resource_stats_tools + jos_resource_stats end-states.
# Excludes `id` (auto-inc) and `processed_on` (timestamp) from the diff.
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
        echo "  $label invocation failed; log:"
        cat "$OUT/${label}_stdout.log"
        return 1
    }
    # Strip id + processed_on; order by (resid, period) for stable diff.
    # Float columns are ROUND()ed to 8 digits because PHP and Python
    # serialise floats with different default precision (PHP: 14 sig figs,
    # Python: ~17) — the stored DOUBLE bit patterns differ in the trailing
    # digits but the user-visible values are identical.
    mysql_test "$HUB_DB" -BN -e "
        SELECT resid, restype, users, sessions, simulations, jobs,
               ROUND(avg_wall,8), tot_wall,
               ROUND(avg_cpu,8),  tot_cpu,
               ROUND(avg_view,8), tot_view,
               ROUND(avg_wait,8), tot_wait,
               avg_cpus, tot_cpus, datetime, period
        FROM jos_resource_stats_tools ORDER BY resid, period
    " > "$OUT/${label}_stats_tools.tsv"
    mysql_test "$HUB_DB" -BN -e "
        SELECT resid, restype, users, jobs, avg_wall, tot_wall,
               avg_cpu, tot_cpu, datetime, period
        FROM jos_resource_stats ORDER BY resid, period
    " > "$OUT/${label}_stats.tsv"
    echo "  wrote $OUT/${label}_stats{,_tools}.tsv"
}

run_side legacy run_legacy_php gen_tool_stats.php "$MONTH"
run_side new    run_new        gen-tool-stats     "$MONTH"

echo
fail=0
for t in stats_tools stats; do
    echo "=== diff ($t): legacy vs new ==="
    if diff -u "$OUT/legacy_${t}.tsv" "$OUT/new_${t}.tsv"; then
        echo "  PASS"
    else
        echo "  FAIL"
        fail=1
    fi
done

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
