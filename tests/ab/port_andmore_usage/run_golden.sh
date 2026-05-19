#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
MONTH="${1:-2025-07}"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
run_new andmore-usage "$MONTH" > "$OUT/new_stdout.log" 2>&1
dump_full jos_resource_stats "$HUB_DB" "resid, period" > "$OUT/new_stats.tsv"

golden_diff "$DIR" stats.tsv
