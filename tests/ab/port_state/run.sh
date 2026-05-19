#!/bin/bash
# pipeline_state: DB-backed orchestrator state.
# Pure-Python tests with a monkeypatched DB layer — no live DB required.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_state.py" -v 2>&1 | tee "$OUT/state.log"
