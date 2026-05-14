#!/bin/bash
# A/B compare legacy xlogfix_clean.php vs new hzmetrics.py clean-bots.
#
# For each side of the comparison:
#   1. tests/ab/setup_test_dbs.sh --reset           (truncate + reload reference data)
#   2. mysql < seed.sql                             (per-test variable rows)
#   3. invoke the script with the same args
#   4. dump target table to TSV, deterministically ordered
# Then diff the two TSVs.  Empty diff = port matches legacy.

set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

TABLE="${1:-web}"     # web or websessions
MONTH="${2:-2025-07}"

run_side() {
    local label="$1"     # "legacy" or "new"
    local invoker="$2"   # function name
    shift 2
    echo
    echo "=== $label: $* ==="
    reset_test_dbs
    load_fixture "$DIR/seed.sql"

    # capture before-state for sanity
    dump_table_tsv "$TABLE" "$OUT/${label}_before_${TABLE}.tsv"
    local before_n; before_n=$(wc -l < "$OUT/${label}_before_${TABLE}.tsv")
    echo "  before: $before_n row(s) in $TABLE"

    "$invoker" "$@" > "$OUT/${label}_stdout.log" 2>&1 || {
        echo "  $label invocation failed; log:"
        cat "$OUT/${label}_stdout.log"
        return 1
    }

    dump_table_tsv "$TABLE" "$OUT/${label}_after_${TABLE}.tsv"
    local after_n; after_n=$(wc -l < "$OUT/${label}_after_${TABLE}.tsv")
    echo "  after:  $after_n row(s) (deleted $((before_n - after_n)))"
}

run_side legacy run_legacy_php  xlogfix_clean.php "$TABLE" "$MONTH"
run_side new    run_new          clean-bots         "$TABLE" "$MONTH"

echo
echo "=== diff: legacy_after vs new_after ==="
if diff -u "$OUT/legacy_after_${TABLE}.tsv" "$OUT/new_after_${TABLE}.tsv"; then
    echo "PASS — outputs identical"
    exit 0
else
    echo "FAIL — outputs differ (see diff above)"
    exit 1
fi
