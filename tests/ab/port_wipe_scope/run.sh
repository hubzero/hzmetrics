#!/bin/bash
# Integration test: _wipe_month_data deletes exactly the target month's
# rows from the base + summary tables, and leaves other months untouched.
#
# Hits the real test DB so we're testing the actual DELETE SQL, not a
# mock.  conftest.sh resolves the right access.cfg for the host
# (CI-patched test_access.cfg, the per-hub starter, etc.).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"

. "$AB/conftest.sh"

OUT="$DIR/_out"
mkdir -p "$OUT"

# Seed three months — wipe the middle one and confirm the other two
# survive.  Tables touched: web, userlogin, webhits, websessions, plus
# the four summary_*_vals.
seed() {
    mysql_test "$METRICS_DB" <<'SQL'
DELETE FROM web         WHERE datetime >= '2024-06-01' AND datetime < '2024-09-01';
DELETE FROM userlogin   WHERE datetime >= '2024-06-01' AND datetime < '2024-09-01';
DELETE FROM webhits     WHERE datetime >= '2024-06-01' AND datetime < '2024-09-01';
DELETE FROM websessions WHERE datetime >= '2024-06-01' AND datetime < '2024-09-01';
DELETE FROM summary_user_vals     WHERE datetime IN ('2024-06-00','2024-07-00','2024-08-00');
DELETE FROM summary_misc_vals     WHERE datetime IN ('2024-06-00','2024-07-00','2024-08-00');
DELETE FROM summary_simusage_vals WHERE datetime IN ('2024-06-00','2024-07-00','2024-08-00');
DELETE FROM summary_andmore_vals  WHERE datetime IN ('2024-06-00','2024-07-00','2024-08-00');

-- Three months of fake activity.  Bare-minimum schemas; we're testing
-- DELETE scope, not the rows' semantic meaning.
INSERT INTO web (id, datetime, ip, content) VALUES
    (90000601, '2024-06-15 12:00:00', '1.1.1.1', '/keep-june'),
    (90000701, '2024-07-15 12:00:00', '2.2.2.2', '/wipe-me'),
    (90000702, '2024-07-20 12:00:00', '3.3.3.3', '/wipe-me-too'),
    (90000801, '2024-08-15 12:00:00', '4.4.4.4', '/keep-aug');

INSERT INTO userlogin (datetime, user, uidNumber, ip, action) VALUES
    ('2024-06-15 12:00:00', 'alice', 1, '1.1.1.1', 'login'),
    ('2024-07-15 12:00:00', 'bob',   2, '2.2.2.2', 'login'),
    ('2024-07-20 12:00:00', 'carol', 3, '3.3.3.3', 'simulation'),
    ('2024-08-15 12:00:00', 'dave',  4, '4.4.4.4', 'login');

INSERT INTO webhits (datetime, hits) VALUES
    ('2024-06-15 12:00:00', 100),
    ('2024-07-15 12:00:00', 200),
    ('2024-08-15 12:00:00', 300);

INSERT INTO websessions (id, datetime, ipcountry, ip) VALUES
    (90000601, '2024-06-15 12:00:00', 'US', '1.1.1.1'),
    (90000701, '2024-07-15 12:00:00', 'US', '2.2.2.2'),
    (90000801, '2024-08-15 12:00:00', 'US', '4.4.4.4');

-- summary_*_vals: one row per (table, month) with a sentinel period=1.
INSERT INTO summary_user_vals     (rowid, colid, datetime, period, value, valfmt) VALUES
    (1, 1, '2024-06-00', 1, 10, 0),
    (1, 1, '2024-07-00', 1, 20, 0),
    (1, 1, '2024-08-00', 1, 30, 0);

INSERT INTO summary_misc_vals     (rowid, colid, datetime, period, value, valfmt) VALUES
    (1, 1, '2024-06-00', 1, 11, 0),
    (1, 1, '2024-07-00', 1, 21, 0),
    (1, 1, '2024-08-00', 1, 31, 0);

INSERT INTO summary_simusage_vals (rowid, colid, datetime, period, value, valfmt) VALUES
    (1, 1, '2024-06-00', 1, 12, 0),
    (1, 1, '2024-07-00', 1, 22, 0),
    (1, 1, '2024-08-00', 1, 32, 0);

INSERT INTO summary_andmore_vals  (rowid, colid, datetime, period, value, valfmt) VALUES
    (1, 1, '2024-06-00', 1, 13, 0),
    (1, 1, '2024-07-00', 1, 23, 0),
    (1, 1, '2024-08-00', 1, 33, 0);
SQL
}

count() {
    local sql="$1"
    mysql_test "$METRICS_DB" -BN -e "$sql"
}

