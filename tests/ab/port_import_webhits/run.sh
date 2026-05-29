#!/bin/bash
# `webhits` is now a derived table populated inline by do_import_apache
# (one row per day with COUNT(*) of kept web rows for that day, same
# loop and same transaction as the web INSERTs).  do_import_webhits as
# a standalone parser was removed in the rebuild-webhits refactor —
# the operator-driven regenerate-from-`web` path is `rebuild-webhits`
# [--month YYYY-MM | --all].
#
# The legacy A/B that previously lived here is retired: the new code
# produces a strict subset of legacy `xlogimport_webhits.php` output
# (more filters applied).  The golden TSV is the canonical expected
# output now — defer to run_golden.sh.  run_golden.sh exercises the
# inline path by running import-apache and asserting the resulting
# webhits rows match.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/run_golden.sh"
