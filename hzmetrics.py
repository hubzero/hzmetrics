#!/usr/bin/env python3
"""
hzmetrics.py - metrics pipeline management

Usage:
  hzmetrics.py tick                                                   # every-5-min cron entry (whoisonline + metrics at :30)
  hzmetrics.py run                                                    # metrics run only (called by tick; also manual)
  hzmetrics.py whoisonline                                            # update real-time session geo map
  hzmetrics.py status                                                 # show pending vs imported state
  hzmetrics.py process  [--next | --month YYYY-MM | --day YYYY-MM-DD] [--force]
  hzmetrics.py import   [--next | --month YYYY-MM | --day YYYY-MM-DD] [--force]
  hzmetrics.py analyze  --month YYYY-MM [--force]
  hzmetrics.py summarize --month YYYY-MM [--force]
  hzmetrics.py fill-geo  --month YYYY-MM | --all
  hzmetrics.py backfill-dnload [--start YYYY-MM]
  hzmetrics.py resolve-dns {metrics|hub} <table> [YYYY-MM] [-n NAMESERVER] [-c N] [-t SEC]
  hzmetrics.py migrate [--apply]
  hzmetrics.py setup-db
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

METRICS     = Path("/opt/hubzero/bin/metrics")
HTTPD_DAILY = Path("/var/log/httpd/daily")
HZ_DAILY    = Path("/var/log/hubzero/daily")
LOG         = Path("/var/log/hubzero/metrics/manage.log")
LOCK_FILE   = Path("/var/run/hzmetrics/hzmetrics.pid")
STATE_FILE  = Path("/var/run/hzmetrics/hzmetrics.state")
SITE        = ""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def log_open():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    return open(LOG, "a")

def run(cmd, logfile=None, dry_run=False):
    """Run a command, streaming output to stdout and optionally a logfile."""
    label = "[dry-run] " if dry_run else ""
    cmd_str = ' '.join(str(c) for c in cmd)
    ts_start = datetime.now()
    print(f">>> {label}{cmd_str}", flush=True)
    if dry_run:
        return
    if logfile:
        logfile.write(f">>> {cmd_str}  @ {ts_start}\n")
        logfile.flush()
    with subprocess.Popen(
        [str(c) for c in cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ) as proc:
        for line in proc.stdout:
            line = line.decode("utf-8", errors="replace")
            print(line, end="", flush=True)
            if logfile:
                logfile.write(line)
                logfile.flush()
        proc.wait()
        ts_end = datetime.now()
        elapsed = ts_end - ts_start
        if logfile:
            logfile.write(f"<<< {cmd_str}  @ {ts_end}  elapsed {elapsed}\n")
            logfile.flush()
        if proc.returncode != 0:
            print(f"[exit {proc.returncode}]", flush=True)

def dated_files(directory, pattern):
    """Return sorted list of (date_str, Path) for files matching pattern in directory."""
    results = []
    for p in Path(directory).glob(pattern):
        if p.is_dir():
            continue
        for part in p.name.replace("-", ".").replace("_", ".").split("."):
            if len(part) == 8 and part.isdigit():
                results.append((part, p))
                break
    return sorted(results)

def pending_days_for_month(month_str):
    """Sorted list of date strings in daily/ for the given YYYY-MM."""
    yyyymm = month_str.replace("-", "")
    return [d for d, _ in dated_files(HTTPD_DAILY, f"{SITE}-access*log*") if d.startswith(yyyymm)]

def oldest_pending_month():
    files = dated_files(HTTPD_DAILY, f"{SITE}-access*log*")
    if not files:
        return None
    d = files[0][0]
    return f"{d[:4]}-{d[4:6]}"

def last_imported_date():
    files = dated_files("/var/log/httpd/imported", f"{SITE}-access*log*")
    return files[-1][0] if files else None

def is_current_month(month_str):
    return month_str == date.today().strftime("%Y-%m")

def check_order(date_str, force):
    """Abort if date_str would be imported out of order."""
    if force:
        return
    pending = [d for d, _ in dated_files(HTTPD_DAILY, f"{SITE}-access*log*")]
    if pending and date_str > pending[0]:
        print(f"ERROR: {date_str} is not the oldest pending day in daily/.")
        print(f"       Oldest pending: {pending[0]}")
        print(f"       Use --force to override.")
        sys.exit(1)
    last = last_imported_date()
    if last and date_str < last:
        print(f"ERROR: {date_str} is older than the most recently imported log ({last}).")
        print(f"       Use --force to override.")
        sys.exit(1)

def previous_month(month_str):
    y, m = int(month_str[:4]), int(month_str[5:7])
    m -= 1
    if m == 0:
        m, y = 12, y - 1
    return f"{y:04d}-{m:02d}"

def last_day_of_month(month_str):
    """Return YYYYMMDD for the last calendar day of the given YYYY-MM."""
    y, m = int(month_str[:4]), int(month_str[5:7])
    last = (datetime(y, m, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return last.strftime("%Y%m%d")

def is_month_fully_imported(month_str):
    """True if the last calendar day of month_str is present in imported/."""
    last = last_day_of_month(month_str)
    return any(d == last for d, _ in dated_files("/var/log/httpd/imported", f"{SITE}-access*log*"))

def is_month_summarized(month_str):
    _, _, _, metrics_db = db_credentials()
    dt = month_str + "-00"
    rows = mysql_query(
        f"SELECT COUNT(*) FROM {metrics_db}.summary_user_vals "
        f"WHERE datetime = '{dt}' AND period = 1;"
    )
    return rows and rows[0] != "0"

def acquire_lock():
    """Try to write a PID lock. Returns True if acquired, False if another instance is running.

    /var/run/hzmetrics/ must be pre-created and owned by the service user:
      install: echo 'd /var/run/hzmetrics 0755 apache apache -' > /etc/tmpfiles.d/hzmetrics.conf
               systemd-tmpfiles --create /etc/tmpfiles.d/hzmetrics.conf
    """
    if not LOCK_FILE.parent.exists():
        print(f"ERROR: {LOCK_FILE.parent} does not exist.")
        print(f"  Run once as root:  mkdir -p {LOCK_FILE.parent} && chown apache:apache {LOCK_FILE.parent}")
        sys.exit(1)
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            Path(f"/proc/{pid}").stat()  # raises OSError if process is gone
            return False  # still running
        except (ValueError, OSError):
            pass  # stale lock
    LOCK_FILE.write_text(str(os.getpid()))
    return True

def release_lock():
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass

def read_state():
    try:
        return dict(
            line.split("=", 1)
            for line in STATE_FILE.read_text().splitlines()
            if "=" in line
        )
    except (FileNotFoundError, ValueError):
        return {}

def update_state(**kwargs):
    state = read_state()
    state.update({k: str(v) for k, v in kwargs.items()})
    STATE_FILE.write_text("".join(f"{k}={v}\n" for k, v in state.items()))


# ---------------------------------------------------------------------------
# Schema migrations
# Each entry: id (int, sequential), description, sql (uses {metrics_db} placeholder),
# and check_sql (returns row count > 0 if the change already exists in the schema).
# Applied state is tracked in metrics_db.migrations.
# check_sql allows auto-detection of changes applied before this system existed.
# ---------------------------------------------------------------------------
MIGRATIONS = [
    {
        "id": 1,
        "description": "Index web(dnload) — applied by backfill-dnload May 2026",
        "sql": "ALTER TABLE {metrics_db}.web ADD INDEX dnload (dnload);",
        "check_sql": "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='web' AND index_name='dnload';",
    },
    {
        "id": 2,
        "description": "Composite index web(sessionid, dnload) — covering index for download_users JOIN",
        "sql": "ALTER TABLE {metrics_db}.web ADD INDEX web_sessionid_dnload (sessionid, dnload);",
        "check_sql": "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='web' AND index_name='web_sessionid_dnload';",
    },
    {
        "id": 3,
        "description": "Composite index websessions(datetime, jobs, duration, ipcountry) — filter pushdown for int/download_users",
        "sql": "ALTER TABLE {metrics_db}.websessions ADD INDEX ws_datetime_jobs_dur_country (datetime, jobs, duration, ipcountry);",
        "check_sql": "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='websessions' AND index_name='ws_datetime_jobs_dur_country';",
    },
    {
        "id": 4,
        "description": "Purge userlogin rows with action not in (login, simulation) — detect/invalid/logout are never queried",
        "sql": "DELETE FROM {metrics_db}.userlogin WHERE action NOT IN ('login', 'simulation');",
        "check_sql": "SELECT COUNT(*) FROM {metrics_db}.userlogin WHERE action NOT IN ('login', 'simulation');",
        "check_expect": 0,
    },
    {
        "id": 5,
        "description": "Index websessions(domain) — speeds up domainclass JOIN in download org queries",
        "sql": "ALTER TABLE {metrics_db}.websessions ADD INDEX ws_domain (domain);",
        "check_sql": "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='websessions' AND index_name='ws_domain';",
    },
    {
        "id": 6,
        "description": "Index websessions(jobs, ipcountry, duration) — period-14 all-time download_users filter",
        "sql": "ALTER TABLE {metrics_db}.websessions ADD INDEX ws_jobs_country_dur (jobs, ipcountry, duration);",
        "check_sql": "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='websessions' AND index_name='ws_jobs_country_dur';",
    },
]

MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {metrics_db}.migrations (
    id INT NOT NULL,
    description VARCHAR(255),
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);
"""

