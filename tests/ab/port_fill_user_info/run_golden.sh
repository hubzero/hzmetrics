#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
TABLE="${1:-toolstart}"
MONTH="${2:-2025-07}"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
run_new fill-user-info metrics "$TABLE" "$MONTH" > "$OUT/new_stdout.log" 2>&1
dump_table_tsv "$TABLE" "$OUT/new_after_${TABLE}.tsv"

golden_diff "$DIR" "after_${TABLE}.tsv"
