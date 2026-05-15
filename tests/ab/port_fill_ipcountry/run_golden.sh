#!/bin/bash
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
TABLE="${1:-web}"
MONTH="${2:-2025-07}"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
run_new fill-ipcountry metrics "$TABLE" "$MONTH" > "$OUT/new_stdout.log" 2>&1
dump_full "$TABLE" "$METRICS_DB" "datetime, ip, content" > "$OUT/new_${TABLE}.tsv"

golden_diff "$DIR" "${TABLE}.tsv"
