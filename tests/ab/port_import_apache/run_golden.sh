#!/bin/bash
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
LOGFILE="$AB/fixtures/sample_apache.log"

reset_test_dbs > /dev/null
run_new import-apache "$LOGFILE" > "$OUT/new_stdout.log" 2>&1
dump_full web "$METRICS_DB" "datetime, ip, content" > "$OUT/new_web.tsv"

golden_diff "$DIR" web.tsv
