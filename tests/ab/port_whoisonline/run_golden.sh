#!/bin/bash
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
MAP_DIR="/tmp/app/site/stats/maps"
mkdir -p "$MAP_DIR"
XML_OUT="$MAP_DIR/whoisonline.xml"

reset_test_dbs > /dev/null
load_fixture "$DIR/seed.sql"
rm -f "$XML_OUT"
run_new whoisonline > "$OUT/new_stdout.log" 2>&1
[ -f "$XML_OUT" ] && cp "$XML_OUT" "$OUT/new_whoisonline.xml" || : > "$OUT/new_whoisonline.xml"
dump_full jos_session_geo "$HUB_DB" "ip, session_id" "time" > "$OUT/new_session_geo.tsv"

golden_diff "$DIR" whoisonline.xml session_geo.tsv
