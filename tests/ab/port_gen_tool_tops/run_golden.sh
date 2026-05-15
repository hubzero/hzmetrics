#!/bin/bash
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
MONTH="${1:-2025-07}"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
run_new gen-tool-stats "$MONTH" > "$OUT/new_stats.log" 2>&1
run_new gen-tool-tops  "$MONTH" > "$OUT/new_tops.log"  2>&1
mysql_test "$HUB_DB" -BN -e "
    SELECT id, top, \`rank\`, name, value FROM jos_resource_stats_tools_topvals
    ORDER BY id, top, \`rank\`, name
" > "$OUT/new_topvals.tsv"

golden_diff "$DIR" topvals.tsv
