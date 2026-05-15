#!/bin/bash
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
run_new import-hub-data > "$OUT/new_stdout.log" 2>&1
dump_full sessionlog_metrics    "$METRICS_DB" "sessnum"   > "$OUT/new_sessionlog.tsv"
dump_full jos_xprofiles_metrics "$METRICS_DB" "uidNumber" > "$OUT/new_xprofiles.tsv"

golden_diff "$DIR" sessionlog.tsv xprofiles.tsv
