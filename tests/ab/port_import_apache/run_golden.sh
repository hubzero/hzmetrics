#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
LOGFILE="$AB/fixtures/sample_apache.log"

reset_test_dbs > /dev/null
run_new import-apache "$LOGFILE" > "$OUT/new_stdout.log" 2>&1
# Exclude dnload: new code sets it inline at insert (0/1); the legacy
# import never touched it (NULL); the frozen golden TSV reflects the
# legacy-shaped NULLs.  dnload semantics are covered by
# port_dnload_classify, not by this port.  Same exclusion as
# port_fuzz/fuzz_import_apache.sh.
dump_full web "$METRICS_DB" "datetime, ip, content" "dnload" > "$OUT/new_web.tsv"

golden_diff "$DIR" web.tsv
