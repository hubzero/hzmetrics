# Shared helpers for tests/ab/port_*/ test wrappers.  Source via:
#   . "$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)/conftest.sh"
# from a test runner — or pass AB_DIR explicitly.

# Intentionally do not set shell options here.  The caller owns whether it
# wants `set -e`; several runners aggregate failures manually and need to keep
# going after an individual command fails.

AB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$AB_DIR/../.." && pwd)"
FIXTURES="$AB_DIR/fixtures"
LEGACY_DIR="$REPO/tests/legacy"

# Resolve the test access.cfg:
#   1. HZMETRICS_ACCESS_CFG env var wins outright (operator override).
#   2. Else use $FIXTURES/test_access.cfg if it has a non-empty db_user
#      (operator filled in the template for this host).
#   3. Else fall back to the first $FIXTURES/test_access_*.cfg that has
#      a non-empty db_user — the per-hub starter cfgs.  This lets a
#      fresh checkout on a hub with a matching test_access_<hub>.cfg
#      "just work" without operator setup.
#   4. Else point at the empty template and let downstream fail with a
#      clear error from setup_test_dbs.sh / validate_config.
_has_db_user() {
    [ -f "$1" ] && grep -qE "^\\\$db_user[[:space:]]*=[[:space:]]*'[^']" "$1"
}
ACCESS_CFG="${HZMETRICS_ACCESS_CFG:-}"
if [ -z "$ACCESS_CFG" ]; then
    if _has_db_user "$FIXTURES/test_access.cfg"; then
        ACCESS_CFG="$FIXTURES/test_access.cfg"
    else
        for _cand in "$FIXTURES"/test_access_*.cfg; do
            if _has_db_user "$_cand"; then
                ACCESS_CFG="$_cand"
                break
            fi
        done
        : "${ACCESS_CFG:=$FIXTURES/test_access.cfg}"
    fi
fi

AB_SKIP=77

cfg_value() {
    local name="$1"
    sed -nE "s/^[[:space:]]*\\\$$name[[:space:]]*=[[:space:]]*'([^']*)'.*/\\1/p" "$ACCESS_CFG" | tail -1
}

# DB connection vars from the test cfg.
DB_PASS=$(cfg_value db_pass)
DB_USER=$(cfg_value db_user)
HUB_DB=$(cfg_value hub_db)
METRICS_DB=$(cfg_value metrics_db)

# Python that has pymysql / aiodns / aiohttp installed.  Tests run the
# script-under-test in two ways:
#   1. Subprocess via run_new(): `$PY hzmetrics.py …`.  In this mode
#      hzmetrics.py self-relaunches if $PY is too old, so any python3.X
#      that exists on PATH would work.
#   2. Direct `import hzmetrics` inside a test process.  Self-relaunch
#      is gated on `__name__ == "__main__"` (relaunching the test
#      runner would execv it with the wrong argv), so we need an
#      interpreter that can actually import the module — i.e. one that
#      satisfies hzmetrics's runtime floor of Python 3.10+.
#
# Resolve in priority order: $HZMETRICS_PY > `python3` if ≥ 3.10 > the
# first python3.10+/python3.11+/… found on PATH.
_modern_py() {
    local cand
    if [ -n "${HZMETRICS_PY:-}" ]; then
        echo "$HZMETRICS_PY"; return 0
    fi
    if command -v python3 >/dev/null 2>&1 \
       && python3 -c "import sys; sys.exit(sys.version_info < (3, 10))" 2>/dev/null
    then
        echo python3; return 0
    fi
    # Prefer newest; iterate high→low so a future python3.14 wins over 3.10.
    for cand in python3.14 python3.13 python3.12 python3.11 python3.10; do
        if command -v "$cand" >/dev/null 2>&1; then
            echo "$cand"; return 0
        fi
    done
    echo python3   # fall through; tests will fail with a clear ImportError
}
PY="$(_modern_py)"

export HZMETRICS_ACCESS_CFG="$ACCESS_CFG"
export HZMETRICS_LOG="${HZMETRICS_LOG:-/tmp/hzmetrics-ab.log}"

# Convenience wrappers.
mysql_test() {
    mysql -h localhost -u "$DB_USER" -p"$DB_PASS" "$@"
}

# Truncate-and-reload everything in both test DBs.  Cheap — ~1 second.
reset_test_dbs() {
    "$AB_DIR/setup_test_dbs.sh" --reset > /tmp/ab-reset.log 2>&1 \
        || { echo "reset failed:"; cat /tmp/ab-reset.log; return 1; }
}

