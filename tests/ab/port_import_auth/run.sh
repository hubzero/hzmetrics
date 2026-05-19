#!/bin/bash
# A/B compare legacy xlogimport_authlog.php vs new hzmetrics.py import-auth.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"
LOGFILE="$DIR/sample_auth.log"

run_side() {
    local label="$1" invoker="$2"; shift 2
    echo
    echo "=== $label: $* ==="
    reset_test_dbs
    "$invoker" "$@" > "$OUT/${label}_stdout.log" 2>&1 || {
        echo "  $label invocation failed; log:"
        cat "$OUT/${label}_stdout.log"
        return 1
    }
    dump_full userlogin "$METRICS_DB" "datetime, user, ip, action" \
        > "$OUT/${label}_userlogin.tsv"
    echo "  wrote $OUT/${label}_userlogin.tsv"
}

run_side legacy run_legacy_php "import/xlogimport_authlog.php" "$LOGFILE"
run_side new    run_new        import-auth                     "$LOGFILE"

# New import-auth filters to action ∈ (login, simulation) at insert time;
# legacy inserts every action type unfiltered.  The pipeline only ever
# reads login/simulation, so the relevant invariant is "legacy and new
# agree on the rows we actually keep" — filter both sides before diff.
# dump_full output for userlogin is: datetime user uidNumber ip action.
filter_keepers() {
    awk -F'\t' '$5 == "login" || $5 == "simulation"' "$1"
}
filter_keepers "$OUT/legacy_userlogin.tsv" > "$OUT/legacy_userlogin_filtered.tsv"
filter_keepers "$OUT/new_userlogin.tsv"    > "$OUT/new_userlogin_filtered.tsv"

echo
echo "=== diff: legacy vs new  (action ∈ login/simulation) ==="
if diff -u "$OUT/legacy_userlogin_filtered.tsv" "$OUT/new_userlogin_filtered.tsv"; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
