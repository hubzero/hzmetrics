#!/bin/bash
# A/B compare legacy gen_tool_toplists.php vs new hzmetrics.py gen-tool-toplists.
# Runs gen-tool-stats first to populate the input table.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

MONTH="${1:-2025-07}"

run_side() {
    local label="$1" lang="$2"
    echo
    echo "=== $label ==="
    reset_test_dbs
    load_fixture "$DIR/seed.sql"
    case "$lang" in
        legacy)
            run_legacy_php gen_tool_stats.php    "$MONTH" > "$OUT/${label}_stats.log" 2>&1
            run_legacy_php gen_tool_toplists.php "$MONTH" > "$OUT/${label}_toplists.log" 2>&1
            ;;
        new)
            run_new gen-tool-stats    "$MONTH" > "$OUT/${label}_stats.log" 2>&1
            run_new gen-tool-toplists "$MONTH" > "$OUT/${label}_toplists.log" 2>&1
            ;;
    esac
    dump_full jos_stats_topvals "$HUB_DB" "top, period, \`rank\`, name" \
        > "$OUT/${label}_topvals.tsv"
    echo "  wrote $OUT/${label}_topvals.tsv"
}

run_side legacy legacy
run_side new    new

echo
echo "=== diff: legacy vs new ==="
if diff -u "$OUT/legacy_topvals.tsv" "$OUT/new_topvals.tsv"; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