# Load a test's per-fixture SQL (web rows, exclude_list rows, etc).
# seed.sql files historically hard-code `USE foo_test` / `USE foo_metrics_test`
# (the original placeholder DB names).  We rewrite those USE statements on the
# fly to the active HUB_DB / METRICS_DB so the same seeds work on whatever DB
# names the operator's test_access.cfg points at — e.g. geodynamics_test /
# geodynamics_metrics_test on this hub.
load_fixture() {
    local sql="$1"
    sed -e "s/^USE foo_test;/USE \`$HUB_DB\`;/" \
        -e "s/^USE foo_metrics_test;/USE \`$METRICS_DB\`;/" \
        "$sql" | mysql_test
}

# Dump a metrics-side table to TSV, deterministically ordered by id.
# Args: table, output_file
dump_table_tsv() {
    local table="$1" out="$2"
    mysql_test "$METRICS_DB" -BN -e "
        SELECT * FROM \`$table\` ORDER BY id;
    " > "$out"
}

# dump_full <table> <db> <order_by_cols> [extra_exclude_csv]
# Dump every column of a table, in schema order, excluding 'id',
# 'processed_on', and any extra columns named in <extra_exclude_csv>.
# Float / double / decimal columns are ROUND()ed to 6 digits to absorb
# PHP↔Python double-stringification noise.  ORDER BY <order_by_cols>
# pins row order so the diff is stable.
dump_full() {
    local table="$1" db="$2" order_by="$3" extra="${4:-}"
    local exclude_in="'id','processed_on'"
    if [ -n "$extra" ]; then
        local x
        for x in $(echo "$extra" | tr ',' ' '); do
            exclude_in="$exclude_in,'$x'"
        done
    fi
    local cols
    cols=$(mysql_test "$db" -BN -e "
        SELECT GROUP_CONCAT(
            CASE
              WHEN DATA_TYPE IN ('float','double','decimal')
                THEN CONCAT('ROUND(\`', COLUMN_NAME, '\`, 6)')
              ELSE CONCAT('\`', COLUMN_NAME, '\`')
            END
          ORDER BY ORDINAL_POSITION SEPARATOR ', ')
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA='$db' AND TABLE_NAME='$table'
          AND COLUMN_NAME NOT IN ($exclude_in)
    ")
    if [ -z "$cols" ]; then
        echo "dump_full: no columns for $db.$table (table missing?)" >&2
        return 1
    fi
    mysql_test "$db" -BN -e "SELECT $cols FROM \`$table\` ORDER BY $order_by"
}

# Run a legacy PHP script with the test access.cfg in scope.
# Args: script_relpath_in_legacy/, then script args.
run_legacy_php() {
    local script="$1"; shift
    HZMETRICS_ACCESS_CFG="$ACCESS_CFG" php "$LEGACY_DIR/$script" "$@"
}

run_legacy_perl() {
    local script="$1"; shift
    HZMETRICS_ACCESS_CFG="$ACCESS_CFG" perl "$LEGACY_DIR/$script" "$@"
}

run_legacy_sh() {
    local script="$1"; shift
    HZMETRICS_ACCESS_CFG="$ACCESS_CFG" bash "$LEGACY_DIR/$script" "$@"
}

# Run the new hzmetrics.py with the test access.cfg in scope.
run_new() {
    HZMETRICS_ACCESS_CFG="$ACCESS_CFG" \
    HZMETRICS_LOG="$HZMETRICS_LOG" \
    "$PY" "$REPO/hzmetrics.py" "$@"
}

# Compare every _out/new_<basename> against port_X/golden/<basename>.
# Used by per-port run_golden.sh scripts to simulate the "legacy/ is
# gone" world: we ran the new code only, and we diff against a frozen
# snapshot of what legacy used to produce.
# Args: dir, then one or more basenames (matching golden/<basename>
# and _out/new_<basename>).  Exits 1 if any file mismatches.
golden_diff() {
    local dir="$1"; shift
    local fail=0 f new gold
    for f in "$@"; do
        new="$dir/_out/new_$f"
        gold="$dir/golden/$f"
        if [ ! -f "$gold" ]; then
            echo "  ERROR  missing golden: golden/$f"; fail=1; continue
        fi
        if [ ! -f "$new" ]; then
            echo "  ERROR  missing new:    _out/new_$f"; fail=1; continue
        fi
        if diff -q "$gold" "$new" >/dev/null 2>&1; then
            echo "  PASS   $f"
        else
            echo "  FAIL   $f (first 20 diff lines)"
            diff -u "$gold" "$new" | sed -n '1,20p' || true
            fail=1
        fi
    done
    [ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; return 1; }
}
