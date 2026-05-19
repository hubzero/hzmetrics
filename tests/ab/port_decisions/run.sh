#!/bin/bash
# Catchup decision helpers: month_has_source / month_has_data /
# is_month_summarized / is_month_fully_summarized.  Pure-Python tests
# with a fake DB and tmpdir filesystem; no live DB required.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_decisions.py" -v 2>&1 | tee "$OUT/decisions.log"
