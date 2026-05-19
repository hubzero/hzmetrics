#!/bin/bash
# Empty-input boundary: invoke each port on a freshly-reset DB (no
# fixture loaded) and assert two things:
#   1. The port exits cleanly (rc == 0).  No crashes on empty input.
#   2. No rows are written into the data tables it owns.
#
# Reference tables (domainclass, exclude_list, bot_useragents, classes
# …) get re-populated by reset_test_dbs and are left alone here — we
# only check the data tables.
#
# Catches the bug where a port assumes ≥ 1 row exists (divide-by-zero,
# index error on empty fetchall, etc.).  Currently untested edge case
# because every other fixture has rows.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"
EMPTY_LOG="$OUT/empty.log"
: > "$EMPTY_LOG"
MONTH="2025-07"

# Data tables that should remain empty after running on empty input.
DATA_TABLES_METRICS=(web websessions toolstart sessionlog_metrics
                     userlogin userlogin_lite webhits
                     summary_user_vals summary_simusage_vals
                     summary_misc_vals summary_andmore_vals)
DATA_TABLES_HUB=(jos_resource_stats jos_resource_stats_tools
                 jos_resource_stats_tools_topvals jos_stats_topvals)

count_data_rows() {
    local total=0 t n
    for t in "${DATA_TABLES_METRICS[@]}"; do
        n=$(mysql_test "$METRICS_DB" -BN -e "SELECT COUNT(*) FROM \`$t\`" 2>/dev/null || echo 0)
        total=$((total + n))
    done
    for t in "${DATA_TABLES_HUB[@]}"; do
        n=$(mysql_test "$HUB_DB" -BN -e "SELECT COUNT(*) FROM \`$t\`" 2>/dev/null || echo 0)
        total=$((total + n))
    done
    echo "$total"
}

# Each entry: "label || mode || command-tokens".
#   mode 'strict' : assert clean exit AND no rows written to data tables.
#                   Used for data importers / per-row enrichers — empty
#                   input should mean zero work.
#   mode 'exit'   : assert clean exit only.  Used for summary writers
#                   (analyze / summarize-month / gen-tool-* / andmore-usage)
#                   which legitimately write zero-valued summary rows
#                   to fill the (period × rowid × colid) grid even on
#                   empty input — that grid IS the contract.
COMMANDS=(
    "backfill-dnload      || strict || backfill-dnload --start $MONTH"
    "fill-geo             || strict || fill-geo --month $MONTH"
    "import-apache(empty) || strict || import-apache $EMPTY_LOG"
    "import-auth(empty)   || strict || import-auth   $EMPTY_LOG"
    "fill-domain          || strict || fill-domain   metrics web $MONTH"
    "logfix-session       || strict || logfix-session $MONTH"
    "middleware-wall      || strict || middleware-wall"
    "middleware-cpu       || strict || middleware-cpu"
    "gen-tool-stats       || exit   || gen-tool-stats $MONTH"
    "gen-tool-tops        || exit   || gen-tool-tops  $MONTH"
    "gen-tool-toplists    || exit   || gen-tool-toplists $MONTH"
    "andmore-usage        || exit   || andmore-usage $MONTH"
    "summarize-month      || exit   || summarize-month $MONTH"
    "analyze              || exit   || analyze --month $MONTH --force"
)

fail=0
for entry in "${COMMANDS[@]}"; do
    label="${entry%%||*}"; rest="${entry#*||}"
    mode="${rest%%||*}";   cmd="${rest#*||}"
    label="${label%% *}"; mode="${mode// /}"; cmd="${cmd# }"

    reset_test_dbs > /dev/null
    before=$(count_data_rows)

    log="$OUT/${label}.log"
    if run_new $cmd > "$log" 2>&1; then
        rc=0
    else
        rc=$?
    fi
    after=$(count_data_rows)

    if [ "$rc" -ne 0 ]; then
        echo "  FAIL  $label (exit=$rc)"
        tail -10 "$log"
        fail=1
    elif [ "$mode" = "strict" ] && [ "$after" -ne "$before" ]; then
        echo "  FAIL  $label (wrote $((after - before)) row(s) into data tables on empty input)"
        for t in "${DATA_TABLES_METRICS[@]}"; do
            n=$(mysql_test "$METRICS_DB" -BN -e "SELECT COUNT(*) FROM \`$t\`")
            [ "$n" -gt 0 ] && echo "        metrics.$t : $n row(s)"
        done
        for t in "${DATA_TABLES_HUB[@]}"; do
            n=$(mysql_test "$HUB_DB" -BN -e "SELECT COUNT(*) FROM \`$t\`")
            [ "$n" -gt 0 ] && echo "        hub.$t : $n row(s)"
        done
        fail=1
    else
        echo "  PASS  $label"
    fi
done

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
