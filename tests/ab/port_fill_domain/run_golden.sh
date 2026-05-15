#!/bin/bash
# Golden-mode A/B: run only the new fill-domain and diff against the
# saved golden output captured when legacy was last run.  No legacy/
# directory needed.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
TABLE="${1:-web}"
MONTH="${2:-2025-07}"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
run_new fill-domain metrics "$TABLE" "$MONTH" > "$OUT/new_stdout.log" 2>&1
dump_table_tsv "$TABLE" "$OUT/new_after_${TABLE}.tsv"

golden_diff "$DIR" "after_${TABLE}.tsv"
