#!/bin/bash
# Create / reset the A/B harness test DBs.
#
# Usage:
#   tests/ab/setup_test_dbs.sh --bootstrap   # one-time: DROP/CREATE DATABASES, GRANT, load schema
#                                            # needs sudo for root mysql (server-level CREATE)
#   tests/ab/setup_test_dbs.sh --reset       # per-test reset: TRUNCATE everything, reload refs
#                                            # no sudo needed
#
# After bootstrap, every other harness step (and per-test reset) runs as the
# MySQL user from HZMETRICS_ACCESS_CFG.

set -euo pipefail
SCRIPTPATH=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$SCRIPTPATH/../.." && pwd)
FIXTURES="$SCRIPTPATH/fixtures"

ACCESS_CFG="${HZMETRICS_ACCESS_CFG:-$FIXTURES/test_access.cfg}"

cfg_value() {
    local name="$1"
    sed -nE "s/^[[:space:]]*\\\$$name[[:space:]]*=[[:space:]]*'([^']*)'.*/\\1/p" "$ACCESS_CFG" | tail -1
}

HUB_TEST_DB="${HUB_TEST_DB:-$(cfg_value hub_db)}"
METRICS_TEST_DB="${METRICS_TEST_DB:-$(cfg_value metrics_db)}"
DB_HOST="${DB_HOST:-$(cfg_value db_host)}"
CFG_DB_USER="$(cfg_value db_user)"
DB_PASS="$(cfg_value db_pass)"

# TEST_USER is an override for unusual local setups.  In the common case, use
# the same DB user hzmetrics.py will read from ACCESS_CFG.
TEST_USER="${TEST_USER:-$CFG_DB_USER}"
DB_HOST="${DB_HOST:-localhost}"

# hzmetrics.py self-relaunches under a newer python (3.10+) if invoked
# under an older one — see _relaunch_if_needed() at the top of hzmetrics.py.
# So we just use plain `python3` here; the script picks the right one.
# Override via HZMETRICS_PY for a venv / pinned version.
PY="${HZMETRICS_PY:-python3}"

# Route hzmetrics.py log writes to a developer-writable file instead of
# /var/log/hubzero/metrics/manage.log (which only the apache user owns).
export HZMETRICS_LOG="${HZMETRICS_LOG:-/tmp/hzmetrics-ab.log}"

mysql_test() {
    mysql -h "$DB_HOST" -u "$TEST_USER" -p"$DB_PASS" "$@"
}

validate_config() {
    local mode="$1"
    if [ -z "$HUB_TEST_DB" ] || [ -z "$METRICS_TEST_DB" ]; then
        echo "ERROR: hub_db and metrics_db must be set in $ACCESS_CFG." >&2
        exit 1
    fi
    if [ -z "$TEST_USER" ]; then
        echo "ERROR: db_user is empty in $ACCESS_CFG." >&2
        echo "Set HZMETRICS_ACCESS_CFG to a test cfg with a real db_user, or set TEST_USER and make the cfg match." >&2
        exit 1
    fi
    if [ "$TEST_USER" != "$CFG_DB_USER" ]; then
        echo "ERROR: TEST_USER ($TEST_USER) differs from db_user in $ACCESS_CFG ($CFG_DB_USER)." >&2
        echo "Patch/use a temporary cfg with the same db_user so hzmetrics.py and mysql agree." >&2
        exit 1
    fi
    if [ "$mode" = "bootstrap" ] && [ -z "$DB_PASS" ]; then
        echo "ERROR: db_pass must be set in $ACCESS_CFG for bootstrap." >&2
        exit 1
    fi
}

usage() {
    sed -n '/^# /{s/^# \?//;p;}' "$0" | head -12
    exit 1
}

# Apply hzmetrics.py setup-db + migrate + reference data into existing metrics DB.
load_schema_and_refs() {
    HZMETRICS_ACCESS_CFG="$ACCESS_CFG" "$PY" "$REPO/hzmetrics.py" setup-db > /tmp/setup-db.log 2>&1 \
        || { echo "setup-db failed:"; cat /tmp/setup-db.log; exit 1; }
    echo "  hzmetrics.py setup-db → $METRICS_TEST_DB"

    mysql_test "$METRICS_TEST_DB" < "$FIXTURES/metrics_reference.sql"
    echo "  reference data → $METRICS_TEST_DB"

    HZMETRICS_ACCESS_CFG="$ACCESS_CFG" "$PY" "$REPO/hzmetrics.py" migrate --apply \
        > /tmp/migrate.log 2>&1 \
        || { echo "migrate failed:"; cat /tmp/migrate.log; exit 1; }
    echo "  migrations applied"
}

bootstrap() {
    validate_config bootstrap
    echo "=== bootstrap: drop / create DATABASES + GRANT (root via unix socket) ==="
    sudo mysql <<EOF
DROP DATABASE IF EXISTS \`$HUB_TEST_DB\`;
DROP DATABASE IF EXISTS \`$METRICS_TEST_DB\`;
CREATE DATABASE \`$HUB_TEST_DB\`     DEFAULT CHARACTER SET utf8mb4;
CREATE DATABASE \`$METRICS_TEST_DB\` DEFAULT CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS '$TEST_USER'@'localhost'
  IDENTIFIED BY '$DB_PASS';
GRANT ALL ON \`$HUB_TEST_DB\`.*     TO '$TEST_USER'@'localhost';
GRANT ALL ON \`$METRICS_TEST_DB\`.* TO '$TEST_USER'@'localhost';
FLUSH PRIVILEGES;
EOF
    echo "  databases created, grants applied."

    echo "=== hub schema (15 tables) ==="
    mysql_test "$HUB_TEST_DB" < "$FIXTURES/hub_schema.sql"
    echo "  hub_schema.sql → $HUB_TEST_DB"

    echo "=== metrics schema + reference data ==="
    load_schema_and_refs
}

reset() {
    validate_config reset
    # Fast per-test reset: truncate every table in both DBs, reload reference data.
    # Doesn't touch schema; assumes bootstrap has already run.
    if ! mysql_test -e "USE $METRICS_TEST_DB" >/dev/null 2>&1; then
        echo "ERROR: $METRICS_TEST_DB doesn't exist or no access."
        echo "Run with --bootstrap first."
        exit 1
    fi

    echo "=== reset: truncate all tables in test DBs ==="
    for db in "$HUB_TEST_DB" "$METRICS_TEST_DB"; do
        # Build TRUNCATE statements for every base table in the DB.
        mysql_test -BN -e "
            SELECT CONCAT('TRUNCATE TABLE \`', table_schema, '\`.\`', table_name, '\`;')
            FROM information_schema.tables
            WHERE table_schema='$db' AND table_type='BASE TABLE';
        " | mysql_test
    done
    echo "  truncated both DBs"

    echo "=== reload metrics reference data ==="
    mysql_test "$METRICS_TEST_DB" < "$FIXTURES/metrics_reference.sql"
    echo "  reference data → $METRICS_TEST_DB"
}

case "${1:-}" in
    --bootstrap|bootstrap) bootstrap ;;
    --reset|reset)         reset ;;
    -h|--help|"")          usage ;;
    *) echo "Unknown arg: $1" >&2; usage ;;
esac

echo
echo "Done.  cfg: $ACCESS_CFG"
echo "Use:  export HZMETRICS_ACCESS_CFG=$ACCESS_CFG"
