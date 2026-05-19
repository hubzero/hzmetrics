#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
TABLE="${1:-web}"
MONTH="${2:-2025-07}"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
dump_table_tsv "$TABLE" "$OUT/new_before_${TABLE}.tsv"
run_new clean-bots "$TABLE" "$MONTH" > "$OUT/new_stdout.log" 2>&1
dump_table_tsv "$TABLE" "$OUT/new_after_${TABLE}.tsv"

golden_diff "$DIR" "before_${TABLE}.tsv" "after_${TABLE}.tsv"
