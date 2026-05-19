#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
LOGFILE="$AB/fixtures/sample_apache.log"

reset_test_dbs > /dev/null
run_new import-webhits "$LOGFILE" > "$OUT/new_stdout.log" 2>&1
dump_full webhits "$METRICS_DB" "datetime" > "$OUT/new_webhits.tsv"

golden_diff "$DIR" webhits.tsv
