#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
MONTH="${1:-2025-07}"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
run_new summarize-month "$MONTH" > "$OUT/new_stdout.log" 2>&1
dump_full summary_user_vals     "$METRICS_DB" "rowid, colid, period" > "$OUT/new_user.tsv"
dump_full summary_simusage_vals "$METRICS_DB" "rowid, colid, period" > "$OUT/new_simusage.tsv"
dump_full summary_misc_vals     "$METRICS_DB" "rowid, colid, period" > "$OUT/new_misc.tsv"

golden_diff "$DIR" user.tsv simusage.tsv misc.tsv
