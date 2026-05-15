#!/bin/bash
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
LOGFILE="$AB/fixtures/sample_apache.log"

reset_test_dbs > /dev/null
run_new identify-bots "$LOGFILE" > "$OUT/new_stdout.log" 2>&1
dump_full bot_useragents "$METRICS_DB" "useragent" > "$OUT/new_bots.tsv"

golden_diff "$DIR" bots.tsv
