#!/bin/bash
# Pin _DnsResolver's bounded worker-pool: caps in-flight DNS queries at
# `concurrency` (the resolve-dns memory invariant) + functional correctness.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

"$PY" -m unittest "$DIR/test_resolve_dns_pool.py" -v 2>&1 \
    | tee "$OUT/resolve_dns_pool.log"
