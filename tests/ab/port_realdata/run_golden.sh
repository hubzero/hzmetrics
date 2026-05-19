#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
SNAP="$DIR/snapshot"
MONTH="${1:-2025-03}"

if [ ! -f "$SNAP/web.sql.gz" ]; then
    echo "SKIP  port_realdata — snapshot not present" >&2
    echo "      Run $DIR/capture.sh against a production DB to enable this test." >&2
    exit "${AB_SKIP:-77}"
fi

reset_test_dbs > /dev/null
echo "  loading hub snapshots into $HUB_DB"
for t in jos_resources jos_resource_assoc jos_xprofiles jos_users \
         jos_user_profiles jos_tool_version jos_tool_version_alias \
         sessionlog joblog; do
    gunzip -c "$SNAP/${t}.sql.gz" | mysql_test "$HUB_DB" 2>&1 | grep -v "^$" | head -3 || true
done
echo "  loading metrics snapshots into $METRICS_DB"
for t in web websessions toolstart webhits userlogin_meaningful userlogin_detect_sample; do
    gunzip -c "$SNAP/${t}.sql.gz" | mysql_test "$METRICS_DB" 2>&1 | grep -v "^$" | head -3 || true
done
echo "  backfilling dnload"
run_new backfill-dnload --start "$MONTH" > "$OUT/new_00_backfill.log"  2>&1
run_new import-hub-data                  > "$OUT/new_01_hub.log"       2>&1
run_new gen-tool-stats    "$MONTH"       > "$OUT/new_02_stats.log"     2>&1
run_new gen-tool-tops     "$MONTH"       > "$OUT/new_03_tops.log"      2>&1
run_new gen-tool-toplists "$MONTH"       > "$OUT/new_04_toplists.log"  2>&1
run_new andmore-usage     "$MONTH"       > "$OUT/new_05_andmore.log"   2>&1
run_new summarize-month   "$MONTH"       > "$OUT/new_06_summary.log"   2>&1

dump_full summary_user_vals     "$METRICS_DB" "rowid, colid, period" > "$OUT/new_summary_user.tsv"
dump_full summary_simusage_vals "$METRICS_DB" "rowid, colid, period" > "$OUT/new_summary_simusage.tsv"
dump_full summary_misc_vals     "$METRICS_DB" "rowid, colid, period" > "$OUT/new_summary_misc.tsv"
dump_full jos_resource_stats_tools "$HUB_DB" "resid, period" > "$OUT/new_stats_tools.tsv"
dump_full jos_resource_stats       "$HUB_DB" "resid, period" > "$OUT/new_stats.tsv"
mysql_test "$HUB_DB" -BN -e "
    SELECT id, top, \`rank\`, name, value
    FROM jos_resource_stats_tools_topvals ORDER BY id, top, \`rank\`, name
" > "$OUT/new_tops.tsv"
dump_full jos_stats_topvals "$HUB_DB" "top, period, \`rank\`, name" > "$OUT/new_toplists.tsv"

golden_diff "$DIR" \
    summary_user.tsv summary_simusage.tsv summary_misc.tsv \
    stats_tools.tsv stats.tsv tops.tsv toplists.tsv
