#!/bin/bash
# Determinism check: run the two integration tests twice and assert
# byte-identical new_*.tsv output between runs.  Catches Python
# set/dict iteration nondeterminism, time-dependent values, floating-
# point accumulation order, and other sources of intra-run drift that
# could pass legacy=new diffs intermittently.
#
# Why pipeline + realdata only: between them they exercise virtually
# every new port in sequence (analyze + summarize), so a single rerun
# of each covers the whole system.  Per-port reruns would be ~5–10×
# slower and mostly redundant given the 8000+ fuzz cases already run.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"

OUT="$DIR/_out"
mkdir -p "$OUT"

# port_realdata is skipped automatically if the production snapshot
# isn't present.
TARGETS=(port_pipeline port_realdata)

fail=0
for name in "${TARGETS[@]}"; do
    port_dir="$AB/$name"
    [ -x "$port_dir/run.sh" ] || { echo "  SKIP  $name (no run.sh)"; continue; }
    if [ "$name" = "port_realdata" ] && [ ! -d "$port_dir/snapshot" ]; then
        echo "  SKIP  $name (no production snapshot available)"
        continue
    fi

    snap1="$OUT/${name}_run1"
    snap2="$OUT/${name}_run2"
    rm -rf "$snap1" "$snap2"
    mkdir -p "$snap1" "$snap2"

    echo
    echo "── $name run 1 ──"
    if ! "$port_dir/run.sh" > "$OUT/${name}_run1.log" 2>&1; then
        echo "  $name run 1 failed — can't check determinism"
        tail -10 "$OUT/${name}_run1.log"
        fail=1
        continue
    fi
    cp "$port_dir/_out/new_"*.tsv "$snap1/" 2>/dev/null

    echo "── $name run 2 ──"
    if ! "$port_dir/run.sh" > "$OUT/${name}_run2.log" 2>&1; then
        echo "  $name run 2 failed — can't check determinism"
        tail -10 "$OUT/${name}_run2.log"
        fail=1
        continue
    fi
    cp "$port_dir/_out/new_"*.tsv "$snap2/" 2>/dev/null

    echo "── diff: run1 vs run2 (${name}) ──"
    if diff -r "$snap1" "$snap2" >/dev/null 2>&1; then
        echo "  PASS  $name (deterministic across reruns)"
    else
        echo "  FAIL  $name (output differs between reruns)"
        diff -r "$snap1" "$snap2" | sed -n '1,30p' || true
        fail=1
    fi
done

[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
