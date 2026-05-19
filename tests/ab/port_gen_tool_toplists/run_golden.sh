#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
MONTH="${1:-2025-07}"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
run_new gen-tool-stats    "$MONTH" > "$OUT/new_stats.log"    2>&1
run_new gen-tool-toplists "$MONTH" > "$OUT/new_toplists.log" 2>&1
dump_full jos_stats_topvals "$HUB_DB" "top, period, \`rank\`, name" > "$OUT/new_topvals.tsv"

golden_diff "$DIR" topvals.tsv
