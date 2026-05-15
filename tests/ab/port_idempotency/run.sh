#!/bin/bash
# Idempotency: run analyze + summarize twice on the same DB without
# resetting between runs.  Assert the second pass produces output
# identical to the first.
#
# Distinct from port_determinism (which compares two fresh-DB runs):
# idempotency tests re-running on already-processed state.  Production
# runs hzmetrics.py tick every 5 minutes and analyze hourly — a
# non-idempotent bug would accumulate errors over weeks.  Sources of
# non-idempotency to watch for:
#   - INSERTs not gated by an exists-check (logfix-session, middleware-*)
#   - UPDATEs that depend on transient state
#   - Counters/aggregates that re-aggregate already-aggregated rows
#
# Tables snapshotted: the same 12 covered by port_pipeline.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

MONTH="${1:-2025-07}"

snapshot() {
    local label="$1"
    dump_full sessionlog_metrics  "$METRICS_DB" "sessnum"                  > "$OUT/${label}_sessionlog.tsv"
    dump_full toolstart           "$METRICS_DB" "datetime, user, ip, walltime, cputime" > "$OUT/${label}_toolstart.tsv"
    dump_full web                 "$METRICS_DB" "datetime, ip, content"    > "$OUT/${label}_web.tsv"
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
    dump_full jos_stats_topvals "$HUB_DB" "top, period, \`rank\`, name"  > "$OUT/${label}_toplists.tsv"
}

echo "── pass 1: fresh DB → analyze + summarize ──"
reset_test_dbs >/dev/null
load_fixture "$AB/port_pipeline/seed.sql"
run_new backfill-dnload --start "$MONTH" > "$OUT/p1_00_backfill.log" 2>&1
run_new analyze   --month "$MONTH" --force > "$OUT/p1_01_analyze.log"   2>&1
run_new summarize --month "$MONTH" --force > "$OUT/p1_02_summarize.log" 2>&1
snapshot pass1

echo "── pass 2: same DB → analyze + summarize again ──"
# NB: NO reset, NO re-seed.  This is the point.
run_new backfill-dnload --start "$MONTH" > "$OUT/p2_00_backfill.log" 2>&1
run_new analyze   --month "$MONTH" --force > "$OUT/p2_01_analyze.log"   2>&1
run_new summarize --month "$MONTH" --force > "$OUT/p2_02_summarize.log" 2>&1
snapshot pass2

echo
echo "── diff pass1 vs pass2 ──"
fail=0
for t in sessionlog toolstart web websessions \
         summary_user summary_simusage summary_misc xprofiles \
         stats_tools stats tops toplists; do
    if diff -q "$OUT/pass1_${t}.tsv" "$OUT/pass2_${t}.tsv" >/dev/null 2>&1; then
        echo "  PASS  $t"
    else
        echo "  FAIL  $t (re-run changed output)"
        diff -u "$OUT/pass1_${t}.tsv" "$OUT/pass2_${t}.tsv" | head -20
        fail=1
    fi
done

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
