#!/bin/bash
# Pin _is_referer_spam behavior for both no-slash and slash-variant
# forms of /login?return= and /resources/browse?...
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_referer_spam.py" -v 2>&1 | tee "$OUT/referer_spam.log"
