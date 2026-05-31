#!/bin/bash
# Run new-code-only defensive tests that do not need tests/legacy/.  This is
# the complement to golden mode: golden protects legacy parity snapshots, while
# these tests protect new behavior around fuzzing, idempotency, dry-run safety,
# empty inputs, determinism, invariants, and CLI contracts.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB_SKIP="${AB_SKIP:-77}"

tests=(
    port_audit
    port_bootstrap
    port_cli_contracts
    port_cmd_run
    port_crawl_filters_2026
    port_decisions
    port_discovery
    port_dnload_backfill_regex
    port_dnload_classify
    port_dryrun
    port_empty_input
    port_fuzz
    port_idempotency
    port_import_atomic
    port_invariants
    port_determinism
    port_msie_filter
    port_lock
    port_month_complete
    port_periods_filter
    port_rebuild_correctness
    port_rebuild_summaries
    port_referer_spam
    port_session_split
    port_state
    port_window_boundaries
)

pass=0
fail=0
skip=0
failed=()

for name in "${tests[@]}"; do
    d="$DIR/$name"
    printf "\n========================================\n"
    printf "Defensive: %s\n" "$name"
    printf "========================================\n"
    if [ ! -x "$d/run.sh" ]; then
        echo "  ${name}: SKIP (no run.sh)"
        skip=$((skip+1))
        continue
    fi

    "$d/run.sh" > /tmp/abd-${name}.log 2>&1
    rc=$?
    if [ "$rc" -eq 0 ]; then
        tail -1 /tmp/abd-${name}.log
        echo "  ${name}: PASS"
        pass=$((pass+1))
    elif [ "$rc" -eq "$AB_SKIP" ]; then
        tail -5 /tmp/abd-${name}.log
        echo "  ${name}: SKIP"
        skip=$((skip+1))
    else
        tail -15 /tmp/abd-${name}.log
        echo "  ${name}: FAIL  (full log: /tmp/abd-${name}.log)"
        fail=$((fail+1))
        failed+=("$name")
    fi
done

printf "\n========================================\n"
printf "Defensive summary: %d pass, %d fail, %d skip\n" "$pass" "$fail" "$skip"
if [ "$fail" -gt 0 ]; then
    printf "Failed:\n"
    for n in "${failed[@]}"; do printf "  %s\n" "$n"; done
    exit 1
fi
