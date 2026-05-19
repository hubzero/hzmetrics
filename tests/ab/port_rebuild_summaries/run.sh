#!/bin/bash
# Phase E: rebuild-summaries CLI + cmd_status orchestrator extension.
# Pure-Python tests with monkey-patched DB / FS / state layers.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_rebuild_summaries.py" -v 2>&1 | tee "$OUT/rebuild_summaries.log"
