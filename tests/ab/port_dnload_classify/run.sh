#!/bin/bash
# dnload classification: regression guard for the long-standing
# importer bug where web.dnload stayed NULL on every row.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_dnload.py" -v 2>&1 | tee "$OUT/dnload.log"
