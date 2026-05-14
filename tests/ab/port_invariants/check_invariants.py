#!/usr/bin/env python3
"""Cross-table invariants that must hold after a metrics run, regardless
of legacy/new parity.  Each invariant is code-backed — a comment cites
the source-of-truth line in legacy/port that establishes it.

Run after a pipeline pass; exits non-zero on any violation.

Usage: check_invariants.py [access_cfg_path]
"""
import os
import re
import sys

try:
    import pymysql
except ImportError:
    print("FAIL: pymysql not installed", file=sys.stderr)
    sys.exit(2)


def parse_access_cfg(path):
    cfg = {}
    with open(path) as f:
        for line in f:
            m = re.match(r"\$(\w+)\s*=\s*'([^']*)'", line.strip())
            if m:
                cfg[m.group(1)] = m.group(2)
    return cfg


def connect(cfg, db):
    return pymysql.connect(
        host='localhost',
        user=cfg['db_user'],
        password=cfg['db_pass'],
        database=db,
        charset='utf8mb4',
    )


# Each invariant is (label, db_key, sql, predicate_on_rows).  The
# predicate receives the full SELECT result; it returns (ok, detail).
INVARIANTS = []


def inv(label, db_key, sql, check):
    INVARIANTS.append((label, db_key, sql, check))


# -- 1.  summary_user_vals rowid=1 == SUM(rowid IN (6,7,8))
#    Established by xlogfix_summary.php:174 "Calculating totals from
#    rows 6, 7 and 8" — the total-users row is explicitly the sum of
#    registered (6) + unregistered (7) + bot/other (8).  Any (datetime,
#    period, colid, valfmt) where this fails indicates one of the four
#    rows was written by a query disconnected from the sum.
inv("summary_user_vals[1] = SUM([6,7,8]) per cell",
    "metrics_db",
    """
    SELECT s1.datetime, s1.period, s1.colid, s1.valfmt,
           s1.value AS total_val,
           COALESCE(SUM(s2.value), 0) AS sum_678
    FROM summary_user_vals s1
    LEFT JOIN summary_user_vals s2
      ON s2.datetime = s1.datetime
     AND s2.period   = s1.period
     AND s2.colid    = s1.colid
     AND s2.valfmt   = s1.valfmt
     AND s2.rowid IN (6, 7, 8)
    WHERE s1.rowid = 1
    GROUP BY s1.datetime, s1.period, s1.colid, s1.valfmt, s1.value
    HAVING CAST(total_val AS DECIMAL(20,4)) <> CAST(sum_678 AS DECIMAL(20,4))
    """,
    lambda rows: (len(rows) == 0,
                  f"{len(rows)} cell(s) violate total = SUM(6,7,8); first: {rows[:3]}"))


# -- 2.  web.dnload is never NULL after backfill-dnload runs.  The
#    column was added by the post-1018cc2 refactor; backfill-dnload
#    fills every row to 0 or 1 (see hzmetrics.py do_backfill_dnload).
#    Any NULL after a pipeline pass means backfill was skipped or the
#    UPDATE missed rows.
inv("web.dnload IS NOT NULL on every row",
    "metrics_db",
    "SELECT COUNT(*) FROM web WHERE dnload IS NULL",
    lambda rows: (rows[0][0] == 0,
                  f"{rows[0][0]} web row(s) have dnload IS NULL"))


# -- 3.  web.dnload IN (0, 1).  No other values are valid — the column
#    is set from a URL pattern match (see xlogimport_apache.php and
#    backfill-dnload).
inv("web.dnload IN (0, 1) on every row",
    "metrics_db",
    "SELECT DISTINCT dnload FROM web WHERE dnload NOT IN (0, 1)",
    lambda rows: (len(rows) == 0,
                  f"{len(rows)} unexpected dnload value(s): {rows}"))


# -- 4.  websessions.duration >= 0.  Computed as
#    prev_datetimeint - s_datetimeint in logfix_session.pl — both come
#    from UNIX_TIMESTAMP and the loop only sets prev_datetime after
#    advancing, so the delta is always non-negative.  A negative value
#    indicates a coalescing logic bug.
inv("websessions.duration >= 0 on every row",
    "metrics_db",
    "SELECT COUNT(*) FROM websessions WHERE duration < 0",
    lambda rows: (rows[0][0] == 0,
                  f"{rows[0][0]} websession(s) have duration < 0"))


