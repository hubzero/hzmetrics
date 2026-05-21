#!/bin/bash
# Pin behavior of the sessions= split in _do_usage_metrics_stage /
# do_analyze.  Current-month daily ticks must skip logfix-session +
# websessions-bound steps; complete-month ticks (catchup, rebuild,
# month-close) must include them.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_session_split.py" -v 2>&1 | tee "$OUT/session_split.log"
