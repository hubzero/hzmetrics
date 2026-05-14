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
    mysql_test "$METRICS_DB" -BN -e "
        SELECT datetime, uidNumber, user, ip, action FROM userlogin
        ORDER BY datetime, user
    " > "$OUT/${label}_userlogin.tsv"
    echo "  wrote $OUT/${label}_userlogin.tsv"
}

run_side legacy run_legacy_php "import/xlogimport_authlog.php" "$LOGFILE"
run_side new    run_new        import-auth                     "$LOGFILE"

echo
echo "=== diff: legacy vs new ==="
if diff -u "$OUT/legacy_userlogin.tsv" "$OUT/new_userlogin.tsv"; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