# -- 5.  userlogin_lite contains no 'hubstatus' / 'hubadmin' rows.
#    xlogimport_authlog.php:97 explicitly filters these out, and
#    userlogin_lite is rebuilt from userlogin in build_userlogin_lite().
#    Any rows here mean the filter regressed.
inv("userlogin_lite excludes hubstatus/hubadmin",
    "metrics_db",
    "SELECT COUNT(*) FROM userlogin_lite WHERE user IN ('hubstatus', 'hubadmin')",
    lambda rows: (rows[0][0] == 0,
                  f"{rows[0][0]} userlogin_lite row(s) for hubstatus/hubadmin"))


# -- 6.  Every (rowid, colid) in summary_user_vals appears for all 6
#    period codes (0, 1, 3, 12, 13, 14) at the same datetime — the
#    summary writes a full grid in one invocation.  A missing period
#    for an otherwise-present (rowid, colid) is a write-path bug.
inv("summary_user_vals full period grid",
    "metrics_db",
    """
    SELECT datetime, rowid, colid, GROUP_CONCAT(DISTINCT period ORDER BY period) AS periods
    FROM summary_user_vals
    GROUP BY datetime, rowid, colid
    HAVING periods <> '0,1,3,12,13,14'
    """,
    lambda rows: (len(rows) == 0,
                  f"{len(rows)} (datetime,rowid,colid) tuple(s) with incomplete period grid; first: {rows[:3]}"))


# -- 7.  jos_xprofiles_metrics counts match the live hub.jos_xprofiles
#    rebuild.  xlogimport_tool_and_reg_user_data.php fully drops and
#    rebuilds jos_xprofiles_metrics from hub.jos_xprofiles on every
#    run.  Counts should be equal (modulo the test's filter excluding
#    invalid profiles — but uidNumber > 0 covers the standard cut).
inv("jos_xprofiles_metrics count matches hub jos_xprofiles (uidNumber>0)",
    "metrics_db",
    "SELECT COUNT(*) FROM jos_xprofiles_metrics",
    lambda rows: rows  # Captured at runtime — compared against hub in main()
    )


def main():
    cfg_path = (sys.argv[1] if len(sys.argv) > 1
                else os.environ.get("HZMETRICS_ACCESS_CFG",
                                    "/etc/hubzero-metrics/access.cfg"))
    cfg = parse_access_cfg(cfg_path)
    metrics_db = cfg.get("metrics_db")
    hub_db = cfg.get("hub_db")
    if not metrics_db or not hub_db:
        print("FAIL: missing metrics_db / hub_db in access.cfg")
        sys.exit(2)

    fail = 0
    metrics_conn = connect(cfg, metrics_db)
    hub_conn = connect(cfg, hub_db)
    try:
        with metrics_conn.cursor() as mcur, hub_conn.cursor() as hcur:
            # Cross-DB invariant #7 needs a comparison.
            mcur.execute("SELECT COUNT(*) FROM jos_xprofiles_metrics")
            xpm_count = mcur.fetchone()[0]
            hcur.execute("SELECT COUNT(*) FROM jos_xprofiles WHERE uidNumber > 0")
            xp_count = hcur.fetchone()[0]

            for label, db_key, sql, check in INVARIANTS:
                if label.startswith("jos_xprofiles_metrics count"):
                    if xpm_count == xp_count:
                        print(f"  PASS  {label} ({xpm_count})")
                    else:
                        print(f"  FAIL  {label} "
                              f"(metrics={xpm_count} hub={xp_count})")
                        fail = 1
                    continue
                conn = metrics_conn if db_key == "metrics_db" else hub_conn
                with conn.cursor() as cur:
                    cur.execute(sql)
                    rows = cur.fetchall()
                ok, detail = check(rows)
                if ok:
                    print(f"  PASS  {label}")
                else:
                    print(f"  FAIL  {label}")
                    print(f"        {detail}")
                    fail = 1
    finally:
        metrics_conn.close()
        hub_conn.close()
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
