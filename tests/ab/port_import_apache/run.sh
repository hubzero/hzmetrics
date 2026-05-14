#!/bin/bash
# A/B compare legacy xlogimport_apache.php vs new hzmetrics.py import-apache.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"
LOGFILE="$AB/fixtures/sample_apache.log"

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
    # Strip the auto-incremented id; order by (datetime, ip, content).
    # Compare every column that the two log formats populate differently:
    # NEW format sets apache_pid + joomla_sessionid + auth/comp/view/task/
    # action/item; OLD format leaves all but joomla_sessionid empty.  Any
    # regex-dispatch bug shows up immediately on these columns.
    mysql_test "$METRICS_DB" -BN -e "
        SELECT datetime, content, ip, host, useragent, dnload,
               apache_pid, joomla_sessionid,
               auth_type, component_name, view_name,
               task_name, action_name, item_name
        FROM web ORDER BY datetime, ip, content
    " > "$OUT/${label}_web.tsv"
    echo "  wrote $OUT/${label}_web.tsv ($(wc -l < $OUT/${label}_web.tsv) row(s))"
}

run_side legacy run_legacy_php "import/xlogimport_apache.php" "$LOGFILE"
run_side new    run_new        import-apache                  "$LOGFILE"

echo
echo "=== diff: legacy vs new ==="
if diff -u "$OUT/legacy_web.tsv" "$OUT/new_web.tsv"; then
    echo "PASS"
    exit 0
else
    echo "FAIL"
    exit 1
fi
