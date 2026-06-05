#!/bin/bash
# Pin the 2025-05 plantingscience-survey filter additions:
#   - /robots.txt exclusion in _is_excluded_url (_ROBOTS_RE)
#   - bare HTTP-client UAs in BOT_UA_FILTERS / _ua_is_bot
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_robots_httpclient_filters.py" -v 2>&1 \
    | tee "$OUT/robots_httpclient_filters.log"
