#!/bin/bash
# Fuzz logfix-session: generate N random web events with random gaps
# (biased toward the 1800s timeout boundary), run legacy + new
# logfix-session, diff websessions + web.sessionid.
#
# Usage: fuzz_logfix_session.sh [iter [events [seed_base]]]
# Defaults: 30 iter × 150 events = 4500 cases per run.

set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

ITERS="${1:-30}"
EVENTS="${2:-150}"
SEED_BASE="${3:-$(date +%s)}"
MONTH="2025-07"
OUT="$DIR/_out_session"
mkdir -p "$OUT"

echo "fuzz_logfix_session: $ITERS iter × $EVENTS events each, seed_base=$SEED_BASE"
echo

passed=0
for i in $(seq 1 "$ITERS"); do
    seed=$((SEED_BASE + i))
    # gen_web_events.py emits `USE foo_metrics_test;` as a placeholder
    # DB name; rewrite to the active METRICS_DB on load.
    "$PY" "$DIR/gen_web_events.py" "$EVENTS" "$seed" \
        | sed "s/^USE foo_metrics_test;/USE \`$METRICS_DB\`;/" \
        > "$OUT/seed_${seed}.sql"

    # ── legacy ────────────────────────────────────────────────
    reset_test_dbs > /dev/null
    mysql_test < "$OUT/seed_${seed}.sql" > /dev/null
    run_legacy_perl logfix_session.pl "$MONTH" \
        > "$OUT/seed_${seed}_legacy.log" 2>&1
    mysql_test "$METRICS_DB" -BN -e "
        SELECT id, datetime, ip, host, duration, jobs, webevents
        FROM websessions ORDER BY id
    " > "$OUT/seed_${seed}_legacy_ws.tsv"
    mysql_test "$METRICS_DB" -BN -e "
        SELECT datetime, ip, content, sessionid FROM web ORDER BY datetime, ip, content
    " > "$OUT/seed_${seed}_legacy_web.tsv"

    # ── new ───────────────────────────────────────────────────
    reset_test_dbs > /dev/null
    mysql_test < "$OUT/seed_${seed}.sql" > /dev/null
    run_new logfix-session "$MONTH" \
        > "$OUT/seed_${seed}_new.log" 2>&1
    mysql_test "$METRICS_DB" -BN -e "
        SELECT id, datetime, ip, host, duration, jobs, webevents
        FROM websessions ORDER BY id
    " > "$OUT/seed_${seed}_new_ws.tsv"
    mysql_test "$METRICS_DB" -BN -e "
        SELECT datetime, ip, content, sessionid FROM web ORDER BY datetime, ip, content
    " > "$OUT/seed_${seed}_new_web.tsv"

    # ── diff ──────────────────────────────────────────────────
    ws_diff=0; web_diff=0
    diff -q "$OUT/seed_${seed}_legacy_ws.tsv" "$OUT/seed_${seed}_new_ws.tsv"  >/dev/null 2>&1 || ws_diff=1
    diff -q "$OUT/seed_${seed}_legacy_web.tsv" "$OUT/seed_${seed}_new_web.tsv" >/dev/null 2>&1 || web_diff=1
    if [ "$ws_diff" -ne 0 ] || [ "$web_diff" -ne 0 ]; then
        echo
        echo "FAIL iteration $i  seed=$seed"
        echo "  reproduce: $PY $DIR/gen_web_events.py $EVENTS $seed > /tmp/fuzz.sql"
        if [ "$ws_diff" -ne 0 ]; then
            echo "  websessions diff (first 30):"
            diff "$OUT/seed_${seed}_legacy_ws.tsv" "$OUT/seed_${seed}_new_ws.tsv" | sed -n '1,30p' || true
        fi
        if [ "$web_diff" -ne 0 ]; then
            echo "  web.sessionid diff (first 30):"
            diff "$OUT/seed_${seed}_legacy_web.tsv" "$OUT/seed_${seed}_new_web.tsv" | sed -n '1,30p' || true
        fi
        echo
        echo "  $passed / $i iterations passed before failure"
        exit 1
    fi
    passed=$((passed + 1))
    [ $((passed % 10)) -eq 0 ] && printf '.'
    rm -f "$OUT/seed_${seed}.sql" \
          "$OUT/seed_${seed}_legacy_ws.tsv" "$OUT/seed_${seed}_new_ws.tsv" \
          "$OUT/seed_${seed}_legacy_web.tsv" "$OUT/seed_${seed}_new_web.tsv" \
          "$OUT/seed_${seed}_legacy.log" "$OUT/seed_${seed}_new.log"
done

echo
echo "PASS — all $ITERS iterations × $EVENTS events ($(($ITERS * $EVENTS)) cases) clean"
