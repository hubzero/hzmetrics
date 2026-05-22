#!/bin/bash
# PID lock file format + stale-entry diagnostics:
#   acquire_lock writes "<pid> <init_start_epoch> <iso>", uses
#   /proc/stat btime + /proc/1/stat starttime to capture host reboot
#   and container restart in one field, and runs a non-blocking
#   diagnose-stale step before overwriting any prior entry.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_lock.py" -v 2>&1 | tee "$OUT/lock.log"
