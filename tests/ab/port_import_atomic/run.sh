#!/bin/bash
# Per-file atomic-import + crash recovery via imported_sources +
# forget-import CLI.  Pin the schema migration, the import-function
# conn= contract, the atomic-helper transaction/move lifecycle, and
# the forget-import data-DELETE behavior.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_import_atomic.py" -v 2>&1 | tee "$OUT/import_atomic.log"