echo "=== seed three months of test data ==="
seed
for tbl in web userlogin webhits websessions \
           summary_user_vals summary_misc_vals \
           summary_simusage_vals summary_andmore_vals; do
    case "$tbl" in
        summary_*) where="datetime IN ('2024-06-00','2024-07-00','2024-08-00')";;
        *)         where="datetime >= '2024-06-01' AND datetime < '2024-09-01'";;
    esac
    n=$(count "SELECT COUNT(*) FROM $tbl WHERE $where")
    echo "  $tbl seeded: $n row(s)"
done

echo ""
echo "=== invoke _wipe_month_data('2024-07') ==="
HZMETRICS_LOG=/tmp/hzmetrics-ab.log "$PY" <<PYEOF
import sys
sys.path.insert(0, "$AB/../..")
import hzmetrics as hz
hz._wipe_month_data("2024-07")
PYEOF

echo ""
echo "=== verify scope ==="
fail=0

# July rows should be gone
for tbl in web userlogin webhits websessions; do
    n=$(count "SELECT COUNT(*) FROM $tbl WHERE datetime >= '2024-07-01' AND datetime < '2024-08-01'")
    if [ "$n" = "0" ]; then
        echo "  PASS  $tbl July empty"
    else
        echo "  FAIL  $tbl July has $n row(s), expected 0"
        fail=1
    fi
done
for tbl in summary_user_vals summary_misc_vals summary_simusage_vals summary_andmore_vals; do
    n=$(count "SELECT COUNT(*) FROM $tbl WHERE datetime = '2024-07-00'")
    if [ "$n" = "0" ]; then
        echo "  PASS  $tbl 2024-07-00 empty"
    else
        echo "  FAIL  $tbl 2024-07-00 has $n row(s), expected 0"
        fail=1
    fi
done

# June and August rows should be untouched
check_preserved() {
    local label="$1" sql="$2"
    local n
    n=$(count "$sql")
    if [ "$n" -ge "1" ]; then
        echo "  PASS  $label preserved ($n row(s))"
    else
        echo "  FAIL  $label was wiped (expected ≥1, got $n)"
        fail=1
    fi
}

check_preserved "web 2024-06"       "SELECT COUNT(*) FROM web WHERE datetime >= '2024-06-01' AND datetime < '2024-07-01'"
check_preserved "web 2024-08"       "SELECT COUNT(*) FROM web WHERE datetime >= '2024-08-01' AND datetime < '2024-09-01'"
check_preserved "userlogin 2024-06" "SELECT COUNT(*) FROM userlogin WHERE datetime >= '2024-06-01' AND datetime < '2024-07-01'"
check_preserved "userlogin 2024-08" "SELECT COUNT(*) FROM userlogin WHERE datetime >= '2024-08-01' AND datetime < '2024-09-01'"
check_preserved "summary_user_vals 2024-06" "SELECT COUNT(*) FROM summary_user_vals WHERE datetime = '2024-06-00'"
check_preserved "summary_user_vals 2024-08" "SELECT COUNT(*) FROM summary_user_vals WHERE datetime = '2024-08-00'"
check_preserved "summary_misc_vals 2024-06" "SELECT COUNT(*) FROM summary_misc_vals WHERE datetime = '2024-06-00'"
check_preserved "summary_andmore_vals 2024-08" "SELECT COUNT(*) FROM summary_andmore_vals WHERE datetime = '2024-08-00'"

echo ""
if [ "$fail" -eq 0 ]; then
    echo "PASS"
    # Clean up our seed rows
    mysql_test "$METRICS_DB" <<'SQL'
DELETE FROM web         WHERE datetime >= '2024-06-01' AND datetime < '2024-09-01';
DELETE FROM userlogin   WHERE datetime >= '2024-06-01' AND datetime < '2024-09-01';
DELETE FROM webhits     WHERE datetime >= '2024-06-01' AND datetime < '2024-09-01';
DELETE FROM websessions WHERE datetime >= '2024-06-01' AND datetime < '2024-09-01';
DELETE FROM summary_user_vals     WHERE datetime IN ('2024-06-00','2024-07-00','2024-08-00');
DELETE FROM summary_misc_vals     WHERE datetime IN ('2024-06-00','2024-07-00','2024-08-00');
DELETE FROM summary_simusage_vals WHERE datetime IN ('2024-06-00','2024-07-00','2024-08-00');
DELETE FROM summary_andmore_vals  WHERE datetime IN ('2024-06-00','2024-07-00','2024-08-00');
SQL
    exit 0
else
    echo "FAIL"
    exit 1
fi
