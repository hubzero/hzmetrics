#!/bin/bash
# A/B compare legacy logfix_session.pl vs new hzmetrics.py logfix-session.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

MONTH="${1:-2025-07}"

run_side() {
    local label="$1" invoker="$2"; shift 2
    echo
    echo "=== $label: $* ==="
    reset_test_dbs
    load_fixture "$DIR/seed.sql"
    "$invoker" "$@" > "$OUT/${label}_stdout.log" 2>&1 || {
        echo "  $label invocation failed; log:"
        cat "$OUT/${label}_stdout.log"
        return 1
    }
    # websessions: id is explicit (MAX(id)+1 sequence) and deterministic
    # across both sides — keep it.  Inline SELECT to preserve id.
    mysql_test "$METRICS_DB" -BN -e "
        SELECT id, datetime, ipcountry, ip, host, domain,
               duration, jobs, webevents
        FROM websessions ORDER BY id
    " > "$OUT/${label}_websessions.tsv"
    # web + toolstart: full-column dumps; logfix-session only writes
    # sessionid on existing rows so order is stable by row identity.
    dump_full web "$METRICS_DB" "datetime, ip, content" > "$OUT/${label}_web.tsv"
    dump_full toolstart "$METRICS_DB" "datetime, ip, host" > "$OUT/${label}_toolstart.tsv"
    echo "  wrote websessions, web, toolstart tsvs"
}

run_side legacy run_legacy_perl logfix_session.pl   "$MONTH"
run_side new    run_new          logfix-session     "$MONTH"

echo
fail=0
for t in websessions web toolstart; do
    echo "=== diff ($t) ==="
    if diff -u "$OUT/legacy_${t}.tsv" "$OUT/new_${t}.tsv"; then
        echo "  PASS"
    else
        echo "  FAIL"
        fail=1
    fi
done

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
