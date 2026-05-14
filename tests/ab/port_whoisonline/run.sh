#!/bin/bash
# A/B compare legacy xlogfix_whoisonline.php vs new hzmetrics.py whoisonline.
# Network-dependent: hits real DNS + GeoIP.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

# whoisonline writes <hub_dir>/app/site/stats/maps/whoisonline.xml
# test_access.cfg sets hub_dir = /tmp, so the file lands at:
MAP_DIR="/tmp/app/site/stats/maps"
mkdir -p "$MAP_DIR"
XML_OUT="$MAP_DIR/whoisonline.xml"

run_side() {
    local label="$1" invoker="$2"; shift 2
    echo
    echo "=== $label: $* ==="
    reset_test_dbs
    load_fixture "$DIR/seed.sql"
    rm -f "$XML_OUT"
    "$invoker" "$@" > "$OUT/${label}_stdout.log" 2>&1 || {
        echo "  $label invocation failed; log:"
        tail -10 "$OUT/${label}_stdout.log"
        return 1
    }
    # Capture XML output + jos_session_geo state.  The `time` column varies
    # between runs (seconds-since-epoch from when the seed loaded), so we
    # exclude it from the table diff.
    if [ -f "$XML_OUT" ]; then
        cp "$XML_OUT" "$OUT/${label}_whoisonline.xml"
    else
        : > "$OUT/${label}_whoisonline.xml"
    fi
    mysql_test "$HUB_DB" -BN -e "
        SELECT ip, username, guest, userid, host, domain,
               countrySHORT, countryLONG, ipCITY, ipLATITUDE, ipLONGITUDE, bot
        FROM jos_session_geo ORDER BY ip
    " > "$OUT/${label}_session_geo.tsv"
    echo "  wrote $OUT/${label}_whoisonline.xml + session_geo.tsv"
}

run_side legacy run_legacy_php xlogfix_whoisonline.php
run_side new    run_new        whoisonline

echo
fail=0
echo "=== diff: whoisonline.xml ==="
if diff -u "$OUT/legacy_whoisonline.xml" "$OUT/new_whoisonline.xml"; then
    echo "  PASS"
else
    echo "  FAIL"
    fail=1
fi
echo "=== diff: jos_session_geo ==="
if diff -u "$OUT/legacy_session_geo.tsv" "$OUT/new_session_geo.tsv"; then
    echo "  PASS"
else
    echo "  FAIL"
    fail=1
fi

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
