#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
MONTH="${1:-2025-07}"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
run_new backfill-dnload --start "$MONTH"     > "$OUT/new_00_backfill.log"  2>&1
run_new analyze   --month "$MONTH" --force   > "$OUT/new_01_analyze.log"   2>&1
run_new summarize --month "$MONTH" --force   > "$OUT/new_02_summarize.log" 2>&1

dump_full sessionlog_metrics  "$METRICS_DB" "sessnum"                  > "$OUT/new_sessionlog.tsv"
dump_full toolstart           "$METRICS_DB" "datetime, user, ip, walltime, cputime" > "$OUT/new_toolstart.tsv"
dump_full web                 "$METRICS_DB" "datetime, ip, content"    > "$OUT/new_web.tsv"
mysql_test "$METRICS_DB" -BN -e "
    SELECT id, datetime, ipcountry, ip, host, domain, duration, jobs, webevents
    FROM websessions ORDER BY id
" > "$OUT/new_websessions.tsv"
dump_full summary_user_vals     "$METRICS_DB" "rowid, colid, period" > "$OUT/new_summary_user.tsv"
dump_full summary_simusage_vals "$METRICS_DB" "rowid, colid, period" > "$OUT/new_summary_simusage.tsv"
dump_full summary_misc_vals     "$METRICS_DB" "rowid, colid, period" > "$OUT/new_summary_misc.tsv"
dump_full jos_xprofiles_metrics "$METRICS_DB" "uidNumber"            > "$OUT/new_xprofiles.tsv"
dump_full jos_resource_stats_tools "$HUB_DB" "resid, period"          > "$OUT/new_stats_tools.tsv"
dump_full jos_resource_stats       "$HUB_DB" "resid, period"          > "$OUT/new_stats.tsv"
mysql_test "$HUB_DB" -BN -e "
    SELECT id, top, \`rank\`, name, value
    FROM jos_resource_stats_tools_topvals ORDER BY id, top, \`rank\`, name
" > "$OUT/new_tops.tsv"
dump_full jos_stats_topvals "$HUB_DB" "top, period, \`rank\`, name" > "$OUT/new_toplists.tsv"

golden_diff "$DIR" \
    sessionlog.tsv toolstart.tsv web.tsv websessions.tsv \
    summary_user.tsv summary_simusage.tsv summary_misc.tsv xprofiles.tsv \
    stats_tools.tsv stats.tsv tops.tsv toplists.tsv
