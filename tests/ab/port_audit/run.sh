#!/bin/bash
# Audit command: median-based anomaly detection + range-collapsed
# remediation output.  Pure-Python tests with mocked mysql_query — no
# live DB required.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_audit.py" -v 2>&1 | tee "$OUT/audit.log"
