#!/bin/bash
# Run every A/B port test in tests/ab/port_*/.  Reports PASS/FAIL per test
# and an overall summary.  Assumes setup_test_dbs.sh --bootstrap has run.
#
# Each port_*/run.sh handles its own --reset of the test DB; this driver
# just dispatches them in series and tallies results.

set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pass=0
fail=0
skip=0
failed=()
AB_SKIP="${AB_SKIP:-77}"

for d in "$DIR"/port_*/; do
    name=$(basename "$d")
    printf "\n========================================\n"
    printf "Running %s\n" "$name"
    printf "========================================\n"
    "$d/run.sh" > /tmp/ab-${name}.log 2>&1
    rc=$?
    if [ "$rc" -eq 0 ]; then
        tail -1 /tmp/ab-${name}.log
        echo "  ${name}: PASS"
        pass=$((pass+1))
    elif [ "$rc" -eq "$AB_SKIP" ]; then
        tail -5 /tmp/ab-${name}.log
        echo "  ${name}: SKIP"
        skip=$((skip+1))
    else
        tail -10 /tmp/ab-${name}.log
        echo "  ${name}: FAIL  (full log: /tmp/ab-${name}.log)"
        fail=$((fail+1))
        failed+=("$name")
    fi
done

printf "\n========================================\n"
printf "Summary: %d pass, %d fail, %d skip\n" "$pass" "$fail" "$skip"
if [ "$fail" -gt 0 ]; then
    printf "Failed tests:\n"
    for n in "${failed[@]}"; do printf "  %s\n" "$n"; done
    exit 1
fi
