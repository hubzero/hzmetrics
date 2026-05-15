#!/bin/bash
# Golden-mode round of the A/B suite: run only the new ports and diff
# against frozen snapshots of what legacy used to produce.  Simulates
# the world where tests/legacy/ has been removed from the repo.
#
# Each port_X/run_golden.sh is a per-port runner that:
#   - reset_test_dbs + loads its usual fixture
#   - runs ONLY the new (hzmetrics.py) side
#   - dumps output tables
#   - diffs against port_X/golden/<basename>
#
# A port without a run_golden.sh is skipped (e.g. port_fuzz,
# port_invariants, port_idempotency, port_dryrun, port_empty_input,
# port_determinism — those tests don't compare against legacy at all
# and don't need a golden-mode variant).
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pass=0
fail=0
skip=0
failed=()

for d in "$DIR"/port_*/; do
    name=$(basename "$d")
    if [ ! -x "$d/run_golden.sh" ]; then
        skip=$((skip+1))
        continue
    fi
    printf "\n========================================\n"
    printf "Golden: %s\n" "$name"
    printf "========================================\n"
    if "$d/run_golden.sh" > /tmp/abg-${name}.log 2>&1; then
        tail -1 /tmp/abg-${name}.log
        echo "  ${name}: PASS"
        pass=$((pass+1))
    else
        tail -15 /tmp/abg-${name}.log
        echo "  ${name}: FAIL  (full log: /tmp/abg-${name}.log)"
        fail=$((fail+1))
        failed+=("$name")
    fi
done

printf "\n========================================\n"
printf "Golden summary: %d pass, %d fail, %d skip (no run_golden.sh)\n" "$pass" "$fail" "$skip"
if [ "$fail" -gt 0 ]; then
    printf "Failed:\n"
    for n in "${failed[@]}"; do printf "  %s\n" "$n"; done
    exit 1
fi
