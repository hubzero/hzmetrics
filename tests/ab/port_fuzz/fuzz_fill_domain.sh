#!/bin/bash
# Fuzz fill-domain: generate N random hostnames, run legacy + new
# fill-domain, diff the resulting domain values.  Repeat M iterations
# with different seeds until either the budget runs out or a divergence
# is found.
#
# Usage:
#   fuzz_fill_domain.sh [iterations [hosts_per_iteration [seed_base]]]
#
# Defaults: 50 iterations × 200 hosts each (10k total cases per run).
#
# On divergence: prints the seed so the case is reproducible, and dumps
# the failing diff.  Exit code 0 = all clean.

set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

ITERS="${1:-50}"
HOSTS="${2:-200}"
SEED_BASE="${3:-$(date +%s)}"
OUT="$DIR/_out"
mkdir -p "$OUT"

echo "fuzz_fill_domain: $ITERS iter × $HOSTS hosts each, seed_base=$SEED_BASE"
echo

passed=0
for i in $(seq 1 "$ITERS"); do
    seed=$((SEED_BASE + i))
    # Generate the random fixture
    "$PY" "$DIR/gen_hostnames.py" "$HOSTS" "$seed" > "$OUT/seed_${seed}.sql"

    # ── legacy run ───────────────────────────────────────────────
    reset_test_dbs > /dev/null
    mysql_test < "$OUT/seed_${seed}.sql" > /dev/null
    run_legacy_php xlogfix_domain.php metrics web 2025-07 \
        > "$OUT/seed_${seed}_legacy.log" 2>&1
    mysql_test "$METRICS_DB" -BN -e "
        SELECT ip, host, domain FROM web ORDER BY ip;
    " > "$OUT/seed_${seed}_legacy.tsv"

    # ── new run ──────────────────────────────────────────────────
    reset_test_dbs > /dev/null
    mysql_test < "$OUT/seed_${seed}.sql" > /dev/null
    run_new fill-domain metrics web 2025-07 \
        > "$OUT/seed_${seed}_new.log" 2>&1
    mysql_test "$METRICS_DB" -BN -e "
        SELECT ip, host, domain FROM web ORDER BY ip;
    " > "$OUT/seed_${seed}_new.tsv"

    # ── diff ─────────────────────────────────────────────────────
    if ! diff -q "$OUT/seed_${seed}_legacy.tsv" "$OUT/seed_${seed}_new.tsv" >/dev/null 2>&1; then
        echo
        echo "FAIL  iteration $i  seed=$seed"
        echo "  reproduce: $PY $DIR/gen_hostnames.py $HOSTS $seed > /tmp/fuzz.sql"
        echo
        echo "  first 20 diff lines:"
        diff "$OUT/seed_${seed}_legacy.tsv" "$OUT/seed_${seed}_new.tsv" | sed -n '1,20p' || true
        echo
        echo "  $passed / $i iterations passed before this failure"
        exit 1
    fi
    passed=$((passed + 1))
    if [ $((passed % 10)) -eq 0 ]; then
        printf '.'
    fi
    # Tidy: remove per-seed files immediately if passing to keep _out small
    rm -f "$OUT/seed_${seed}.sql" "$OUT/seed_${seed}_legacy."{tsv,log} "$OUT/seed_${seed}_new."{tsv,log}
done

echo
echo "PASS — all $ITERS iterations × $HOSTS hosts ($(($ITERS * $HOSTS)) cases) clean"