def ensure_migrations_table(metrics_db):
    mysql_exec(MIGRATIONS_TABLE_SQL.format(metrics_db=metrics_db))
    _automark_applied(metrics_db)

def _automark_applied(metrics_db):
    """Mark migrations as applied if the schema change already exists (applied outside this system)."""
    applied = applied_migration_ids(metrics_db)
    for m in MIGRATIONS:
        if m["id"] in applied or "check_sql" not in m:
            continue
        rows = mysql_query(m["check_sql"].format(metrics_db=metrics_db))
        expect = str(m.get("check_expect", None))
        already = (expect == "None" and rows and rows[0] != "0") or (expect != "None" and rows and rows[0] == expect)
        if already:
            desc = m["description"].replace("'", "''")
            mysql_exec(
                f"INSERT IGNORE INTO {metrics_db}.migrations (id, description) "
                f"VALUES ({m['id']}, '{desc}');"
            )

def applied_migration_ids(metrics_db):
    rows = mysql_query(f"SELECT id FROM {metrics_db}.migrations ORDER BY id;")
    return set(int(r) for r in rows if r.isdigit())

def cmd_migrate(args):
    _, _, _, metrics_db = db_credentials()
    ensure_migrations_table(metrics_db)
    applied = applied_migration_ids(metrics_db)

    print(f"{'ID':<4}  {'STATUS':<9}  DESCRIPTION")
    print("-" * 72)
    for m in MIGRATIONS:
        status = "applied" if m["id"] in applied else "PENDING"
        print(f"{m['id']:<4}  {status:<9}  {m['description']}")

    pending = [m for m in MIGRATIONS if m["id"] not in applied]
    if not pending:
        print("\nAll migrations applied.")
        return

    print(f"\n{len(pending)} pending migration(s).")

    if not args.apply:
        print("Run with --apply to execute them.")
        return

    with log_open() as logfile:
        logfile.write(f"\n=== manage.py migrate --apply  @ {datetime.now()} ===\n")
        for m in pending:
            sql = m["sql"].format(metrics_db=metrics_db)
            print(f"\n[{m['id']}] {m['description']}")
            print(f"    {sql}")
            rc = mysql_exec(sql)
            if rc == 0:
                desc = m["description"].replace("'", "''")
                mysql_exec(
                    f"INSERT IGNORE INTO {metrics_db}.migrations (id, description) "
                    f"VALUES ({m['id']}, '{desc}');"
                )
                print(f"    done.")
                logfile.write(f"migration {m['id']}: {m['description']}\n")
            else:
                print(f"    FAILED (rc={rc}) — stopping.")
                logfile.write(f"migration {m['id']} FAILED\n")
                break

    print("\n>>> done")


# ---------------------------------------------------------------------------
# setup-db  (create metrics database and all tables; idempotent)
# ---------------------------------------------------------------------------

