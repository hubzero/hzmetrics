#!/bin/bash
# Date-bound MSIE-Trident UA filter regression tests.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_msie.py" -v 2>&1 | tee "$OUT/msie.log"
