# Shared helpers for tests/ab/port_*/ test wrappers.  Source via:
#   . "$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)/conftest.sh"
# from a test runner — or pass AB_DIR explicitly.

set -euo pipefail

AB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$AB_DIR/../.." && pwd)"
FIXTURES="$AB_DIR/fixtures"
ACCESS_CFG="$FIXTURES/test_access.cfg"
LEGACY_DIR="$REPO/tests/legacy"

# DB connection vars from the test cfg.
DB_PASS=$(grep "^\$db_pass"    "$ACCESS_CFG" | sed -E "s/.*'([^']+)'.*/\1/")
DB_USER=$(grep "^\$db_user"    "$ACCESS_CFG" | sed -E "s/.*'([^']+)'.*/\1/")
HUB_DB=$( grep "^\$hub_db"     "$ACCESS_CFG" | sed -E "s/.*'([^']+)'.*/\1/")
METRICS_DB=$(grep "^\$metrics_db" "$ACCESS_CFG" | sed -E "s/.*'([^']+)'.*/\1/")

# Python that has pymysql / aiodns / aiohttp installed.
PY="${HZMETRICS_PY:-python3.11}"

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

# Run the new hzmetrics.py with the test access.cfg in scope.
run_new() {
    HZMETRICS_ACCESS_CFG="$ACCESS_CFG" \
    HZMETRICS_LOG="$HZMETRICS_LOG" \
    "$PY" "$REPO/hzmetrics.py" "$@"
}