METRICS_DB_DDL = [
    "CREATE DATABASE IF NOT EXISTS `{metrics_db}` DEFAULT CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`bot_useragents` (
  `useragent` tinytext NOT NULL,
  PRIMARY KEY (`useragent`(255))
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`classes` (
  `class` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(128) NOT NULL DEFAULT '',
  `valfmt` tinyint(4) NOT NULL DEFAULT 0,
  `size` tinyint(4) NOT NULL DEFAULT 0,
  PRIMARY KEY (`class`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`classvals` (
  `class` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 0,
  `rank` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(255) DEFAULT NULL,
  `value` bigint(20) NOT NULL DEFAULT 0,
  KEY `class` (`class`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`continents` (
  `continentSHORT` char(2) NOT NULL DEFAULT '',
  `continentLONG` varchar(45) NOT NULL DEFAULT '',
  UNIQUE KEY `continentSHORT` (`continentSHORT`,`continentLONG`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`countries` (
  `code` varchar(4) NOT NULL DEFAULT '',
  `name` varchar(128) NOT NULL DEFAULT '',
  PRIMARY KEY (`code`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`country_continent` (
  `country` char(2) NOT NULL DEFAULT '',
  `continent` char(2) NOT NULL DEFAULT '',
  PRIMARY KEY (`country`,`continent`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`domainclass` (
  `domain` varchar(64) NOT NULL DEFAULT '',
  `class` tinyint(4) NOT NULL DEFAULT 0,
  `country` varchar(4) NOT NULL DEFAULT '',
  `state` varchar(4) NOT NULL DEFAULT '',
  `name` tinytext NOT NULL,
  PRIMARY KEY (`domain`),
  KEY `class` (`class`),
  KEY `domain_class` (`domain`,`class`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`domainclasses` (
  `class` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(64) NOT NULL DEFAULT '',
  PRIMARY KEY (`class`),
  UNIQUE KEY `class_name` (`class`,`name`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`exclude_list` (
  `id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `filter` varchar(65) NOT NULL,
  `type` varchar(65) NOT NULL DEFAULT 'domain',
  `notes` varchar(120) DEFAULT NULL,
  `date_added` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `filter_type` (`filter`,`type`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`exclude_list2` (
  `filter` varchar(65) NOT NULL,
  `type` varchar(65) NOT NULL DEFAULT 'domain',
  `notes` varchar(120) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`jos_xprofiles_metrics` (
  `uidNumber` int(11) NOT NULL,
  `name` varchar(255) NOT NULL DEFAULT '',
  `username` varchar(150) NOT NULL DEFAULT '',
  `email` varchar(100) NOT NULL DEFAULT '',
  `registerDate` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `gidNumber` varchar(11) NOT NULL DEFAULT '',
  `homeDirectory` varchar(255) NOT NULL DEFAULT '',
  `loginShell` varchar(255) NOT NULL DEFAULT '',
  `ftpShell` varchar(255) NOT NULL DEFAULT '',
  `userPassword` varchar(255) NOT NULL DEFAULT '',
  `gid` varchar(255) NOT NULL DEFAULT '',
  `orgtype` varchar(255) NOT NULL DEFAULT '',
  `organization` varchar(255) NOT NULL DEFAULT '',
  `countryresident` char(2) NOT NULL DEFAULT '',
  `countryorigin` char(2) NOT NULL DEFAULT '',
  `gender` varchar(255) NOT NULL DEFAULT '',
  `url` varchar(255) NOT NULL DEFAULT '',
  `reason` text NOT NULL,
  `mailPreferenceOption` int(11) NOT NULL DEFAULT -1,
  `usageAgreement` int(11) NOT NULL DEFAULT 0,
  `modifiedDate` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `emailConfirmed` int(11) NOT NULL DEFAULT 0,
  `regIP` varchar(255) NOT NULL DEFAULT '',
  `regHost` varchar(255) NOT NULL DEFAULT '',
  `nativeTribe` varchar(255) NOT NULL DEFAULT '',
  `phone` varchar(255) NOT NULL DEFAULT '',
  `proxyPassword` varchar(255) NOT NULL DEFAULT '',
  `proxyUidNumber` varchar(255) NOT NULL DEFAULT '',
  `givenName` varchar(255) NOT NULL DEFAULT '',
  `middleName` varchar(255) NOT NULL DEFAULT '',
  `surname` varchar(255) NOT NULL DEFAULT '',
  `picture` varchar(255) NOT NULL DEFAULT '',
  `vip` int(11) NOT NULL DEFAULT 0,
  `public` tinyint(2) NOT NULL DEFAULT 0,
  `params` text NOT NULL,
  `note` text NOT NULL,
  `shadowExpire` int(11) DEFAULT NULL,
  `location` varchar(50) DEFAULT NULL,
  `orcid` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`uidNumber`),
  KEY `idx_username` (`username`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`migrations` (
  `id` int(11) NOT NULL,
  `description` varchar(255) DEFAULT NULL,
  `applied_at` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`regions` (
  `region` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(128) NOT NULL DEFAULT '',
  `valfmt` tinyint(4) NOT NULL DEFAULT 0,
  `size` tinyint(4) NOT NULL DEFAULT 0,
  PRIMARY KEY (`region`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`regionvals` (
  `region` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 0,
  `rank` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(255) DEFAULT NULL,
  `value` bigint(20) NOT NULL DEFAULT 0
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`sessionlog_metrics` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `sessnum` bigint(20) unsigned NOT NULL,
  `user` varchar(150) NOT NULL DEFAULT '',
  `ip` varchar(15) NOT NULL DEFAULT '',
  `start` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `appname` varchar(150) NOT NULL DEFAULT '',
  `host` tinytext DEFAULT NULL,
  `domain` tinytext DEFAULT NULL,
  `orgtype` tinytext DEFAULT NULL,
  `countryresident` char(2) DEFAULT NULL,
  `countrycitizen` char(2) DEFAULT NULL,
  `ipcountry` char(2) DEFAULT NULL,
  PRIMARY KEY (`sessnum`),
  UNIQUE KEY `id` (`id`),
  KEY `user` (`user`),
  KEY `start` (`start`),
  KEY `appname` (`appname`),
  KEY `countryresident` (`countryresident`),
  KEY `countrycitizen` (`countrycitizen`),
  KEY `orgtype` (`orgtype`(255))
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_andmore` (
  `id` tinyint(4) NOT NULL DEFAULT 0,
  `label` varchar(255) NOT NULL DEFAULT '',
  `plot` int(1) DEFAULT 0,
  UNIQUE KEY `label` (`label`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_andmore_vals` (
  `rowid` tinyint(4) NOT NULL DEFAULT 0,
  `colid` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 1,
  `value` bigint(20) DEFAULT 0,
  `valfmt` tinyint(4) NOT NULL DEFAULT 0
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_misc` (
  `id` tinyint(4) NOT NULL DEFAULT 0,
  `label` varchar(255) NOT NULL DEFAULT '',
  `plot` int(1) DEFAULT 0,
  UNIQUE KEY `label` (`label`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_misc_vals` (
  `rowid` tinyint(4) NOT NULL DEFAULT 0,
  `colid` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 1,
  `value` varchar(200) DEFAULT '',
  `valfmt` tinyint(4) NOT NULL DEFAULT 0
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_simusage` (
  `id` tinyint(4) NOT NULL DEFAULT 0,
  `label` varchar(255) NOT NULL DEFAULT '',
  `plot` int(1) DEFAULT 0,
  UNIQUE KEY `label` (`label`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_simusage_vals` (
  `rowid` tinyint(4) NOT NULL DEFAULT 0,
  `colid` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 1,
  `value` bigint(20) DEFAULT 0,
  `valfmt` tinyint(4) NOT NULL DEFAULT 0
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_user` (
  `id` tinyint(4) NOT NULL DEFAULT 0,
  `label` varchar(255) NOT NULL DEFAULT '',
  `plot` int(1) DEFAULT 0,
  UNIQUE KEY `label` (`label`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_user_vals` (
  `rowid` tinyint(4) NOT NULL DEFAULT 0,
  `colid` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 1,
  `value` bigint(20) DEFAULT 0,
  `valfmt` tinyint(4) NOT NULL DEFAULT 0
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`toolstart` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `sessionid` bigint(20) DEFAULT NULL,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `orgtype` tinytext DEFAULT NULL,
  `countryresident` char(2) DEFAULT NULL,
  `countrycitizen` char(2) DEFAULT NULL,
  `success` tinyint(4) NOT NULL DEFAULT 0,
  `ipcountry` char(2) DEFAULT NULL,
  `ip` varchar(15) NOT NULL DEFAULT '',
  `host` tinytext DEFAULT NULL,
  `user` varchar(150) DEFAULT NULL,
  `tool` tinytext NOT NULL,
  `pid` int(11) DEFAULT NULL,
  `domain` tinytext DEFAULT NULL,
  `filesystem` tinytext DEFAULT NULL,
  `execunit` tinytext DEFAULT NULL,
  `walltime` float unsigned DEFAULT 0,
  `cputime` float unsigned DEFAULT 0,
  `error` tinytext DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `datetime` (`datetime`),
  KEY `success` (`success`),
  KEY `sessionid` (`sessionid`),
  KEY `ipcountry` (`ipcountry`),
  KEY `countrycitizen` (`countrycitizen`),
  KEY `countryresident` (`countryresident`),
  KEY `orgtype` (`orgtype`(255)),
  KEY `ip` (`ip`),
  KEY `user` (`user`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`tops` (
  `top` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(128) NOT NULL DEFAULT '',
  `valfmt` tinyint(4) NOT NULL DEFAULT 0,
  `size` tinyint(4) NOT NULL DEFAULT 0,
  PRIMARY KEY (`top`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`topvals` (
  `top` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 1,
  `rank` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(255) DEFAULT NULL,
  `value` bigint(20) NOT NULL DEFAULT 0,
  KEY `top` (`top`),
  KEY `top_datetime_period` (`top`,`datetime`,`period`),
  KEY `top_datetime_rank` (`top`,`datetime`,`rank`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`totalvals` (
  `hub` tinyint(4) NOT NULL DEFAULT 0,
  `total` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 1,
  `value` bigint(20) NOT NULL DEFAULT 0,
  KEY `hub_total_datetime` (`hub`,`total`,`datetime`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`userlogin` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `user` varchar(255) NOT NULL DEFAULT '-',
  `uidNumber` bigint(20) DEFAULT 0,
  `ip` varchar(15) NOT NULL DEFAULT '',
  `action` varchar(40) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `userlogin` (`datetime`,`user`,`uidNumber`,`ip`,`action`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`userlogin_lite` (
  `id` bigint(20) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `user` varchar(255) NOT NULL DEFAULT '-',
  `uidNumber` bigint(20) DEFAULT 0,
  `ip` varchar(15) NOT NULL DEFAULT '',
  `action` varchar(40) NOT NULL DEFAULT '',
  KEY `uidNumber` (`uidNumber`),
  KEY `datetime_user` (`datetime`,`user`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`web` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `elementid` bigint(20) DEFAULT NULL,
  `sessionid` bigint(20) DEFAULT NULL,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `ipcountry` char(2) DEFAULT NULL,
  `content` tinytext NOT NULL,
  `referrer` tinytext DEFAULT NULL,
  `useragent` tinytext DEFAULT NULL,
  `ip` varchar(15) NOT NULL DEFAULT '',
  `host` tinytext DEFAULT NULL,
  `domain` tinytext DEFAULT NULL,
  `uidNumber` int(11) DEFAULT NULL,
  `apache_pid` varchar(120) NOT NULL DEFAULT '',
  `joomla_sessionid` varchar(120) NOT NULL DEFAULT '',
  `site_cookie` varchar(120) NOT NULL DEFAULT '',
  `auth_type` varchar(120) NOT NULL DEFAULT '',
  `component_name` varchar(120) NOT NULL DEFAULT '',
  `view_name` varchar(120) NOT NULL DEFAULT '',
  `task_name` varchar(120) NOT NULL DEFAULT '',
  `action_name` varchar(120) NOT NULL DEFAULT '',
  `item_name` varchar(120) NOT NULL DEFAULT '',
  `dnload` tinyint(4) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `datetime` (`datetime`),
  KEY `sessionid` (`sessionid`),
  KEY `elementid` (`elementid`),
  KEY `ipcountry` (`ipcountry`),
  KEY `ip` (`ip`),
  KEY `content` (`content`(255)),
  KEY `dnload` (`dnload`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`webhits` (
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `hits` bigint(20) NOT NULL DEFAULT 0
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`websessions` (
  `id` bigint(20) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `ipcountry` char(2) NOT NULL DEFAULT '',
  `ip` varchar(15) NOT NULL DEFAULT '',
  `host` tinytext DEFAULT NULL,
  `domain` tinytext DEFAULT NULL,
  `duration` bigint(20) NOT NULL DEFAULT 0,
  `jobs` tinyint(4) NOT NULL DEFAULT 0,
  `webevents` bigint(20) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  KEY `datetime` (`datetime`),
  KEY `ipcountry` (`ipcountry`),
  KEY `ip` (`ip`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",
]


def cmd_setup_db(args):
    _, _, _, metrics_db = db_credentials()
    dry_run = args.dry_run
    errors = 0

    for stmt in METRICS_DB_DDL:
        sql = stmt.format(metrics_db=metrics_db)
        label = sql.split("\n")[0][:72]
        if dry_run:
            print(f"  [dry-run] {label}")
            continue
        rc = mysql_exec(sql)
        if rc != 0:
            errors += 1

    if not dry_run:
        if errors:
            print(f"\n{errors} statement(s) failed.")
        else:
            print(f"  {len(METRICS_DB_DDL)} statement(s) executed, database ready.")
    print(">>> done")


DOWNLOAD_EXTS = [
    "txt", "png", "pdf", "ppt", "pptx", "swf", "docx", "jpg", "doc", "zip",
    "mp3", "mbtiles", "xml", "xlsx", "webm", "mp4", "xls", "r", "csv", "nc4",
    "template", "tgz", "mov", "ipynb", "py", "rar", "grd", "tif", "nc", "har",
]

# tables that carry ipcountry, and their datetime column
IPCOUNTRY_TABLES = [
    ("metrics", "web"),
    ("metrics", "websessions"),
    ("metrics", "toolstart"),
    ("metrics", "sessionlog_metrics"),
]


# ---------------------------------------------------------------------------
# pipeline steps
# ---------------------------------------------------------------------------

def do_import_day(date_str, logfile, dry_run=False):
    if dry_run:
        # show the actual files that would be fetched
        access = sorted(HTTPD_DAILY.glob(f"{SITE}-access*log*{date_str}*"))
        cmsauth = sorted(HZ_DAILY.glob(f"cmsauth*log*{date_str}*"))
        for f in access + cmsauth:
            print(f"    [dry-run] would fetch: {f}")
        if not access:
            print(f"    [dry-run] WARNING: no access log found for {date_str} in {HTTPD_DAILY}")
        if not cmsauth:
            print(f"    [dry-run] WARNING: no cmsauth log found for {date_str} in {HZ_DAILY}")
    run([METRICS / "import" / "__fetch_apache_and_auth_log.sh",   date_str], logfile, dry_run)
    run([METRICS / "import" / "__import_apache_and_auth_log.sh"],            logfile, dry_run)
    run([METRICS / "import" / "__archive_apache_and_auth_log.sh", date_str], logfile, dry_run)

def do_analyze(month_str, logfile, dry_run=False):
    run([METRICS / "__process_tool_metrics.sh",  month_str], logfile, dry_run)
    run([METRICS / "__process_usage_metrics.sh", month_str], logfile, dry_run)

def do_summarize(month_str, logfile, dry_run=False):
    run([METRICS / "__process_usage_metrics_summary.sh", month_str], logfile, dry_run)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status():
    def summarize(directory, pattern, label):
        files = dated_files(directory, pattern)
        count = len(files)
        if count == 0:
            print(f"  {label}: 0")
            return
        oldest, newest = files[0][0], files[-1][0]
        span = f"({oldest})" if oldest == newest else f"({oldest} .. {newest})"
        print(f"  {label}: {count}  {span}")

    print("=== daily/ (pending import) ===")
    summarize(HTTPD_DAILY, f"{SITE}-access*log*", "httpd access")
    summarize(HZ_DAILY,    "cmsauth*log*",         "cmsauth      ")

    print("\n=== imported/ (already processed) ===")
    summarize("/var/log/httpd/imported",   f"{SITE}-access*log*", "httpd  ")
    summarize("/var/log/hubzero/imported", "cmsauth*log*",         "hubzero")

    print("\n=== resolve-dns settings ===")
    try:
        conf_present = HZMETRICS_CONF.is_file()
        conf_src = str(HZMETRICS_CONF) if conf_present else "(not present — using defaults / env)"
    except PermissionError:
        conf_src = f"{HZMETRICS_CONF} (no read access — using defaults / env)"
    print(f"  config file: {conf_src}")
    print(f"  nameserver : {DNS_NAMESERVER}")
    print(f"  concurrency: {DNS_CONCURRENCY}")
    print(f"  timeout    : {DNS_TIMEOUT}s")


# ---------------------------------------------------------------------------
# import  (raw log ingestion only)
# ---------------------------------------------------------------------------

def cmd_import(args):
    dry_run = args.dry_run
    with log_open() as logfile:
        if not dry_run:
            logfile.write(f"\n=== manage.py import {' '.join(sys.argv[1:])}  @ {datetime.now()} ===\n")

        if args.next:
            month_str = oldest_pending_month()
            if not month_str:
                print("Nothing pending in daily/.")
                return
            days = pending_days_for_month(month_str)
            check_order(days[0], args.force)
            print(f"{'[dry-run] would import' if dry_run else 'Importing'} {len(days)} day(s) for {month_str}")
            for date_str in days:
                print(f"\n--- {date_str} ---")
                do_import_day(date_str, logfile, dry_run)

        elif args.month:
            days = pending_days_for_month(args.month)
            if not days:
                print(f"No pending days in daily/ for {args.month}.")
                return
            check_order(days[0], args.force)
            print(f"{'[dry-run] would import' if dry_run else 'Importing'} {len(days)} day(s) for {args.month}")
            for date_str in days:
                print(f"\n--- {date_str} ---")
                do_import_day(date_str, logfile, dry_run)

        elif args.day:
            date_str = args.day.replace("-", "")
            check_order(date_str, args.force)
            do_import_day(date_str, logfile, dry_run)

        else:
            print("Specify --next, --month, or --day.")
            sys.exit(1)

        print(">>> done")


# ---------------------------------------------------------------------------
# analyze  (enrichment and stats only)
# ---------------------------------------------------------------------------

def cmd_analyze(args):
    dry_run = args.dry_run
    if is_current_month(args.month) and not args.force:
        print(f"ERROR: {args.month} is the current month and not yet complete.")
        print(f"       Use --force to override.")
        sys.exit(1)
    with log_open() as logfile:
        if not dry_run:
            logfile.write(f"\n=== manage.py analyze {' '.join(sys.argv[1:])}  @ {datetime.now()} ===\n")
        do_analyze(args.month, logfile, dry_run)
        do_summarize(args.month, logfile, dry_run)
    print(">>> done")


# ---------------------------------------------------------------------------
# summarize  (rolling-window aggregation; normally run once after catchup)
# ---------------------------------------------------------------------------

def cmd_summarize(args):
    dry_run = args.dry_run
    if is_current_month(args.month) and not args.force:
        print(f"ERROR: {args.month} is the current month and not yet complete.")
        print(f"       Use --force to override.")
        sys.exit(1)
    with log_open() as logfile:
        if not dry_run:
            logfile.write(f"\n=== manage.py summarize {' '.join(sys.argv[1:])}  @ {datetime.now()} ===\n")
        do_summarize(args.month, logfile, dry_run)
    print(">>> done")


# ---------------------------------------------------------------------------
# process  (import + analyze; the normal command)
# ---------------------------------------------------------------------------

def cmd_process(args):
    dry_run = args.dry_run
    with log_open() as logfile:
        if not dry_run:
            logfile.write(f"\n=== manage.py process {' '.join(sys.argv[1:])}  @ {datetime.now()} ===\n")

        if args.next:
            month_str = oldest_pending_month()
            if not month_str:
                print("Nothing pending in daily/.")
                return
            days = pending_days_for_month(month_str)
            print(f"{'[dry-run] would process' if dry_run else 'Processing'} {len(days)} day(s) for {month_str}")
        elif args.month:
            month_str = args.month
            days = pending_days_for_month(month_str)
            if days:
                print(f"{'[dry-run] would process' if dry_run else 'Processing'} {len(days)} day(s) for {month_str}")
        elif args.day:
            date_str = args.day.replace("-", "")
            month_str = args.day[:7]
            check_order(date_str, args.force)
            do_import_day(date_str, logfile, dry_run)
            if is_current_month(month_str) and not args.force:
                print(f">>> {month_str} is the current month — skipping analysis until it ends.")
            else:
                do_analyze(month_str, logfile, dry_run)
                do_summarize(month_str, logfile, dry_run)
            print(">>> done")
            return
        else:
            print("Specify --next, --month, or --day.")
            sys.exit(1)

        check_order(days[0], args.force)
        for date_str in days:
            print(f"\n--- {date_str} ---")
            do_import_day(date_str, logfile, dry_run)

        if is_current_month(month_str) and not args.force:
            print(f"\n>>> {month_str} is the current month — skipping analysis until it ends.")
            print(f"    Run: manage.py analyze --month {month_str}")
        else:
            print(f"\n>>> {'[dry-run] would analyze' if dry_run else 'analyzing'} {month_str}")
            do_analyze(month_str, logfile, dry_run)
            print(f"\n>>> {'[dry-run] would summarize' if dry_run else 'summarizing'} {month_str}")
            do_summarize(month_str, logfile, dry_run)

        print(">>> done")


# ---------------------------------------------------------------------------
# fill-geo
# ---------------------------------------------------------------------------

ACCESS_CFG = Path("/etc/hubzero-metrics/access.cfg")

def db_credentials():
    """Parse DB connection info from access.cfg."""
    text = ACCESS_CFG.read_text()
    def get(var):
        m = re.search(r'\$' + var + r"\s*=\s*'([^']*)'", text)
        return m.group(1) if m else ""
    return get("db_host"), get("db_user"), get("db_pass"), get("metrics_db")

def mysql_query(sql):
    """Run a SELECT against the metrics DB, return list of result rows.

    Each row is rendered as a tab-joined string for backwards compatibility
    with the older `mysql -BN` shell-out output format — single-column
    callers index rows[i] as the value directly; multi-column callers
    split on \\t themselves.  NULL renders as the literal 'NULL', again
    matching the prior shell behaviour.

    Callers must fully-qualify table names with {metrics_db} — no default
    database is selected on the connection.
    """
    conn = _open_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return ["\t".join("NULL" if c is None else str(c) for c in row)
                    for row in cur.fetchall()]
    finally:
        conn.close()

def mysql_exec(sql):
    """Run a DML/DDL statement against the metrics DB.  Returns 0 on
    success, 1 on failure (prints the error).  Single-statement contract.

    Callers must fully-qualify table names with {metrics_db} — no default
    database is selected on the connection."""
    import pymysql
    try:
        conn = _open_db()
    except pymysql.MySQLError as e:
        print(f"[mysql error] connect: {e}", flush=True)
        return 1
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        return 0
    except pymysql.MySQLError as e:
        print(f"[mysql error] {e}", flush=True)
        return 1
    finally:
        conn.close()

def months_with_missing_geo():
    """Query DB for months that have rows with null ipcountry in the web table."""
    _, _, _, metrics_db = db_credentials()
    return mysql_query(
        f"SELECT DISTINCT DATE_FORMAT(datetime,'%Y-%m') FROM {metrics_db}.web "
        f"WHERE ipcountry IS NULL OR ipcountry = '' ORDER BY 1;"
    )

def do_fill_geo(month_str, logfile, dry_run=False):
    for db, table in IPCOUNTRY_TABLES:
        run([METRICS / "xlogfix_ipcountry.php", db, table, month_str], logfile, dry_run)

def cmd_fill_geo(args):
    dry_run = args.dry_run

    if args.all:
        months = months_with_missing_geo()
        if not months:
            print("No months with missing GeoIP data found.")
            return
        print(f"{'[dry-run] would fill' if dry_run else 'Filling'} GeoIP for {len(months)} month(s): {months[0]} .. {months[-1]}")
    else:
        months = [args.month]
        print(f"{'[dry-run] would fill' if dry_run else 'Filling'} GeoIP for {args.month}")

    with log_open() as logfile:
        if not dry_run:
            logfile.write(f"\n=== manage.py fill-geo {' '.join(sys.argv[1:])}  @ {datetime.now()} ===\n")
        for month_str in months:
            print(f"\n--- {month_str} ---")
            do_fill_geo(month_str, logfile, dry_run)

    print(">>> done")


# ---------------------------------------------------------------------------
# backfill-dnload  (populate web.dnload for historical rows)
# ---------------------------------------------------------------------------

def do_backfill_dnload(start_month, logfile, dry_run=False):
    _, _, _, metrics_db = db_credentials()

    ext_pattern = "|".join(re.escape(e) for e in DOWNLOAD_EXTS)

    where = "dnload IS NULL"
    if start_month:
        where += f" AND datetime >= '{start_month}-01'"

    months = mysql_query(
        f"SELECT DISTINCT DATE_FORMAT(datetime,'%Y-%m') FROM {metrics_db}.web "
        f"WHERE {where} ORDER BY 1;"
    )
    if not months:
        print("  No months with unprocessed rows found.")
        return

    print(f"  Will backfill {len(months)} month(s): {months[0]} .. {months[-1]}")

    for month in months:
        m = datetime.strptime(month + "-01", "%Y-%m-%d")
        next_m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        m_start = m.strftime("%Y-%m-%d")
        m_end   = next_m.strftime("%Y-%m-%d")

        label = f"  {month}"
        if logfile:
            logfile.write(f"backfill-dnload {month}\n")
            logfile.flush()

        sql = (
            f"UPDATE {metrics_db}.web "
            f"SET dnload = IF("
            f"content LIKE '/resources/%/download/%' OR "
            f"content REGEXP '^/resources/.*\\.({ext_pattern})([?#]|$)', "
            f"1, 0) "
            f"WHERE datetime >= '{m_start}' AND datetime < '{m_end}' AND dnload IS NULL;"
        )

        if dry_run:
            print(f"{label}  [dry-run]", flush=True)
        else:
            print(f"{label} ...", end="", flush=True)
            rc = mysql_exec(sql)
            print(f" done (rc={rc})", flush=True)

    if not dry_run:
        rows = mysql_query(
            f"SELECT COUNT(*) FROM information_schema.statistics "
            f"WHERE table_schema='{metrics_db}' AND table_name='web' AND index_name='dnload';"
        )
        if rows and rows[0] == "0":
            print(f"  Adding index on {metrics_db}.web(dnload) ...", end="", flush=True)
            rc = mysql_exec(f"ALTER TABLE {metrics_db}.web ADD INDEX dnload (dnload);")
            print(f" done (rc={rc})", flush=True)
        else:
            print(f"  Index on {metrics_db}.web(dnload) already exists.")


def cmd_backfill_dnload(args):
    dry_run = args.dry_run
    with log_open() as logfile:
        if not dry_run:
            logfile.write(f"\n=== manage.py backfill-dnload {' '.join(sys.argv[1:])}  @ {datetime.now()} ===\n")
        do_backfill_dnload(args.start, logfile, dry_run)
    print(">>> done")


# ---------------------------------------------------------------------------
# whoisonline  (real-time session geo map; normally called every 5 min)
# ---------------------------------------------------------------------------

def cmd_whoisonline(args):
    with log_open() as logfile:
        run([METRICS / "xlogfix_whoisonline.php"], logfile, args.dry_run)


# ---------------------------------------------------------------------------
# tick  (every-5-min cron entry: always updates whoisonline, starts a full
#        metrics run when near the half-hour boundary)
# ---------------------------------------------------------------------------

def cmd_tick(args):
    # Capture the minute now so we can decide on the metrics run
    # before whoisonline consumes any time.
    at_metrics_tick = (datetime.now().minute == 30)

    # Always update the who-is-online map — fast, no lock needed.
    run([METRICS / "xlogfix_whoisonline.php"], logfile=None, dry_run=args.dry_run)

    # At :30 past each hour, attempt a full metrics run.
    # cmd_run acquires its own lock, so concurrent ticks fast-exit there.
    if at_metrics_tick:
        cmd_run(args)


# ---------------------------------------------------------------------------
# resolve-dns  (async reverse-DNS via aiodns; replaces xlogfix_dns_v2.sh
#               + xlogfix_dns_worker.php fan-out)
# ---------------------------------------------------------------------------

# Built-in defaults.  Overridable by /etc/hubzero-metrics/hzmetrics.conf
# (INI format, [dns] section), then by env vars HZMETRICS_DNS_NAMESERVER /
# HZMETRICS_DNS_CONCURRENCY / HZMETRICS_DNS_TIMEOUT, then by CLI flags.
#
# Defaults are deliberately conservative: aim at the system resolver (no
# unbound assumed) at a concurrency that empirically stayed under the
# Purdue DNS rate-limit floor.  Operators who deploy a local or central
# unbound should override:
#     [dns]
#     nameserver  = 127.0.0.1     ; or central unbound IP
#     concurrency = 500
# unbound absorbs c=500 cleanly; the system resolver does not — c=500
# direct-to-system regressed in benchmarking.  c=100 to system is fine.
_DEFAULT_DNS_NAMESERVER  = "system"
_DEFAULT_DNS_CONCURRENCY = 100
_DEFAULT_DNS_TIMEOUT     = 2.0

HZMETRICS_CONF = Path("/etc/hubzero-metrics/hzmetrics.conf")

def _read_dns_config():
    """Resolve DNS-related settings from config file → env vars → defaults.

    Tolerant of an unreadable config: PermissionError / FileNotFoundError
    silently fall through to env vars and built-in defaults (so the script
    keeps working when invoked by a user without /etc/hubzero-metrics
    access)."""
    ns          = _DEFAULT_DNS_NAMESERVER
    concurrency = _DEFAULT_DNS_CONCURRENCY
    timeout     = _DEFAULT_DNS_TIMEOUT

    try:
        text = HZMETRICS_CONF.read_text()
    except (FileNotFoundError, PermissionError):
        text = None
    if text is not None:
        import configparser, io
        cp = configparser.ConfigParser()
        try:
            cp.read_file(io.StringIO(text))
            if cp.has_section("dns"):
                ns          = cp.get("dns", "nameserver",  fallback=ns).strip()
                concurrency = cp.getint("dns", "concurrency", fallback=concurrency)
                timeout     = cp.getfloat("dns", "timeout",  fallback=timeout)
        except (configparser.Error, ValueError) as e:
            print(f"[warn] {HZMETRICS_CONF}: {e}; using defaults", flush=True)

    ns = os.environ.get("HZMETRICS_DNS_NAMESERVER", ns)
    try:
        concurrency = int(os.environ.get("HZMETRICS_DNS_CONCURRENCY", concurrency))
    except ValueError:
        pass
    try:
        timeout = float(os.environ.get("HZMETRICS_DNS_TIMEOUT", timeout))
    except ValueError:
        pass

    return ns, concurrency, timeout

DNS_NAMESERVER, DNS_CONCURRENCY, DNS_TIMEOUT = _read_dns_config()

def hub_db_name():
    """Parse hub_db (the hub-side DB name) from access.cfg."""
    text = ACCESS_CFG.read_text()
    m = re.search(r"\$hub_db\s*=\s*'([^']*)'", text)
    return m.group(1) if m else ""

def _open_db(database=None):
    """Open a pymysql connection from access.cfg.  Lazy import so other
    hzmetrics.py commands don't pay the dep if they don't need it."""
    import pymysql
    host, user, password, _ = db_credentials()
    return pymysql.connect(
        host=host, user=user, password=password,
        database=database, autocommit=True, charset="utf8mb4",
    )

async def _resolve_ips_async(ips, nameserver, concurrency, timeout):
    """Resolve IPs to (ip, host) pairs.  Returns '?' for any failure / no-PTR.

    nameserver='system' (case-insensitive) or '' / None means: use whatever
    /etc/resolv.conf points at (no explicit override).  Otherwise pass the
    string as a single nameserver IP to aiodns.
    """
    import asyncio
    import aiodns
    if not nameserver or str(nameserver).strip().lower() == "system":
        resolver = aiodns.DNSResolver(timeout=timeout)
    else:
        resolver = aiodns.DNSResolver(nameservers=[nameserver], timeout=timeout)
    sem = asyncio.Semaphore(concurrency)
    async def one(ip):
        async with sem:
            try:
                r = await resolver.gethostbyaddr(ip)
                return ip, (r.name if r and r.name else "?")
            except aiodns.error.DNSError:
                return ip, "?"
    return await asyncio.gather(*(one(ip) for ip in ips))

def _expand_date_token(tok, *, side):
    """Expand 'YYYY' / 'YYYY-MM' / 'YYYY-MM-DD' to a date.

    side='start' anchors to the first day of the period;
    side='end'   anchors to the first day AFTER the period (so the
                 caller can use < end for an exclusive bound).
    """
    parts = tok.strip().split("-")
    if len(parts) == 1:
        y = int(parts[0])
        return date(y, 1, 1) if side == "start" else date(y + 1, 1, 1)
    if len(parts) == 2:
        y, m = int(parts[0]), int(parts[1])
        if side == "start":
            return date(y, m, 1)
        first = date(y, m, 1)
        return (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    if len(parts) == 3:
        d = date(int(parts[0]), int(parts[1]), int(parts[2]))
        return d if side == "start" else d + timedelta(days=1)
    raise ValueError(f"unrecognized date token {tok!r}; expected YYYY, YYYY-MM, or YYYY-MM-DD")

def parse_date_range(spec):
    """Parse a flexible date-range spec into (start, end-exclusive).

    Accepts any of:
        YYYY                       a whole year
        YYYY-MM                    a whole month
        YYYY-MM-DD                 a single day
        <left>..<right>            a range; each side any of the above
        ..<right>                  open-ended lower bound — everything before <right>
        <left>..                   open-ended upper bound — everything from <left> onward

    Either returned bound may be None to signal "no limit on that side".
    Right-side resolves to the FIRST day after its period, so the caller
    treats end as exclusive (`{col} < end`).
    """
    if ".." in spec:
        left, right = spec.split("..", 1)
        if left.strip():
            start = _expand_date_token(left, side="start")
            # closed range: right side is "end of period <right>" — first day AFTER
            right_side = "end"
        else:
            start = None
            # open-ended `..<right>` means "before <right>" — right side is the
            # START of <right>'s period (exclusive boundary).  Otherwise
            # `..2025` would mean "before end-of-2025" which is surprising.
            right_side = "start"
        end = _expand_date_token(right, side=right_side) if right.strip() else None
    else:
        start = _expand_date_token(spec, side="start")
        end   = _expand_date_token(spec, side="end")
    if start is not None and end is not None and end <= start:
        raise ValueError(f"empty or inverted date range: {spec!r} → {start}..{end}")
    return start, end

def do_resolve_dns(db_key, table, date_spec=None, *, all_dates=False,
                   nameserver=DNS_NAMESERVER, concurrency=DNS_CONCURRENCY,
                   timeout=DNS_TIMEOUT, logfile=None, dry_run=False):
    """
    Reverse-DNS resolve unresolved IPs in <db>.<table>.  Replaces the
    PHP/shell xlogfix_dns_v2.sh + xlogfix_dns_worker.php fan-out with
    one async Python pass through a local/central unbound.

    db_key:    'metrics' or 'hub' — which DB the table lives in.
    table:     web | toolstart | sessionlog_metrics | …
    date_spec: flexible date / date range string.  Accepts
               YYYY, YYYY-MM, YYYY-MM-DD, or `<start>..<end>` of any
               combination.  If None, defaults to the last 7 days unless
               all_dates is set.  See parse_date_range().
    all_dates: if True, drop the date filter entirely — useful for
               cross-month backfill of orphaned unresolved IPs.
    """
    try:
        import asyncio    # asyncio.run requires python >= 3.7
        import aiodns     # noqa: F401 — fails loudly if missing
        import pymysql    # noqa: F401
    except ImportError as e:
        msg = (f"[resolve-dns] missing dependency: {e}. "
               f"Install via 'python3 -m pip install aiodns pymysql' (needs python >= 3.7).")
        print(msg, flush=True)
        if logfile: logfile.write(msg + "\n")
        return 1

    _, _, _, metrics_db = db_credentials()
    if db_key == "metrics":
        db_name = metrics_db
    elif db_key == "hub":
        db_name = hub_db_name()
    else:
        msg = f"[resolve-dns] unknown db_key {db_key!r}; expected 'metrics' or 'hub'"
        print(msg, flush=True)
        return 2
    if not db_name:
        msg = f"[resolve-dns] could not resolve DB name for db_key={db_key!r} from access.cfg"
        print(msg, flush=True)
        return 2

    # sessionlog_metrics is keyed on 'start'; everyone else on 'datetime'
    d_col = "start" if table == "sessionlog_metrics" else "datetime"

    # Resolve scope: all rows, a parsed range (possibly half-open), or default last-7-days.
    if all_dates:
        start_d = end_d = None
    else:
        if date_spec:
            try:
                start_d, end_d = parse_date_range(date_spec)
            except ValueError as e:
                msg = f"[resolve-dns] {e}"
                print(msg, flush=True)
                if logfile: logfile.write(msg + "\n")
                return 2
        else:
            end_d = date.today()
            start_d = end_d - timedelta(days=7)

    def _build_pred(col_alias=""):
        col = f"{col_alias}{d_col}" if col_alias else d_col
        parts = []
        if start_d is not None:
            parts.append(f"AND {col} >= '{start_d.isoformat()} 00:00:00'")
        if end_d is not None:
            parts.append(f"AND {col} < '{end_d.isoformat()} 00:00:00'")
        return " ".join(parts) + (" " if parts else "")

    if start_d is None and end_d is None:
        scope_label = "ALL" if all_dates else "(unbounded)"
    elif start_d is None:
        scope_label = f"..{end_d}"
    elif end_d is None:
        scope_label = f"{start_d}.."
    else:
        scope_label = f"{start_d}..{end_d}"
    date_pred = _build_pred()

    sel_sql = (
        f"SELECT DISTINCT ip FROM {table} "
        f"WHERE ip <> '' AND ip IS NOT NULL "
        f"{date_pred}"
        f"AND (host IS NULL OR host = '')"
    )

    def log(s):
        print(s, flush=True)
        if logfile: logfile.write(s + "\n")

    # Use one pymysql connection for the whole flow: select, async resolve
    # (network-bound, releases the connection), then temp-table insert +
    # update join.  Temp table is per-connection so it has to be one conn.
    conn = _open_db(db_name)
    try:
        with conn.cursor() as cur:
            cur.execute(sel_sql)
            ips = [r[0] for r in cur.fetchall()]

            log(f"[resolve-dns] {db_name}.{table} {scope_label}: "
                f"{len(ips)} unresolved IPs (ns={nameserver}, c={concurrency})")
            if not ips or dry_run:
                return 0

            import time
            t0 = time.monotonic()
            pairs = asyncio.run(_resolve_ips_async(ips, nameserver, concurrency, timeout))
            wall = time.monotonic() - t0
            resolved_count = sum(1 for _, h in pairs if h != "?")
            no_ptr   = len(pairs) - resolved_count
            rate     = len(pairs) / wall if wall > 0 else 0
            log(f"[resolve-dns] resolved={resolved_count} no_ptr={no_ptr} "
                f"wall={wall:.1f}s rate={rate:.0f} IPs/s")

            cur.execute(
                "CREATE TEMPORARY TABLE _dns_tmp ("
                "ip VARCHAR(45) NOT NULL PRIMARY KEY, host VARCHAR(255)) ENGINE=Memory")
            cur.executemany(
                "INSERT INTO _dns_tmp (ip, host) VALUES (%s, %s)", pairs)
            update_date_pred = _build_pred("t.")
            cur.execute(
                f"UPDATE {table} t INNER JOIN _dns_tmp d ON t.ip = d.ip "
                f"SET t.host = d.host "
                f"WHERE (t.host IS NULL OR t.host = '') {update_date_pred}")
            updated = cur.rowcount
            log(f"[resolve-dns] applied: {updated} rows updated in {table}")
            return 0
    finally:
        conn.close()

def cmd_resolve_dns(args):
    with log_open() as lf:
        return do_resolve_dns(
            args.db_key, args.table, args.date_spec,
            all_dates=args.all,
            nameserver=args.nameserver,
            concurrency=args.concurrency,
            timeout=args.timeout,
            logfile=lf,
            dry_run=args.dry_run,
        )


# ---------------------------------------------------------------------------
# run  (autonomous daily / catch-up mode)
# ---------------------------------------------------------------------------

def cmd_run(args):
    dry_run = args.dry_run

    if not dry_run:
        if not acquire_lock():
            print(f"[run] still running — skipping.")
            return

    try:
        today_str  = date.today().strftime("%Y-%m")
        today_date = date.today().isoformat()
        prev       = previous_month(today_str)

        state          = read_state()
        analyzed_today = state.get("analyzed") == today_date

        all_pending_months = sorted(set(
            f"{d[:4]}-{d[4:6]}"
            for d, _ in dated_files(HTTPD_DAILY, f"{SITE}-access*log*")
        ))
        backlog_months  = [m for m in all_pending_months if m < today_str]
        current_pending = pending_days_for_month(today_str)
        has_pending     = bool(current_pending or backlog_months)

        # Check whether the previous month is complete but not yet summarized.
        prev_needs_work = (
            not is_month_summarized(prev) and is_month_fully_imported(prev)
        )

        # Fast exit: nothing in daily/, already analyzed today, nothing left to summarize.
        if not has_pending and analyzed_today and not prev_needs_work:
            print(f"[run] nothing to do")
            return

        with log_open() as logfile:
            if not dry_run:
                logfile.write(f"\n=== hzmetrics.py run @ {datetime.now()} ===\n")

            # Always import pending days for the current month first.
            if current_pending:
                print(f"[run] importing {len(current_pending)} pending day(s) for {today_str}")
                for date_str in current_pending:
                    print(f"\n--- {date_str} ---")
                    do_import_day(date_str, logfile, dry_run)

            if backlog_months:
                # Catch-up mode: one backlog month per tick.
                target    = backlog_months[0]
                remaining = len(backlog_months)
                print(f"\n[run] catch-up: {remaining} backlog month(s) — processing {target}")
                if not dry_run:
                    logfile.write(f"catch-up: {target} ({remaining} remaining)\n")
                days = pending_days_for_month(target)
                for date_str in days:
                    print(f"\n--- {date_str} ---")
                    do_import_day(date_str, logfile, dry_run)
                print(f"\n>>> analyzing {target}")
                do_analyze(target, logfile, dry_run)
                print(f"\n>>> summarizing {target}")
                do_summarize(target, logfile, dry_run)

            else:
                # Normal mode: analyze current month once per day, then check previous month.
                if not analyzed_today:
                    print(f"\n[run] analyzing current month {today_str}")
                    do_analyze(today_str, logfile, dry_run)
                    if not dry_run:
                        update_state(analyzed=today_date)

                if prev_needs_work:
                    print(f"\n[run] {prev} complete — analyzing and summarizing")
                    do_analyze(prev, logfile, dry_run)
                    do_summarize(prev, logfile, dry_run)
                elif not is_month_summarized(prev):
                    last    = last_day_of_month(prev)
                    days_in = date.today().day
                    if days_in > 5:
                        print(f"\n[run] WARNING: {prev} last day ({last}) never arrived "
                              f"({days_in}d into {today_str}) — summarizing without it")
                        do_analyze(prev, logfile, dry_run)
                        do_summarize(prev, logfile, dry_run)
                    else:
                        print(f"\n[run] {prev} last day ({last}) not yet imported — deferring")

            print("\n>>> done")

    finally:
        if not dry_run:
            release_lock()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Metrics pipeline manager")
    sub = parser.add_subparsers(dest="command")

    p_tick = sub.add_parser("tick", help="Every-5-min cron entry: whoisonline always, metrics run at :30")
    p_tick.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_run = sub.add_parser("run", help="Autonomous daily/catch-up metrics run (called by tick at :30)")
    p_run.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_woo = sub.add_parser("whoisonline", help="Update real-time session geo map")
    p_woo.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    sub.add_parser("status", help="Show pipeline state")

    p_process = sub.add_parser("process", help="Import logs, analyze, and summarize for a month (normal usage)")
    grp = p_process.add_mutually_exclusive_group()
    grp.add_argument("--next",  action="store_true",  help="Use the oldest pending month")
    grp.add_argument("--month", metavar="YYYY-MM",    help="Specify a month")
    grp.add_argument("--day",   metavar="YYYY-MM-DD", help="Specify a single day")
    p_process.add_argument("--force",   action="store_true", help="Skip order and current-month checks")
    p_process.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_import = sub.add_parser("import", help="Raw log ingestion only — fetch, import, archive")
    grp2 = p_import.add_mutually_exclusive_group()
    grp2.add_argument("--next",  action="store_true",  help="Use the oldest pending month")
    grp2.add_argument("--month", metavar="YYYY-MM",    help="Specify a month")
    grp2.add_argument("--day",   metavar="YYYY-MM-DD", help="Specify a single day")
    p_import.add_argument("--force",    action="store_true", help="Skip order and current-month checks")
    p_import.add_argument("--dry-run",  action="store_true", help="Show what would be done without doing it")

    p_analyze = sub.add_parser("analyze", help="Run enrichment and stats for a completed month")
    p_analyze.add_argument("--month",   metavar="YYYY-MM", required=True)
    p_analyze.add_argument("--force",   action="store_true", help="Run even if month is not yet complete")
    p_analyze.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_summarize = sub.add_parser("summarize", help="Run rolling-window aggregation for a completed month")
    p_summarize.add_argument("--month",   metavar="YYYY-MM", required=True)
    p_summarize.add_argument("--force",   action="store_true", help="Run even if month is not yet complete")
    p_summarize.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_setup = sub.add_parser("setup-db", help="Create metrics database and all tables (idempotent)")
    p_setup.add_argument("--dry-run", action="store_true", help="Show statements without executing")

    p_migrate = sub.add_parser("migrate", help="Show or apply schema migrations")
    p_migrate.add_argument("--apply", action="store_true", help="Apply all pending migrations")

    p_dnload = sub.add_parser("backfill-dnload", help="Populate web.dnload flag for historical rows")
    p_dnload.add_argument("--start", metavar="YYYY-MM", help="Only process months >= this (default: all)")
    p_dnload.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_geo = sub.add_parser("fill-geo", help="Backfill missing GeoIP country data")
    grp_geo = p_geo.add_mutually_exclusive_group(required=True)
    grp_geo.add_argument("--month", metavar="YYYY-MM", help="Fill a specific month")
    grp_geo.add_argument("--all",   action="store_true", help="Fill all months with missing GeoIP data")
    p_geo.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_dns = sub.add_parser("resolve-dns",
        help="Reverse-DNS resolve unresolved IPs in a metrics table (replaces xlogfix_dns_v2.sh)",
        description=(
            "Resolve reverse DNS for unresolved IPs in a metrics table.\n"
            "Date scope is flexible: a year, month, or day, or a range using '..':\n"
            "  YYYY                    e.g. 2024                — whole year\n"
            "  YYYY-MM                 e.g. 2024-10             — whole month\n"
            "  YYYY-MM-DD              e.g. 2024-10-15          — single day\n"
            "  <start>..<end>          e.g. 2024-10..2024-12    — closed range\n"
            "  ..<end>                 e.g. ..2025              — everything before <end>\n"
            "  <start>..               e.g. 2025-07-01..        — everything from <start> on\n"
            "Each side of a range may use any granularity (YYYY / YYYY-MM / YYYY-MM-DD).\n"
            "Omit the date to default to the last 7 days; use --all for everything."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p_dns.add_argument("db_key", choices=["metrics", "hub"],
        help="Target DB ('metrics' or 'hub')")
    p_dns.add_argument("table",
        help="Target table (web | toolstart | sessionlog_metrics | ...)")
    p_dns.add_argument("date_spec", nargs="?", default=None, metavar="DATE_OR_RANGE",
        help="YYYY | YYYY-MM | YYYY-MM-DD or '<start>..<end>' of any combination "
             "(default: last 7 days)")
    p_dns.add_argument("--all", action="store_true",
        help="Resolve every unresolved IP in the table regardless of date "
             "(for cross-month backfill)")
    p_dns.add_argument("--nameserver", "-n", default=DNS_NAMESERVER,
        help=f"DNS server IP — set to a local/central unbound for max speed "
             f"(default '{DNS_NAMESERVER}')")
    p_dns.add_argument("--concurrency", "-c", type=int, default=DNS_CONCURRENCY,
        help=f"aiodns concurrency (default {DNS_CONCURRENCY})")
    p_dns.add_argument("--timeout", "-t", type=float, default=DNS_TIMEOUT,
        help=f"DNS timeout seconds (default {DNS_TIMEOUT})")
    p_dns.add_argument("--dry-run", action="store_true",
        help="Just count unresolved IPs; don't resolve or update")

    args = parser.parse_args()

    if args.command == "tick":
        cmd_tick(args)
    elif args.command == "whoisonline":
        cmd_whoisonline(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "status":
        cmd_status()
    elif args.command == "process":
        cmd_process(args)
    elif args.command == "import":
        cmd_import(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "summarize":
        cmd_summarize(args)
    elif args.command == "setup-db":
        cmd_setup_db(args)
    elif args.command == "migrate":
        cmd_migrate(args)
    elif args.command == "backfill-dnload":
        cmd_backfill_dnload(args)
    elif args.command == "fill-geo":
        cmd_fill_geo(args)
    elif args.command == "resolve-dns":
        cmd_resolve_dns(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
