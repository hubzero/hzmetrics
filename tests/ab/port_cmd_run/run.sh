#!/bin/bash
# Three-mode cmd_run orchestrator: mode dispatch + transitions +
# catchup decision-matrix routing.  Pure-Python tests with monkey-patched
# DB, filesystem, and state layers; no live DB required.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_cmd_run.py" -v 2>&1 | tee "$OUT/cmd_run.log"
