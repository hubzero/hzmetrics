#!/bin/bash
# Full-pipeline A/B test.  Loads a raw fixture (un-enriched web rows,
# pre-existing toolstart with -1 walltime/cputime, empty websessions),
# then runs the COMPLETE __process_tool_metrics.sh +
# __process_usage_metrics.sh + __process_usage_metrics_summary.sh chain
# in both implementations and diffs every output table.
#
# Catches interaction bugs between ports that individual per-port tests
# can't surface:
#   - resolve-dns output → fill-domain input
#   - logfix-session output → summarize-month input
#   - middleware-wall/cpu output → gen-tool-stats input
#   - fill-domain output → clean-bots filter
#   - fill-user-info output → summarize-month sim_users
#
# Network-dependent: resolve-dns hits real DNS PTR lookups and
# fill-ipcountry hits https://help.hubzero.org/ipinfo/v1.  Both sides
# hit the same services with the same fixture IPs (stable-PTR public
# IPs only) so they produce identical results — but the test can fail
# transiently if either service is unreachable.
#
# xlogfix_prep.php is skipped (it writes /etc/hubzero-metrics/access.cfg
# which is a deploy-time artifact, not a per-run thing).
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

MONTH="${1:-2025-07}"

run_side() {
    local label="$1" lang="$2"
    echo
    echo "=================================================================="
    echo "  $label pipeline"
    echo "=================================================================="
    reset_test_dbs >/dev/null
    load_fixture "$DIR/seed.sql"

    # Backfill dnload from URL pattern.  Mirrors the production deployment
    # order: hzmetrics.py backfill-dnload ran once after the 1018cc2 refactor.
    # The legacy summarize uses a `content LIKE "/resources/%/download/%"
    # OR dnload=1 OR <file_ext LIKE chain>` hybrid; the new port uses only
    # dnload=1 (post-1018cc2 simplification).  Without backfill they'd
    # disagree on download counts.  Run on both sides so neither has an
    # unfair advantage.
    run_new backfill-dnload --start "$MONTH" > "$OUT/${label}_00_backfill.log" 2>&1

    case "$lang" in
        legacy)
            # Mirror __process_tool_metrics.sh + __process_usage_metrics.sh +
            # __process_usage_metrics_summary.sh exactly (minus xlogfix_prep).
            run_legacy_php  xlogimport_tool_and_reg_user_data.php          > "$OUT/${label}_01_hub.log"        2>&1

            run_legacy_sh   xlogfix_dns_v2.sh metrics sessionlog_metrics "$MONTH"   > "$OUT/${label}_02_dns_sm.log"  2>&1
            run_legacy_php  xlogfix_domain.php metrics sessionlog_metrics "$MONTH"  > "$OUT/${label}_03_dom_sm.log"  2>&1
            run_legacy_php  xlogfix_user_info.php metrics sessionlog_metrics "$MONTH" > "$OUT/${label}_04_ui_sm.log"  2>&1
            run_legacy_php  xlogfix_ipcountry.php metrics sessionlog_metrics "$MONTH" > "$OUT/${label}_05_ic_sm.log"  2>&1
            run_legacy_php  gen_tool_stats.php    "$MONTH"                 > "$OUT/${label}_06_tstat.log"      2>&1
            run_legacy_php  gen_tool_tops.php     "$MONTH"                 > "$OUT/${label}_07_ttop.log"       2>&1
            run_legacy_php  gen_tool_toplists.php "$MONTH"                 > "$OUT/${label}_08_tlist.log"      2>&1

            run_legacy_perl xlogfix_middleware_wall.pl                     > "$OUT/${label}_09_mw_wall.log"    2>&1
            run_legacy_perl xlogfix_middleware_cpu.pl                      > "$OUT/${label}_10_mw_cpu.log"     2>&1
            run_legacy_sh   xlogfix_dns_v2.sh metrics web       "$MONTH"   > "$OUT/${label}_11_dns_web.log"    2>&1
            run_legacy_sh   xlogfix_dns_v2.sh metrics toolstart "$MONTH"   > "$OUT/${label}_12_dns_ts.log"     2>&1
            run_legacy_php  xlogfix_domain.php metrics web        "$MONTH" > "$OUT/${label}_13_dom_web.log"   2>&1
            run_legacy_php  xlogfix_domain.php metrics toolstart  "$MONTH" > "$OUT/${label}_14_dom_ts.log"    2>&1
            run_legacy_perl logfix_session.pl  "$MONTH"                    > "$OUT/${label}_15_logfix.log"     2>&1
            run_legacy_php  xlogfix_clean.php web         "$MONTH"         > "$OUT/${label}_16_clean_web.log"  2>&1
            run_legacy_php  xlogfix_clean.php websessions "$MONTH"         > "$OUT/${label}_17_clean_ws.log"   2>&1
            run_legacy_php  xlogfix_user_info.php metrics toolstart "$MONTH"  > "$OUT/${label}_18_ui_ts.log"   2>&1
            run_legacy_php  xlogfix_ipcountry.php metrics web         "$MONTH" > "$OUT/${label}_19_ic_web.log" 2>&1
            run_legacy_php  xlogfix_ipcountry.php metrics websessions "$MONTH" > "$OUT/${label}_20_ic_ws.log"  2>&1
            run_legacy_php  xlogfix_ipcountry.php metrics toolstart   "$MONTH" > "$OUT/${label}_21_ic_ts.log"  2>&1

            run_legacy_php  xlogfix_summary.php       "$MONTH"             > "$OUT/${label}_22_summary.log"   2>&1
            run_legacy_php  xlogfix_andmore_usage.php "$MONTH"             > "$OUT/${label}_23_andmore.log"   2>&1
            ;;
        new)
            # Same chain via hzmetrics.py analyze + summarize (which inlines
            # all of these in the same order).
            run_new analyze   --month "$MONTH" --force > "$OUT/${label}_analyze.log"   2>&1
            run_new summarize --month "$MONTH" --force > "$OUT/${label}_summarize.log" 2>&1
            ;;
    esac

    # Dump every output table.
    dump_full sessionlog_metrics  "$METRICS_DB" "sessnum"                  > "$OUT/${label}_sessionlog.tsv"
    dump_full toolstart           "$METRICS_DB" "datetime, user, ip, walltime, cputime" > "$OUT/${label}_toolstart.tsv"
    dump_full web                 "$METRICS_DB" "datetime, ip, content"    > "$OUT/${label}_web.tsv"
    # websessions: id explicit, dump inline to keep it
    mysql_test "$METRICS_DB" -BN -e "
        SELECT id, datetime, ipcountry, ip, host, domain,
               duration, jobs, webevents
        FROM websessions ORDER BY id
    " > "$OUT/${label}_websessions.tsv"
    dump_full summary_user_vals     "$METRICS_DB" "rowid, colid, period" > "$OUT/${label}_summary_user.tsv"
    dump_full summary_simusage_vals "$METRICS_DB" "rowid, colid, period" > "$OUT/${label}_summary_simusage.tsv"
    dump_full summary_misc_vals     "$METRICS_DB" "rowid, colid, period" > "$OUT/${label}_summary_misc.tsv"
    dump_full jos_xprofiles_metrics "$METRICS_DB" "uidNumber"            > "$OUT/${label}_xprofiles.tsv"

    dump_full jos_resource_stats_tools "$HUB_DB" "resid, period"          > "$OUT/${label}_stats_tools.tsv"
    dump_full jos_resource_stats       "$HUB_DB" "resid, period"          > "$OUT/${label}_stats.tsv"
    mysql_test "$HUB_DB" -BN -e "
        SELECT id, top, \`rank\`, name, value
        FROM jos_resource_stats_tools_topvals ORDER BY id, top, \`rank\`, name
    " > "$OUT/${label}_tops.tsv"
    dump_full jos_stats_topvals "$HUB_DB" "top, period, \`rank\`, name" > "$OUT/${label}_toplists.tsv"

    echo "  captured 12 output table TSVs"
}

run_side legacy legacy
run_side new    new

echo
echo "=================================================================="
echo "  diff every output table"
echo "=================================================================="
fail=0
for t in sessionlog toolstart web websessions \
         summary_user summary_simusage summary_misc xprofiles \
         stats_tools stats tops toplists; do
    if diff -q "$OUT/legacy_${t}.tsv" "$OUT/new_${t}.tsv" >/dev/null 2>&1; then
        echo "  PASS  $t"
    else
        echo "  FAIL  $t"
        diff -u "$OUT/legacy_${t}.tsv" "$OUT/new_${t}.tsv" | head -30
        fail=1
    fi
done

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
