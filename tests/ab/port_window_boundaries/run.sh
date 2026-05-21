#!/bin/bash
# Pin per-stage month-boundary semantics: documents which stage drops
# the YYYY-MM-01 00:00:00 sliver and which stage keeps it, plus the
# expected `(start, stop)` bounds for every period code.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_boundaries.py" -v 2>&1 | tee "$OUT/boundaries.log"
