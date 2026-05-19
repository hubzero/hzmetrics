#!/bin/bash
# Discovery layer: source-log enumeration across daily/, daily/YYYY/, daily.holding/.
# Pure-Python tests — no DB required.  No fixtures to load.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_discovery.py" -v 2>&1 | tee "$OUT/discovery.log"
