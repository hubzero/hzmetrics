#!/bin/bash
# do_import_webhits now applies the same shared filter chain as
# do_import_apache (see _filter_apache_row in hzmetrics.py).  This is
# a deliberate divergence from legacy xlogimport_webhits.php, which
# only applied the exclude_list substring filter — the 2026-02 audit
# found ~30 % of bot rows that get dropped from `web` were being
# counted in `webhits`, inflating the dashboard's "Web server hits"
# cell (summary_misc_vals rowid=8) relative to every other cell in
# the same row-set (sessions, downloads, etc., derived from filtered
# web rows).
#
# The legacy A/B that previously lived here is retired: the new code
# is intentionally a strict subset of legacy output.  The golden TSV
# is the canonical expected output now — defer to run_golden.sh.  The
# shared filter chain itself is pinned by tests/ab/port_filter_chain.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/run_golden.sh"
