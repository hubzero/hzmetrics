#!/bin/bash
# Direct unit tests for is_month_complete — the data-driven completeness
# check that replaced the legacy "days_in > 5" calendar fallback.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_complete.py" -v 2>&1 | tee "$OUT/complete.log"
