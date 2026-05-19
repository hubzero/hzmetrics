#!/bin/bash
# Cross-table invariant checker.  Runs the new pipeline (analyze +
# summarize) on the port_pipeline fixture, then asserts a set of
# code-backed cross-table rules in check_invariants.py.  Fails loudly
# on any violation — even though legacy and new agree on the produced
# values, an invariant violation means BOTH may have a bug the A/B
# diff can't catch.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

MONTH="${1:-2025-07}"

echo "── load pipeline fixture + run new analyze/summarize ──"
reset_test_dbs > /dev/null
load_fixture "$AB/port_pipeline/seed.sql"
run_new backfill-dnload --start "$MONTH" > "$OUT/00_backfill.log" 2>&1
run_new analyze   --month "$MONTH" --force > "$OUT/01_analyze.log"   2>&1
run_new summarize --month "$MONTH" --force > "$OUT/02_summarize.log" 2>&1

echo
echo "── invariant checks ──"
"$PY" "$DIR/check_invariants.py" "$ACCESS_CFG"
