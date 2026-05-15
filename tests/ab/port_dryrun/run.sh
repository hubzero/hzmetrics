#!/bin/bash
# Dry-run correctness: assert that every port invoked with --dry-run
# performs NO database writes.  Strategy:
#   1. Load fixture, run analyze + summarize for real (post-pipeline state)
#   2. Md5-hash the full content of every interesting table
#   3. Invoke a representative set of port commands with --dry-run
#   4. Re-hash; assert every hash is unchanged
#
# Catches the bug where a code path inside --dry-run forgets to gate
# its INSERT/UPDATE/DELETE behind the dry-run flag — silently writing
# anyway.  This bug is invisible to A/B diffs (since dry-run is only
# in the new port) and to idempotency (which always writes).
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"
MONTH="${1:-2025-07}"

# Tables to hash.  Picked to cover every write surface of the dry-run
# commands below.
HASH_TABLES_METRICS=(web websessions toolstart sessionlog_metrics
                     userlogin userlogin_lite webhits
                     summary_user_vals summary_simusage_vals
                     summary_misc_vals jos_xprofiles_metrics
                     bot_useragents domainclass exclude_list)
HASH_TABLES_HUB=(jos_resource_stats jos_resource_stats_tools
                 jos_resource_stats_tools_topvals jos_stats_topvals
                 jos_session_geo)

hash_all() {
    local label="$1" db t h
    : > "$OUT/${label}_hashes.txt"
    # SELECT * without ORDER BY is intentional — shell-sort makes the
    # hash order-independent and works for tables of any column count.
    # auto-inc id columns aren't excluded; if --dry-run accidentally
    # writes a row, both content and id-set change.
    for db_t in $(for t in "${HASH_TABLES_METRICS[@]}"; do echo "metrics:$t"; done; \
                  for t in "${HASH_TABLES_HUB[@]}";     do echo "hub:$t"; done); do
        local db_key="${db_t%%:*}" tname="${db_t##*:}"
        local db_name
        if [ "$db_key" = "metrics" ]; then db_name="$METRICS_DB"; else db_name="$HUB_DB"; fi
        h=$(mysql_test "$db_name" -BN -e "SELECT * FROM \`$tname\`" 2>/dev/null \
            | LC_ALL=C sort \
            | md5sum | awk '{print $1}')
        printf "%s  %s.%s\n" "$h" "$db_key" "$tname" >> "$OUT/${label}_hashes.txt"
    done
}

echo "── set up post-pipeline state ──"
reset_test_dbs >/dev/null
load_fixture "$AB/port_pipeline/seed.sql"
run_new backfill-dnload --start "$MONTH" > "$OUT/00_backfill.log" 2>&1
run_new analyze   --month "$MONTH" --force > "$OUT/01_analyze.log"   2>&1
run_new summarize --month "$MONTH" --force > "$OUT/02_summarize.log" 2>&1

echo "── snapshot before dry-runs ──"
hash_all before

echo "── run a representative set of port commands with --dry-run ──"
# backfill-dnload supports --dry-run via its own arg.
run_new backfill-dnload --start "$MONTH" --dry-run     > "$OUT/dry_01.log" 2>&1
run_new fill-geo        --month "$MONTH" --dry-run     > "$OUT/dry_02.log" 2>&1
run_new analyze         --month "$MONTH" --dry-run --force > "$OUT/dry_03.log" 2>&1
run_new summarize       --month "$MONTH" --dry-run --force > "$OUT/dry_04.log" 2>&1
run_new import-apache   "$AB/fixtures/sample_apache.log" --dry-run > "$OUT/dry_05.log" 2>&1
# logfix-session and middleware-* take --dry-run too
run_new logfix-session   "$MONTH" --dry-run > "$OUT/dry_06.log" 2>&1
run_new middleware-wall  --dry-run          > "$OUT/dry_07.log" 2>&1
run_new middleware-cpu   --dry-run          > "$OUT/dry_08.log" 2>&1
run_new fill-domain      metrics web "$MONTH" --dry-run > "$OUT/dry_09.log" 2>&1
run_new gen-tool-stats   "$MONTH" --dry-run             > "$OUT/dry_10.log" 2>&1
run_new gen-tool-toplists "$MONTH" --dry-run            > "$OUT/dry_11.log" 2>&1
run_new andmore-usage     "$MONTH" --dry-run            > "$OUT/dry_12.log" 2>&1

echo "── snapshot after dry-runs ──"
hash_all after

echo
echo "── diff hashes ──"
if diff -u "$OUT/before_hashes.txt" "$OUT/after_hashes.txt" >/dev/null 2>&1; then
    echo "  PASS  all hashes unchanged after --dry-run invocations"
    echo "PASS"
else
    echo "  FAIL  --dry-run mutated the database"
    diff -u "$OUT/before_hashes.txt" "$OUT/after_hashes.txt"
    echo "FAIL"
    exit 1
fi
