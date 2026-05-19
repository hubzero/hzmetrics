#!/bin/bash
# CLI/error contract checks for behavior that A/B/golden diffs do not cover:
# invalid inputs and configuration errors must produce non-zero process exit
# codes so cron/CI can notice failures.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

fail=0

expect_rc() {
    local label="$1" expected="$2"; shift 2
    local log="$OUT/${label}.log"

    "$@" > "$log" 2>&1
    local rc=$?
    if [ "$rc" -eq "$expected" ]; then
        echo "  PASS  $label (exit=$rc)"
    else
        echo "  FAIL  $label (expected exit=$expected, got exit=$rc)"
        tail -20 "$log"
        fail=1
    fi
}

run_new_raw() {
    HZMETRICS_ACCESS_CFG="$ACCESS_CFG" \
    HZMETRICS_LOG="$HZMETRICS_LOG" \
    "$PY" "$REPO/hzmetrics.py" "$@"
}

BAD_MAP_CFG="$OUT/missing_map.cfg"
sed "s#^\$hub_dir = .*#\$hub_dir = '/tmp/hzmetrics-missing-map-dir';#" \
    "$ACCESS_CFG" > "$BAD_MAP_CFG"

echo "── invalid argument / config exit codes ──"
expect_rc bad_resolve_db_key 2 run_new_raw resolve-dns nope web
expect_rc bad_clean_range 2 run_new_raw clean-bots web 2025-08..2025-07
expect_rc missing_whoisonline_map 2 env HZMETRICS_ACCESS_CFG="$BAD_MAP_CFG" \
    HZMETRICS_LOG="$HZMETRICS_LOG" "$PY" "$REPO/hzmetrics.py" whoisonline --dry-run

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
