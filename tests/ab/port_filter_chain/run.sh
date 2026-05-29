#!/bin/bash
# Pin the shared apache-log filter chain (_filter_apache_row) that
# both do_import_apache and do_import_webhits call.  Sharing the chain
# is what keeps webhits' SUM(hits) consistent with filtered `web`
# row counts — see _filter_apache_row's docstring for the audit history.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_filter_chain.py" -v 2>&1 | tee "$OUT/filter_chain.log"
