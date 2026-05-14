#!/bin/bash
# port_fuzz: property-based / randomized A/B fixtures.  Drives each
# fuzz_*.sh in series with fixed seeds so this is deterministic.  For
# ad-hoc fuzzing with a different seed, run the individual fuzz_*.sh
# scripts directly.
#
# Default budget per harness is sized so the suite runs in ~2 minutes.
# Bump iterations on the command line of the individual fuzz_*.sh to
# burn more cases when looking for new bugs.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fail=0

# Fixed seed_base = 1 keeps this run reproducible.
echo "── fuzz_fill_domain (20 iter × 100 hosts) ──"
if ! "$DIR/fuzz_fill_domain.sh" 20 100 1; then
    fail=1
fi

echo
echo "── fuzz_logfix_session (20 iter × 100 events) ──"
if ! "$DIR/fuzz_logfix_session.sh" 20 100 1; then
    fail=1
fi

echo
echo "── fuzz_import_apache (20 iter × 100 lines) ──"
if ! "$DIR/fuzz_import_apache.sh" 20 100 1; then
    fail=1
fi

echo
echo "── fuzz_import_auth (20 iter × 100 lines) ──"
if ! "$DIR/fuzz_import_auth.sh" 20 100 1; then
    fail=1
fi

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
