#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
METRIC="${1:-wall}"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
run_new "middleware-${METRIC}" > "$OUT/new_stdout.log" 2>&1
dump_full toolstart "$METRICS_DB" \
    "datetime, user, ip, walltime, cputime" \
    > "$OUT/new_after_${METRIC}.tsv"

golden_diff "$DIR" "after_${METRIC}.tsv"
