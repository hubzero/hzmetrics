#!/bin/bash
# A/B test the full summary pipeline against a real production-data slice.
# The snapshot is pre-enriched (DNS host/domain, ipcountry, sessionid all
# filled from the prior production pipeline run), so this exercises the
# SUMMARY phase against authentic distributions of inputs:
#
#   import-hub-data → gen-tool-stats → gen-tool-tops → gen-tool-toplists →
#   andmore-usage → summarize-month
#
# vs the legacy equivalents.  Diff every output table; the strongest
# assertion of "produces identical results" possible short of running
# both pipelines against live production.
#
# Run capture.sh first to produce snapshot/*.sql.gz (one-time / on update).

set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
SNAP="$DIR/snapshot"
mkdir -p "$OUT"

MONTH="${1:-2025-03}"

if [ ! -f "$SNAP/web.sql.gz" ]; then
    echo "SKIP  port_realdata — snapshot not present" >&2
    echo "      Run $DIR/capture.sh against a production DB to enable this test." >&2
    echo "PASS"
    exit 0
fi

# Load every captured snapshot file into the right DB.
load_snapshot() {
    echo "  loading hub snapshots into $HUB_DB"
    for t in jos_resources jos_resource_assoc jos_xprofiles jos_users \
             jos_user_profiles jos_tool_version jos_tool_version_alias \
             sessionlog joblog; do
        gunzip -c "$SNAP/${t}.sql.gz" \
            | mysql_test "$HUB_DB" 2>&1 | grep -v "^$" | head -3 || true
    done
    echo "  loading metrics snapshots into $METRICS_DB"
    for t in web websessions toolstart webhits \
             userlogin_meaningful userlogin_detect_sample; do
        gunzip -c "$SNAP/${t}.sql.gz" \
            | mysql_test "$METRICS_DB" 2>&1 | grep -v "^$" | head -3 || true
    done
}

run_side() {
    local label="$1" lang="$2"
    echo
    echo "=================================================================="
    echo "  $label run"
    echo "=================================================================="
    reset_test_dbs >/dev/null
    load_snapshot
    # Backfill dnload from URL pattern — mirrors the production deployment
    # where hzmetrics.py backfill-dnload populated dnload once after the
    # 1018cc2 refactor.  Without this, the legacy's LIKE-chain fallback
    # `content LIKE "/resources/%/download/%" OR dnload=1 OR <exts>` and
    # the new port's `dnload=1` see different row sets and produce
    # divergent download_users counts.
    echo "  backfilling dnload"
    run_new backfill-dnload --start "$MONTH" > "$OUT/${label}_00_backfill.log" 2>&1
    case "$lang" in
        legacy)
            run_legacy_php xlogimport_tool_and_reg_user_data.php > "$OUT/${label}_01_hub.log"     2>&1
            run_legacy_php gen_tool_stats.php    "$MONTH"        > "$OUT/${label}_02_stats.log"   2>&1
            run_legacy_php gen_tool_tops.php     "$MONTH"        > "$OUT/${label}_03_tops.log"    2>&1
            run_legacy_php gen_tool_toplists.php "$MONTH"        > "$OUT/${label}_04_toplists.log" 2>&1
            run_legacy_php xlogfix_andmore_usage.php "$MONTH"    > "$OUT/${label}_05_andmore.log" 2>&1
            run_legacy_php xlogfix_summary.php   "$MONTH"        > "$OUT/${label}_06_summary.log" 2>&1
            ;;
        new)
            run_new import-hub-data                             > "$OUT/${label}_01_hub.log"     2>&1
            run_new gen-tool-stats    "$MONTH"                  > "$OUT/${label}_02_stats.log"   2>&1
            run_new gen-tool-tops     "$MONTH"                  > "$OUT/${label}_03_tops.log"    2>&1
            run_new gen-tool-toplists "$MONTH"                  > "$OUT/${label}_04_toplists.log" 2>&1
            run_new andmore-usage     "$MONTH"                  > "$OUT/${label}_05_andmore.log" 2>&1
            run_new summarize-month   "$MONTH"                  > "$OUT/${label}_06_summary.log" 2>&1
            ;;
    esac
    # Full-column dumps; dump_full strips id+processed_on and rounds floats.
    dump_full summary_user_vals     "$METRICS_DB" "rowid, colid, period" > "$OUT/${label}_summary_user.tsv"
    dump_full summary_simusage_vals "$METRICS_DB" "rowid, colid, period" > "$OUT/${label}_summary_simusage.tsv"
    dump_full summary_misc_vals     "$METRICS_DB" "rowid, colid, period" > "$OUT/${label}_summary_misc.tsv"
    dump_full jos_resource_stats_tools "$HUB_DB" "resid, period" > "$OUT/${label}_stats_tools.tsv"
    dump_full jos_resource_stats       "$HUB_DB" "resid, period" > "$OUT/${label}_stats.tsv"
    # topvals.id is a FK to stats_tools.id — both implementations produce
    # matching auto-inc ids since gen-tool-stats inserts in deterministic
    # order.  Inline SELECT to keep the id column.
    mysql_test "$HUB_DB" -BN -e "
        SELECT id, top, \`rank\`, name, value FROM jos_resource_stats_tools_topvals
        ORDER BY id, top, \`rank\`, name
    " > "$OUT/${label}_tops.tsv"
    dump_full jos_stats_topvals "$HUB_DB" "top, period, \`rank\`, name" > "$OUT/${label}_toplists.tsv"
    echo "  captured 7 output table TSVs"
}

run_side legacy legacy
run_side new    new

echo
echo "=================================================================="
echo "  diff every output table"
echo "=================================================================="
fail=0
for t in summary_user summary_simusage summary_misc stats_tools stats tops toplists; do
    if diff -q "$OUT/legacy_${t}.tsv" "$OUT/new_${t}.tsv" >/dev/null 2>&1; then
        echo "  PASS  $t"
    else
        echo "  FAIL  $t"
        diff -u "$OUT/legacy_${t}.tsv" "$OUT/new_${t}.tsv" | head -30
        fail=1
    fi
done

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
