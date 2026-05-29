#!/bin/bash
# Pin the inline-webhits behavior: do_import_apache emits one webhits
# row per day with COUNT(*) of kept web rows for that day, in the
# same transaction.  We exercise via import-apache (the standalone
# import-webhits CLI was removed when webhits became a derived
# table) and assert against the golden TSV.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
LOGFILE="$AB/fixtures/sample_apache.log"

reset_test_dbs > /dev/null
run_new import-apache "$LOGFILE" > "$OUT/new_stdout.log" 2>&1
dump_full webhits "$METRICS_DB" "datetime" > "$OUT/new_webhits.tsv"

golden_diff "$DIR" webhits.tsv
