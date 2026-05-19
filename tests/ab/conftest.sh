# Shared helpers for tests/ab/port_*/ test wrappers.  Source via:
#   . "$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)/conftest.sh"
# from a test runner — or pass AB_DIR explicitly.

# Intentionally do not set shell options here.  The caller owns whether it
# wants `set -e`; several runners aggregate failures manually and need to keep
# going after an individual command fails.

AB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$AB_DIR/../.." && pwd)"
FIXTURES="$AB_DIR/fixtures"
ACCESS_CFG="${HZMETRICS_ACCESS_CFG:-$FIXTURES/test_access.cfg}"
LEGACY_DIR="$REPO/tests/legacy"

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

# Python that has pymysql / aiodns / aiohttp installed.
PY="${HZMETRICS_PY:-python3}"

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
load_fixture() {
    local sql="$1"
    mysql_test < "$sql"
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
