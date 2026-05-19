#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
LOGFILE="$DIR/sample_auth.log"

reset_test_dbs > /dev/null
run_new import-auth "$LOGFILE" > "$OUT/new_stdout.log" 2>&1
dump_full userlogin "$METRICS_DB" "datetime, user, ip, action" > "$OUT/new_userlogin.tsv"

golden_diff "$DIR" userlogin.tsv
