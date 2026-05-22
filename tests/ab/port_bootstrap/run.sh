#!/bin/bash
# Self-bootstrap + init + doctor regression tests.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_bootstrap.py" -v 2>&1 | tee "$OUT/bootstrap.log"
