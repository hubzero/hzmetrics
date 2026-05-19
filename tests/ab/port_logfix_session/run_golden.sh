#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
MONTH="${1:-2025-07}"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
run_new logfix-session "$MONTH" > "$OUT/new_stdout.log" 2>&1
mysql_test "$METRICS_DB" -BN -e "
    SELECT id, datetime, ipcountry, ip, host, domain,
           duration, jobs, webevents
    FROM websessions ORDER BY id
" > "$OUT/new_websessions.tsv"
dump_full web       "$METRICS_DB" "datetime, ip, content" > "$OUT/new_web.tsv"
dump_full toolstart "$METRICS_DB" "datetime, ip, host"    > "$OUT/new_toolstart.tsv"

golden_diff "$DIR" websessions.tsv web.tsv toolstart.tsv
