#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
SEED="$AB/port_summarize_month/seed.sql"
ANCHORS=("2024-09" "2024-10" "2024-12" "2025-01" "2025-04" "2025-07")

reset_test_dbs > /dev/null
load_fixture "$SEED"
for anchor in "${ANCHORS[@]}"; do
    run_new summarize-month "$anchor" > "$OUT/new_${anchor}.log" 2>&1
done

dump_full summary_user_vals     "$METRICS_DB" "datetime, rowid, colid, period" > "$OUT/new_user.tsv"
dump_full summary_simusage_vals "$METRICS_DB" "datetime, rowid, colid, period" > "$OUT/new_simusage.tsv"
dump_full summary_misc_vals     "$METRICS_DB" "datetime, rowid, colid, period" > "$OUT/new_misc.tsv"

# Skip Phase 2 (delegation) — the per-port golden runners handle their
# own checks.  Phase 1 was the multi-anchor sweep, which is what these
# 3 cumulative tables capture.
golden_diff "$DIR" user.tsv simusage.tsv misc.tsv
