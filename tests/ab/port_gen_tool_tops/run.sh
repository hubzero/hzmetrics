#!/bin/bash
# A/B compare legacy gen_tool_tops.php vs new hzmetrics.py gen-tool-tops.
# Runs gen-tool-stats first to populate the input table.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

MONTH="${1:-2025-07}"

run_side() {
    local label="$1" lang="$2"  # lang: legacy|new
    echo
    echo "=== $label ==="
    reset_test_dbs
    load_fixture "$DIR/seed.sql"

    # Step 1: populate jos_resource_stats_tools (input to gen-tool-tops).
    case "$lang" in
        legacy) run_legacy_php gen_tool_stats.php "$MONTH" > "$OUT/${label}_stats.log" 2>&1 ;;
        new)    run_new        gen-tool-stats     "$MONTH" > "$OUT/${label}_stats.log" 2>&1 ;;
    esac

    # Step 2: gen-tool-tops itself.
    case "$lang" in
        legacy) run_legacy_php gen_tool_tops.php "$MONTH" > "$OUT/${label}_tops.log" 2>&1 ;;
        new)    run_new        gen-tool-tops     "$MONTH" > "$OUT/${label}_tops.log" 2>&1 ;;
    esac

    # id here is the FK to jos_resource_stats_tools (NOT an auto-inc of
    # the topvals row itself), so keep it.  dump_full would strip 'id' —
    # inline the SELECT instead.
    mysql_test "$HUB_DB" -BN -e "
        SELECT id, top, \`rank\`, name, value FROM jos_resource_stats_tools_topvals
        ORDER BY id, top, \`rank\`, name
    " > "$OUT/${label}_topvals.tsv"
    echo "  wrote $OUT/${label}_topvals.tsv"
}

run_side legacy legacy
run_side new    new

echo
echo "=== diff: legacy vs new ==="
if diff -u "$OUT/legacy_topvals.tsv" "$OUT/new_topvals.tsv"; then
    echo "PASS — outputs identical"
    exit 0
else
    echo "FAIL — outputs differ"
    exit 1
fi
