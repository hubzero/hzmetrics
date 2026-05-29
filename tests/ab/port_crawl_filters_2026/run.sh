#!/bin/bash
# 2026-02 audit additions to the import-time crawl-filter chain:
#   - /register dropped unconditionally (629 k hits / 3 mo, distributed bot)
#   - /events/YYYY/ archive walks dropped when YYYY <= log_year - 3
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_crawl_filters.py" -v 2>&1 | tee "$OUT/crawl_filters.log"
