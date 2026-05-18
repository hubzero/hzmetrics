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
  hzmetrics.py clean-bots {web|websessions} [YYYY-MM | range]
  hzmetrics.py import-hub-data
  hzmetrics.py import-auth <file>      (file may be '-' for stdin)
  hzmetrics.py fill-user-info {metrics|hub} <table>
  hzmetrics.py identify-bots <file>    (file may be '-' for stdin)
  hzmetrics.py import-webhits <file>   (file may be '-' for stdin)
  hzmetrics.py fill-domain {metrics|hub} <table>
  hzmetrics.py import-apache <file>    (file may be '-' for stdin)
  hzmetrics.py andmore-usage [YYYY-MM]
  hzmetrics.py fill-ipcountry {metrics|hub} <table> [DATE_OR_RANGE]
  hzmetrics.py gen-tool-stats [YYYY-MM]
  hzmetrics.py gen-tool-tops  [YYYY-MM]
  hzmetrics.py gen-tool-toplists [YYYY-MM]
  hzmetrics.py middleware-wall
  hzmetrics.py middleware-cpu
  hzmetrics.py migrate [--apply]
  hzmetrics.py setup-db
"""

import os
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# Python version self-relaunch.  hzmetrics.py uses asyncio.run() (3.7+) and
# match-case / typing features at the 3.10+ level, but Rocky/RHEL 8 ships
# /usr/bin/python3 as 3.6.  If the current interpreter is too old, re-exec
# under the first available newer python found on PATH.  Cron / wrappers can
# safely invoke `python3 /opt/hubzero/bin/hzmetrics.py` regardless.
# ---------------------------------------------------------------------------

_MIN_PYTHON = (3, 10)

def _relaunch_if_needed():
    if sys.version_info >= _MIN_PYTHON:
        return

    # Scan PATH for every `python3.N` interpreter (auto-discovers future
    # versions — python3.14, python3.20, … — without hard-coding).  Build a
    # list of (parsed_version, exe_path) tuples, de-duped by realpath so a
    # symlink farm doesn't probe the same binary twice.
    import re as _re
    from pathlib import Path as _Path
    pat = _re.compile(r"^python3\.(\d+)$")
    self_real = _Path(sys.executable).resolve()
    seen = {self_real}
    cands = []  # list of ((major, minor), exe)
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        try:
            entries = list(_Path(d).iterdir())
        except OSError:
            continue
        for entry in entries:
            m = pat.match(entry.name)
            if not m:
                continue
            minor = int(m.group(1))
            if (3, minor) < _MIN_PYTHON:
                continue
            if not os.access(entry, os.X_OK):
                continue
            try:
                real = entry.resolve()
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)
            cands.append(((3, minor), str(entry)))

    # Try highest version first.
    cands.sort(reverse=True)
    for _, exe in cands:
        # Confirm reality (name → version mismatch can happen with aliases).
        check = subprocess.run(
            [exe, "-c", f"import sys; raise SystemExit(sys.version_info < {_MIN_PYTHON})"]
        )
        if check.returncode == 0:
            os.execv(exe, [exe, *sys.argv])

    versions = ", ".join(f"{mj}.{mi}" for (mj, mi), _ in cands) or "none"
    raise SystemExit(
        f"hzmetrics.py requires Python {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}+; "
        f"running {sys.version.split()[0]}.  "
        f"Newer python3.X on PATH: {versions}."
    )

_relaunch_if_needed()


import argparse
import gzip
import logging
import re
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("hzmetrics")


def _pipeline_paths():
    """Detect site name and APACHELOGDIR from /etc/hubzero.conf and the
    presence of /etc/apache2 vs /etc/httpd.  Falls back to safe defaults
    so the module still imports outside a deployed hub host."""
    site = "hub"
    try:
        with open("/etc/hubzero.conf") as f:
            for line in f:
                m = re.match(r"\s*site\s*=\s*(\S+)", line)
                if m:
                    site = m.group(1).strip()
                    break
    except (FileNotFoundError, PermissionError, OSError):
        pass
    if Path("/etc/apache2").is_dir():
        apache_log_dir = Path("/var/log/apache2")
    else:
        apache_log_dir = Path("/var/log/httpd")
    return {
        "site":            site,
        "apache_log_dir": apache_log_dir,
        "cms_log_dir":    Path("/var/log/hubzero"),
        "metrics_log_dir": Path("/var/log/hubzero/metrics"),
    }


_PIPELINE_PATHS    = _pipeline_paths()
SITE               = _PIPELINE_PATHS["site"]
APACHE_LOG_DIR     = _PIPELINE_PATHS["apache_log_dir"]
CMS_LOG_DIR        = _PIPELINE_PATHS["cms_log_dir"]
HTTPD_DAILY        = APACHE_LOG_DIR / "daily"
HZ_DAILY           = CMS_LOG_DIR / "daily"
HTTPD_IMPORTED     = APACHE_LOG_DIR / "imported"
HZ_IMPORTED        = CMS_LOG_DIR / "imported"
HZ_METRICS_STAGING = _PIPELINE_PATHS["metrics_log_dir"]
STAGED_APACHE      = HZ_METRICS_STAGING / "_hub_apache.log"
STAGED_AUTH        = HZ_METRICS_STAGING / "_hub_auth.log"

LOG         = HZ_METRICS_STAGING / "manage.log"
LOCK_FILE   = Path("/var/run/hzmetrics/hzmetrics.pid")
STATE_FILE  = Path("/var/run/hzmetrics/hzmetrics.state")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    """Configure the `hzmetrics` logger with a timestamped format on both
    stderr (for cron-emailed output) and the persistent pipeline log file.

    Log file path defaults to LOG and may be overridden via the
    HZMETRICS_LOG env var — used by the A/B test harness (running as the
    developer's UID, not apache) to write to a path it actually owns.

    Idempotent: re-invocation replaces any previously installed handlers
    so test setups can call it more than once."""
    log.setLevel(logging.DEBUG)
    for h in list(log.handlers):
        log.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler()
    stream.setLevel(logging.INFO)
    stream.setFormatter(fmt)
    log.addHandler(stream)

    log_path = Path(os.environ.get("HZMETRICS_LOG", str(LOG)))
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        log.addHandler(file_handler)
    except OSError as e:
        log.warning("could not open %s for append: %s", log_path, e)

def dated_files(directory: str | Path, pattern: str) -> list[tuple[str, Path]]:
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

def pending_days_for_month(month_str: str) -> list[str]:
    """Sorted list of date strings in daily/ for the given YYYY-MM."""
    yyyymm = month_str.replace("-", "")
    return [d for d, _ in dated_files(HTTPD_DAILY, f"{SITE}-access*log*") if d.startswith(yyyymm)]

def oldest_pending_month() -> str | None:
    files = dated_files(HTTPD_DAILY, f"{SITE}-access*log*")
    if not files:
        return None
    d = files[0][0]
    return f"{d[:4]}-{d[4:6]}"

def last_imported_date() -> str | None:
    files = dated_files("/var/log/httpd/imported", f"{SITE}-access*log*")
    return files[-1][0] if files else None

def is_current_month(month_str: str) -> bool:
    return month_str == date.today().strftime("%Y-%m")

def _arg_yyyymm(s: str) -> str:
    """argparse `type=` validator: accept 'YYYY-MM', reject anything else.

    Used on CLI args whose value flows into SQL string interpolation, so a
    malformed value can't widen into an injection vector."""
    if not re.fullmatch(r"\d{4}-\d{2}", s):
        raise argparse.ArgumentTypeError(f"expected YYYY-MM, got {s!r}")
    return s

def _open_input(path: str):
    """Open `path` for reading, with `"-"` treated as stdin.

    Returns a context manager so callers can write
    `with _open_input(path) as src:` regardless of which branch they got
    — `nullcontext` keeps stdin from being closed when the `with` exits."""
    if path == "-":
        return nullcontext(sys.stdin)
    return open(path, "r", errors="replace")

def check_order(date_str: str, force: bool) -> None:
    """Abort if date_str would be imported out of order."""
    if force:
        return
    pending = [d for d, _ in dated_files(HTTPD_DAILY, f"{SITE}-access*log*")]
    if pending and date_str > pending[0]:
        log.error(f"{date_str} is not the oldest pending day in daily/.")
        log.error(f"  Oldest pending: {pending[0]}")
        log.error(f"  Use --force to override.")
        raise SystemExit(1)
    last = last_imported_date()
    if last and date_str < last:
        log.error(f"{date_str} is older than the most recently imported log ({last}).")
        log.error(f"  Use --force to override.")
        raise SystemExit(1)

def previous_month(month_str: str) -> str:
    y, m = int(month_str[:4]), int(month_str[5:7])
    m -= 1
    if m == 0:
        m, y = 12, y - 1
    return f"{y:04d}-{m:02d}"

def last_day_of_month(month_str: str) -> str:
    """Return YYYYMMDD for the last calendar day of the given YYYY-MM."""
    y, m = int(month_str[:4]), int(month_str[5:7])
    last = (datetime(y, m, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return last.strftime("%Y%m%d")

def is_month_fully_imported(month_str: str) -> bool:
    """True if the last calendar day of month_str is present in imported/."""
    last = last_day_of_month(month_str)
    return any(d == last for d, _ in dated_files("/var/log/httpd/imported", f"{SITE}-access*log*"))

def is_month_summarized(month_str: str) -> bool:
    _, _, _, metrics_db = db_credentials()
    dt = month_str + "-00"
    count = mysql_scalar(
        f"SELECT COUNT(*) FROM {metrics_db}.summary_user_vals "
        f"WHERE datetime = '{dt}' AND period = 1;"
    )
    return bool(count)

def acquire_lock() -> bool:
    """Try to write a PID lock. Returns True if acquired, False if another instance is running.

    /var/run/hzmetrics/ must be pre-created and owned by the service user:
      install: echo 'd /var/run/hzmetrics 0755 apache apache -' > /etc/tmpfiles.d/hzmetrics.conf
               systemd-tmpfiles --create /etc/tmpfiles.d/hzmetrics.conf
    """
    if not LOCK_FILE.parent.exists():
        log.error(f"{LOCK_FILE.parent} does not exist.")
        log.error(f"  Run once as root:  mkdir -p {LOCK_FILE.parent} && chown apache:apache {LOCK_FILE.parent}")
        raise SystemExit(1)
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            Path(f"/proc/{pid}").stat()  # raises OSError if process is gone
            return False  # still running
        except (ValueError, OSError):
            pass  # stale lock
    LOCK_FILE.write_text(str(os.getpid()))
    return True

def release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass

def read_state() -> dict[str, str]:
    try:
        return dict(
            line.split("=", 1)
            for line in STATE_FILE.read_text().splitlines()
            if "=" in line
        )
    except (FileNotFoundError, ValueError):
        return {}

def update_state(**kwargs: object) -> None:
    state = read_state()
    state.update({k: str(v) for k, v in kwargs.items()})
    STATE_FILE.write_text("".join(f"{k}={v}\n" for k, v in state.items()))


# ---------------------------------------------------------------------------
# Schema migrations
# Applied state is tracked in metrics_db.migrations.  check_sql is what
# lets us auto-detect changes applied before this system existed: the
# query returns a count, and the migration is considered already-applied
# if the count is nonzero — or, when check_expect is set, if the count
# equals check_expect (used for "rows that should no longer exist" purges).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Migration:
    id: int
    description: str
    sql: str                         # uses {metrics_db} placeholder
    check_sql: str | None = None
    check_expect: int | None = None  # if set, "already applied" means count == this

MIGRATIONS = [
    Migration(
        id=1,
        description="Index web(dnload) — applied by backfill-dnload May 2026",
        sql="ALTER TABLE {metrics_db}.web ADD INDEX dnload (dnload);",
        check_sql="SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='web' AND index_name='dnload';",
    ),
    Migration(
        id=2,
        description="Composite index web(sessionid, dnload) — covering index for download_users JOIN",
        sql="ALTER TABLE {metrics_db}.web ADD INDEX web_sessionid_dnload (sessionid, dnload);",
        check_sql="SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='web' AND index_name='web_sessionid_dnload';",
    ),
    Migration(
        id=3,
        description="Composite index websessions(datetime, jobs, duration, ipcountry) — filter pushdown for int/download_users",
        sql="ALTER TABLE {metrics_db}.websessions ADD INDEX ws_datetime_jobs_dur_country (datetime, jobs, duration, ipcountry);",
        check_sql="SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='websessions' AND index_name='ws_datetime_jobs_dur_country';",
    ),
    Migration(
        id=4,
        description="Purge userlogin rows with action not in (login, simulation) — detect/invalid/logout are never queried",
        sql="DELETE FROM {metrics_db}.userlogin WHERE action NOT IN ('login', 'simulation');",
        check_sql="SELECT COUNT(*) FROM {metrics_db}.userlogin WHERE action NOT IN ('login', 'simulation');",
        check_expect=0,
    ),
    Migration(
        id=5,
        description="Index websessions(domain) — speeds up domainclass JOIN in download org queries",
        sql="ALTER TABLE {metrics_db}.websessions ADD INDEX ws_domain (domain);",
        check_sql="SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='websessions' AND index_name='ws_domain';",
    ),
    Migration(
        id=6,
        description="Index websessions(jobs, ipcountry, duration) — period-14 all-time download_users filter",
        sql="ALTER TABLE {metrics_db}.websessions ADD INDEX ws_jobs_country_dur (jobs, ipcountry, duration);",
        check_sql="SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='websessions' AND index_name='ws_jobs_country_dur';",
    ),
]

MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {metrics_db}.migrations (
    id INT NOT NULL,
    description VARCHAR(255),
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);
"""

def ensure_migrations_table(metrics_db: str) -> None:
    mysql_exec(MIGRATIONS_TABLE_SQL.format(metrics_db=metrics_db))
    _automark_applied(metrics_db)

def _automark_applied(metrics_db: str) -> None:
    """Mark migrations as applied if the schema change already exists (applied outside this system)."""
    applied = applied_migration_ids(metrics_db)
    for m in MIGRATIONS:
        if m.id in applied or not m.check_sql:
            continue
        count = mysql_scalar(m.check_sql.format(metrics_db=metrics_db))
        if count is None:
            continue
        if m.check_expect is None:
            already = count != 0
        else:
            already = count == m.check_expect
        if already:
            mysql_exec(
                f"INSERT IGNORE INTO {metrics_db}.migrations (id, description) "
                f"VALUES (%s, %s);",
                (m.id, m.description),
            )

def applied_migration_ids(metrics_db: str) -> set[int]:
    return set(mysql_column(f"SELECT id FROM {metrics_db}.migrations ORDER BY id;"))

def cmd_migrate(args):
    _, _, _, metrics_db = db_credentials()
    ensure_migrations_table(metrics_db)
    applied = applied_migration_ids(metrics_db)

    log.info(f"{'ID':<4}  {'STATUS':<9}  DESCRIPTION")
    log.info("-" * 72)
    for m in MIGRATIONS:
        status = "applied" if m.id in applied else "PENDING"
        log.info(f"{m.id:<4}  {status:<9}  {m.description}")

    pending = [m for m in MIGRATIONS if m.id not in applied]
    if not pending:
        log.info("All migrations applied.")
        return

    log.info(f"{len(pending)} pending migration(s).")

    if not args.apply:
        log.info("Run with --apply to execute them.")
        return

    log.debug(f"=== hzmetrics.py migrate --apply  @ {datetime.now()} ===")
    for m in pending:
        sql = m.sql.format(metrics_db=metrics_db)
        log.info(f"[{m.id}] {m.description}")
        log.info(f"    {sql}")
        rc = mysql_exec(sql)
        if rc == 0:
            mysql_exec(
                f"INSERT IGNORE INTO {metrics_db}.migrations (id, description) "
                f"VALUES (%s, %s);",
                (m.id, m.description),
            )
            log.info(f"    done.")
            log.debug(f"migration {m.id}: {m.description}")
        else:
            log.error(f"    FAILED (rc={rc}) — stopping.")
            log.debug(f"migration {m.id} FAILED")
            break

    log.info(">>> done")


# ---------------------------------------------------------------------------
# setup-db  (create metrics database and all tables; idempotent)
# ---------------------------------------------------------------------------

METRICS_DB_DDL = [
    "CREATE DATABASE IF NOT EXISTS `{metrics_db}` DEFAULT CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`bot_useragents` (
  `useragent` tinytext NOT NULL DEFAULT '',
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
  `name` tinytext NOT NULL DEFAULT '',
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
  `filter` varchar(65) NOT NULL DEFAULT '',
  `type` varchar(65) NOT NULL DEFAULT 'domain',
  `notes` varchar(120) DEFAULT NULL,
  `date_added` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `filter_type` (`filter`,`type`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`exclude_list2` (
  `filter` varchar(65) NOT NULL DEFAULT '',
  `type` varchar(65) NOT NULL DEFAULT 'domain',
  `notes` varchar(120) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`jos_xprofiles_metrics` (
  `uidNumber` int(11) NOT NULL DEFAULT 0,
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
  `reason` text NOT NULL DEFAULT '',
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
  `params` text NOT NULL DEFAULT '',
  `note` text NOT NULL DEFAULT '',
  `shadowExpire` int(11) DEFAULT NULL,
  `location` varchar(50) DEFAULT NULL,
  `orcid` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`uidNumber`),
  KEY `idx_username` (`username`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`migrations` (
  `id` int(11) NOT NULL DEFAULT 0,
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
  `sessnum` bigint(20) unsigned NOT NULL DEFAULT 0,
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
  `tool` tinytext NOT NULL DEFAULT '',
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
  `content` tinytext NOT NULL DEFAULT '',
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
            log.info(f"  [dry-run] {label}")
            continue
        rc = mysql_exec(sql)
        if rc != 0:
            errors += 1

    if not dry_run:
        if errors:
            log.error(f"{errors} statement(s) failed.")
        else:
            log.info(f"  {len(METRICS_DB_DDL)} statement(s) executed, database ready.")
    log.info(">>> done")


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

def _stream_decompress(src_path, out_fileobj):
    """zcat -f equivalent — copy bytes from src_path to out_fileobj,
    decompressing gzip on the fly.  1 MiB chunks."""
    opener = gzip.open if src_path.suffix == ".gz" else open
    with opener(src_path, "rb") as src:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            out_fileobj.write(chunk)


def _gzip_in_place(path):
    """gzip --quiet equivalent.  Writes path.gz, preserves mtime, removes
    the original.  Returns the new .gz path."""
    gz = path.with_name(path.name + ".gz")
    with open(path, "rb") as src, gzip.open(gz, "wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
    shutil.copystat(path, gz)
    path.unlink()
    return gz


def _numbered_backup_dst(dst):
    """Pick the first non-conflicting name in the form dst.~N~ —
    matches `mv --backup=numbered`."""
    if not dst.exists():
        return dst
    n = 1
    while True:
        candidate = dst.with_name(f"{dst.name}.~{n}~")
        if not candidate.exists():
            return candidate
        n += 1


def do_fetch_logs(date_filter=None, *, dry_run=False):
    """Concatenate dated daily logs into the metrics staging files.

    Port of import/__fetch_apache_and_auth_log.sh.  With date_filter=None
    we glob all files in daily/; with date_filter='YYYYMMDD' we keep
    only files whose name contains that substring (catch-up mode).
    """
    if date_filter:
        apache_pat  = f"{SITE}-access*log*{date_filter}*"
        cmsauth_pat = f"cmsauth*log*{date_filter}*"
    else:
        apache_pat  = f"{SITE}-access*log*"
        cmsauth_pat = "cmsauth*log*"

    apache_files  = sorted(HTTPD_DAILY.glob(apache_pat))
    cmsauth_files = sorted(HZ_DAILY.glob(cmsauth_pat))

    log.info(f"[fetch-logs] {HTTPD_DAILY}/{apache_pat}: {len(apache_files)} file(s)")
    log.info(f"[fetch-logs] {HZ_DAILY}/{cmsauth_pat}: {len(cmsauth_files)} file(s)")

    if dry_run:
        for f in apache_files + cmsauth_files:
            log.info(f"  [dry-run] would zcat: {f}")
        return 0

    HZ_METRICS_STAGING.mkdir(parents=True, exist_ok=True)

    if apache_files:
        with open(STAGED_APACHE, "wb") as out:
            for f in apache_files:
                _stream_decompress(f, out)
        log.info(f"  -> {STAGED_APACHE}")
    if cmsauth_files:
        with open(STAGED_AUTH, "wb") as out:
            for f in cmsauth_files:
                _stream_decompress(f, out)
        log.info(f"  -> {STAGED_AUTH}")
    return 0


def do_archive_logs(date_filter=None, *, dry_run=False):
    """gzip each daily log in place and move it to imported/.

    Port of import/__archive_apache_and_auth_log.sh.  Handles four globs:
    {site}-access*, new-{site}-access*, cmsauth*, cmsdebug*.
    """
    def pat(base):
        return f"{base}{date_filter}*" if date_filter else base

    groups = [
        (HTTPD_DAILY, HTTPD_IMPORTED, pat(f"{SITE}-access*log*")),
        (HTTPD_DAILY, HTTPD_IMPORTED, pat(f"new-{SITE}-access*log*")),
        (HZ_DAILY,    HZ_IMPORTED,    pat("cmsauth*log*")),
        (HZ_DAILY,    HZ_IMPORTED,    pat("cmsdebug*log*")),
    ]
    for src_dir, dst_dir, p in groups:
        files = sorted(src_dir.glob(p))
        if not files:
            continue
        log.info(f"[archive-logs] {src_dir}/{p}: {len(files)} file(s)")
        if dry_run:
            for f in files:
                log.info(f"  [dry-run] would gzip+move: {f} -> {dst_dir}/")
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            gz = f if f.suffix == ".gz" else _gzip_in_place(f)
            dst = _numbered_backup_dst(dst_dir / gz.name)
            shutil.move(str(gz), str(dst))
            log.info(f"  archived: {f.name} -> {dst}")
    return 0


def do_import_staged_logs(*, dry_run=False):
    """Run the import-* stages against the staged log files, then move
    each staged file to _prev_*.log.  Port of
    import/__import_apache_and_auth_log.sh.
    """
    if not dry_run:
        HZ_METRICS_STAGING.mkdir(parents=True, exist_ok=True)

    if STAGED_APACHE.exists():
        log.info(f"[import-staged] {STAGED_APACHE}")
        do_import_webhits(str(STAGED_APACHE), dry_run=dry_run)
        do_identify_bots( str(STAGED_APACHE), dry_run=dry_run)
        do_import_apache( str(STAGED_APACHE), dry_run=dry_run)
        if not dry_run:
            prev = HZ_METRICS_STAGING / "_prev_hub_apache.log"
            STAGED_APACHE.replace(prev)
            log.info(f"  -> {prev}")
    else:
        log.info(f"[import-staged] {STAGED_APACHE}: not present, skipping")

    if STAGED_AUTH.exists():
        log.info(f"[import-staged] {STAGED_AUTH}")
        do_import_auth(str(STAGED_AUTH), dry_run=dry_run)
        if not dry_run:
            prev = HZ_METRICS_STAGING / "_prev_hub_auth.log"
            STAGED_AUTH.replace(prev)
            log.info(f"  -> {prev}")
    else:
        log.info(f"[import-staged] {STAGED_AUTH}: not present, skipping")
    return 0


def do_import_day(date_str, dry_run=False):
    """Fetch, import, then archive logs for a single day.  date_str is
    'YYYYMMDD'."""
    if dry_run:
        access  = sorted(HTTPD_DAILY.glob(f"{SITE}-access*log*{date_str}*"))
        cmsauth = sorted(HZ_DAILY.glob(   f"cmsauth*log*{date_str}*"))
        for f in access + cmsauth:
            log.info(f"    [dry-run] would fetch: {f}")
        if not access:
            log.info(f"    [dry-run] WARNING: no access log found for {date_str} in {HTTPD_DAILY}")
        if not cmsauth:
            log.info(f"    [dry-run] WARNING: no cmsauth log found for {date_str} in {HZ_DAILY}")
    do_fetch_logs(       date_str, dry_run=dry_run)
    do_import_staged_logs(         dry_run=dry_run)
    do_archive_logs(     date_str, dry_run=dry_run)


def cmd_fetch_logs(args):
    return do_fetch_logs(args.date or None, dry_run=args.dry_run)


def cmd_archive_logs(args):
    return do_archive_logs(args.date or None, dry_run=args.dry_run)

def _stage_banner(name):
    """Mark a pipeline stage boundary."""
    log.info("=== %s ===", name)


def _do_tool_metrics_stage(month_str, dry_run):
    """Run the per-month tool-metrics enrichment + stats chain in-process.
    Direct port of __process_tool_metrics.sh."""
    _stage_banner("tool-metrics")
    do_import_hub_data(dry_run=dry_run)
    do_resolve_dns("metrics", "sessionlog_metrics", month_str,
                   dry_run=dry_run)
    do_fill_domain("metrics", "sessionlog_metrics", month_str,
                   dry_run=dry_run)
    do_fill_user_info("metrics", "sessionlog_metrics", month_str,
                      dry_run=dry_run)
    do_fill_ipcountry("metrics", "sessionlog_metrics", month_str,
                      dry_run=dry_run)
    do_gen_tool_stats(month_str,    dry_run=dry_run)
    do_gen_tool_tops(month_str,     dry_run=dry_run)
    do_gen_tool_toplists(month_str, dry_run=dry_run)


def _do_usage_metrics_stage(month_str, dry_run):
    """Run the per-month web / toolstart / websessions enrichment chain
    in-process.  Direct port of __process_usage_metrics.sh."""
    _stage_banner("usage-metrics")
    do_import_hub_data(dry_run=dry_run)
    do_middleware_wall(dry_run=dry_run)
    do_middleware_cpu( dry_run=dry_run)
    do_resolve_dns("metrics", "web",       month_str, dry_run=dry_run)
    do_resolve_dns("metrics", "toolstart", month_str, dry_run=dry_run)
    do_fill_domain("metrics", "web",       month_str, dry_run=dry_run)
    do_fill_domain("metrics", "toolstart", month_str, dry_run=dry_run)
    do_logfix_session(month_str, dry_run=dry_run)
    do_clean_bots("web",         month_str, dry_run=dry_run)
    do_clean_bots("websessions", month_str, dry_run=dry_run)
    do_fill_user_info("metrics", "toolstart",   month_str, dry_run=dry_run)
    do_fill_ipcountry("metrics", "web",         month_str, dry_run=dry_run)
    do_fill_ipcountry("metrics", "websessions", month_str, dry_run=dry_run)
    do_fill_ipcountry("metrics", "toolstart",   month_str, dry_run=dry_run)


def _do_summary_stage(month_str, dry_run):
    """Run the per-month rolling-window summary stage in-process.
    Direct port of __process_usage_metrics_summary.sh."""
    _stage_banner("summary")
    do_import_hub_data(dry_run=dry_run)
    do_summarize_month(month_str, dry_run=dry_run)
    do_andmore_usage( month_str, dry_run=dry_run)


def do_analyze(month_str, dry_run=False):
    month_str = month_str or None
    _do_tool_metrics_stage(month_str, dry_run)
    _do_usage_metrics_stage(month_str, dry_run)


def do_summarize(month_str, dry_run=False):
    _do_summary_stage(month_str or None, dry_run)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args):
    def summarize(directory, pattern, label):
        files = dated_files(directory, pattern)
        count = len(files)
        if count == 0:
            log.info(f"  {label}: 0")
            return
        oldest, newest = files[0][0], files[-1][0]
        span = f"({oldest})" if oldest == newest else f"({oldest} .. {newest})"
        log.info(f"  {label}: {count}  {span}")

    log.info("=== daily/ (pending import) ===")
    summarize(HTTPD_DAILY, f"{SITE}-access*log*", "httpd access")
    summarize(HZ_DAILY,    "cmsauth*log*",         "cmsauth      ")

    log.info("=== imported/ (already processed) ===")
    summarize("/var/log/httpd/imported",   f"{SITE}-access*log*", "httpd  ")
    summarize("/var/log/hubzero/imported", "cmsauth*log*",         "hubzero")

    log.info("=== resolve-dns settings ===")
    try:
        conf_present = HZMETRICS_CONF.is_file()
        conf_src = str(HZMETRICS_CONF) if conf_present else "(not present — using defaults / env)"
    except PermissionError:
        conf_src = f"{HZMETRICS_CONF} (no read access — using defaults / env)"
    log.info(f"  config file: {conf_src}")
    log.info(f"  nameserver : {DNS_NAMESERVER}")
    log.info(f"  concurrency: {DNS_CONCURRENCY}")
    log.info(f"  timeout    : {DNS_TIMEOUT}s")


# ---------------------------------------------------------------------------
# import  (raw log ingestion only)
# ---------------------------------------------------------------------------

def cmd_import(args):
    dry_run = args.dry_run

    if args.next:
        month_str = oldest_pending_month()
        if not month_str:
            log.info("Nothing pending in daily/.")
            return
        days = pending_days_for_month(month_str)
        check_order(days[0], args.force)
        log.info(f"{'[dry-run] would import' if dry_run else 'Importing'} {len(days)} day(s) for {month_str}")
        for date_str in days:
            log.info(f"--- {date_str} ---")
            do_import_day(date_str, dry_run)

    elif args.month:
        days = pending_days_for_month(args.month)
        if not days:
            log.info(f"No pending days in daily/ for {args.month}.")
            return
        check_order(days[0], args.force)
        log.info(f"{'[dry-run] would import' if dry_run else 'Importing'} {len(days)} day(s) for {args.month}")
        for date_str in days:
            log.info(f"--- {date_str} ---")
            do_import_day(date_str, dry_run)

    elif args.day:
        date_str = args.day.replace("-", "")
        check_order(date_str, args.force)
        do_import_day(date_str, dry_run)

    else:
        log.error("Specify --next, --month, or --day.")
        raise SystemExit(1)

    log.info(">>> done")


# ---------------------------------------------------------------------------
# analyze  (enrichment and stats only)
# ---------------------------------------------------------------------------

def cmd_analyze(args):
    dry_run = args.dry_run
    if is_current_month(args.month) and not args.force:
        log.error(f"{args.month} is the current month and not yet complete.")
        log.error(f"  Use --force to override.")
        raise SystemExit(1)
    do_analyze(args.month, dry_run)
    do_summarize(args.month, dry_run)
    log.info(">>> done")


# ---------------------------------------------------------------------------
# summarize  (rolling-window aggregation; normally run once after catchup)
# ---------------------------------------------------------------------------

def cmd_summarize(args):
    dry_run = args.dry_run
    if is_current_month(args.month) and not args.force:
        log.error(f"{args.month} is the current month and not yet complete.")
        log.error(f"  Use --force to override.")
        raise SystemExit(1)
    do_summarize(args.month, dry_run)
    log.info(">>> done")


# ---------------------------------------------------------------------------
# process  (import + analyze; the normal command)
# ---------------------------------------------------------------------------

def cmd_process(args):
    dry_run = args.dry_run

    if args.next:
        month_str = oldest_pending_month()
        if not month_str:
            log.info("Nothing pending in daily/.")
            return
        days = pending_days_for_month(month_str)
        log.info(f"{'[dry-run] would process' if dry_run else 'Processing'} {len(days)} day(s) for {month_str}")
    elif args.month:
        month_str = args.month
        days = pending_days_for_month(month_str)
        if days:
            log.info(f"{'[dry-run] would process' if dry_run else 'Processing'} {len(days)} day(s) for {month_str}")
    elif args.day:
        date_str = args.day.replace("-", "")
        month_str = args.day[:7]
        check_order(date_str, args.force)
        do_import_day(date_str, dry_run)
        if is_current_month(month_str) and not args.force:
            log.info(f">>> {month_str} is the current month — skipping analysis until it ends.")
        else:
            do_analyze(month_str, dry_run)
            do_summarize(month_str, dry_run)
        log.info(">>> done")
        return
    else:
        log.error("Specify --next, --month, or --day.")
        raise SystemExit(1)

    check_order(days[0], args.force)
    for date_str in days:
        log.info(f"--- {date_str} ---")
        do_import_day(date_str, dry_run)

    if is_current_month(month_str) and not args.force:
        log.info(f">>> {month_str} is the current month — skipping analysis until it ends.")
        log.info(f"    Run: manage.py analyze --month {month_str}")
    else:
        log.info(f">>> {'[dry-run] would analyze' if dry_run else 'analyzing'} {month_str}")
        do_analyze(month_str, dry_run)
        log.info(f">>> {'[dry-run] would summarize' if dry_run else 'summarizing'} {month_str}")
        do_summarize(month_str, dry_run)

    log.info(">>> done")


# ---------------------------------------------------------------------------
# fill-geo
# ---------------------------------------------------------------------------

ACCESS_CFG = Path("/etc/hubzero-metrics/access.cfg")

def db_config() -> dict[str, str]:
    """Parse every $name = '…'; assignment in the access.cfg PHP file
    into a dict.  Defined variables typically include hub_dir, hub_db,
    metrics_db, db_host, db_user, db_pass, db_prefix.

    The cfg path defaults to /etc/hubzero-metrics/access.cfg but can be
    overridden via the HZMETRICS_ACCESS_CFG environment variable — used
    by the A/B test harness to point at a cfg that names the test DBs.
    """
    cfg_path = Path(os.environ.get("HZMETRICS_ACCESS_CFG", str(ACCESS_CFG)))
    text = cfg_path.read_text()
    return {m.group(1): m.group(2)
            for m in re.finditer(r"\$([\w_]+)\s*=\s*'([^']*)'", text)}

def db_credentials() -> tuple[str, str, str, str]:
    """Returns (db_host, db_user, db_pass, metrics_db) for backwards compat.
    Use db_config() for the full set including hub_db, db_prefix, etc."""
    c = db_config()
    return c.get("db_host", ""), c.get("db_user", ""), c.get("db_pass", ""), c.get("metrics_db", "")

def mysql_query(sql: str, params: tuple | list | dict | None = None) -> list[tuple]:
    """Run a SELECT against the metrics DB; return a list of tuples — one
    tuple per row, with each cell as its native Python type (ints stay
    ints, NULL → None).

    Callers must fully-qualify table names with {metrics_db} — no default
    database is selected on the connection.  When `params` is supplied,
    pymysql treats `%s` in `sql` as placeholders for safe value binding;
    in that case any literal `%` in the SQL must be doubled (`%%`).

    See also mysql_scalar() and mysql_column() for the common
    single-cell / single-column cases.
    """
    conn = _open_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()

def mysql_scalar(sql: str, params: tuple | list | dict | None = None) -> Any | None:
    """Run a SELECT expected to return at most one row of one column;
    return that single cell, or None if the query produced no rows.
    Idiomatic for `SELECT COUNT(*) ...` / `SELECT col FROM ... LIMIT 1`."""
    rows = mysql_query(sql, params)
    if not rows:
        return None
    return rows[0][0]

def mysql_column(sql: str, params: tuple | list | dict | None = None) -> list:
    """Run a SELECT expected to return a single column; flatten to a list
    of native-typed values (no tuple wrapping).  Idiomatic for
    `SELECT id FROM ... ORDER BY id` style queries."""
    return [row[0] for row in mysql_query(sql, params)]

def mysql_exec(sql: str, params: tuple | list | dict | None = None) -> int:
    """Run a DML/DDL statement against the metrics DB.  Returns 0 on
    success, 1 on failure (prints the error).  Single-statement contract.

    Callers must fully-qualify table names with {metrics_db} — no default
    database is selected on the connection.  When `params` is supplied,
    pymysql treats `%s` in `sql` as placeholders for safe value binding."""
    import pymysql
    try:
        conn = _open_db()
    except pymysql.MySQLError as e:
        log.error(f"[mysql] connect failed: {e}")
        return 1
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        return 0
    except pymysql.MySQLError as e:
        log.error(f"[mysql] {e}")
        return 1
    finally:
        conn.close()

def months_with_missing_geo():
    """Query DB for months that have rows with null ipcountry in the web table."""
    _, _, _, metrics_db = db_credentials()
    return mysql_column(
        f"SELECT DISTINCT DATE_FORMAT(datetime,'%Y-%m') FROM {metrics_db}.web "
        f"WHERE ipcountry IS NULL OR ipcountry = '' ORDER BY 1;"
    )

def do_fill_geo(month_str, dry_run=False):
    for db, table in IPCOUNTRY_TABLES:
        do_fill_ipcountry(db, table, month_str, dry_run=dry_run)

def cmd_fill_geo(args):
    dry_run = args.dry_run

    if args.all:
        months = months_with_missing_geo()
        if not months:
            log.info("No months with missing GeoIP data found.")
            return
        log.info(f"{'[dry-run] would fill' if dry_run else 'Filling'} GeoIP for {len(months)} month(s): {months[0]} .. {months[-1]}")
    else:
        months = [args.month]
        log.info(f"{'[dry-run] would fill' if dry_run else 'Filling'} GeoIP for {args.month}")

    for month_str in months:
        log.info(f"--- {month_str} ---")
        do_fill_geo(month_str, dry_run)

    log.info(">>> done")


# ---------------------------------------------------------------------------
# backfill-dnload  (populate web.dnload for historical rows)
# ---------------------------------------------------------------------------

def do_backfill_dnload(start_month, dry_run=False):
    _, _, _, metrics_db = db_credentials()

    ext_pattern = "|".join(re.escape(e) for e in DOWNLOAD_EXTS)

    where = "dnload IS NULL"
    if start_month:
        where += f" AND datetime >= '{start_month}-01'"

    months = mysql_column(
        f"SELECT DISTINCT DATE_FORMAT(datetime,'%Y-%m') FROM {metrics_db}.web "
        f"WHERE {where} ORDER BY 1;"
    )
    if not months:
        log.info("  No months with unprocessed rows found.")
        return

    log.info(f"  Will backfill {len(months)} month(s): {months[0]} .. {months[-1]}")

    for month in months:
        m = datetime.strptime(month + "-01", "%Y-%m-%d")
        next_m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        m_start = m.strftime("%Y-%m-%d")
        m_end   = next_m.strftime("%Y-%m-%d")

        label = f"  {month}"
        log.debug(f"backfill-dnload {month}")

        sql = (
            f"UPDATE {metrics_db}.web "
            f"SET dnload = IF("
            f"content LIKE '/resources/%/download/%' OR "
            f"content REGEXP '^/resources/.*\\.({ext_pattern})([?#]|$)', "
            f"1, 0) "
            f"WHERE datetime >= '{m_start}' AND datetime < '{m_end}' AND dnload IS NULL;"
        )

        if dry_run:
            log.info(f"{label}  [dry-run]")
        else:
            rc = mysql_exec(sql)
            log.info(f"{label} done (rc={rc})")

    if not dry_run:
        count = mysql_scalar(
            f"SELECT COUNT(*) FROM information_schema.statistics "
            f"WHERE table_schema='{metrics_db}' AND table_name='web' AND index_name='dnload';"
        )
        if count == 0:
            rc = mysql_exec(f"ALTER TABLE {metrics_db}.web ADD INDEX dnload (dnload);")
            log.info(f"  Adding index on {metrics_db}.web(dnload) ... done (rc={rc})")
        else:
            log.info(f"  Index on {metrics_db}.web(dnload) already exists.")


def cmd_backfill_dnload(args):
    dry_run = args.dry_run
    do_backfill_dnload(args.start, dry_run)
    log.info(">>> done")


# ---------------------------------------------------------------------------
# whoisonline  (real-time session geo map; normally called every 5 min;
#               ports xlogfix_whoisonline.php)
# ---------------------------------------------------------------------------

WHOISONLINE_IDLE_TIME = 3600    # seconds — matches PHP (the in-code comment
                                # says "30 mins" but the actual value is 60).

# Hardcoded force-list of bot/crawler domains, matched as host-suffix.
# Verbatim from xlogfix_whoisonline.php's get_domain() — same order, same
# values (the PHP author seemed to intend this as "domains that must
# always collapse to this token regardless of subdomain depth").
_WHOISONLINE_FORCE_DOMAINS = [
    "brain.grub.org", "crawl.yahoo.net", "crawl8-public.alexa.com",
    "hanta.yahoo.com", "idle.eidetica.com", "morgue1.corp.yahoo.com",
    "msnbot.msn.com", "panchma.tivra.com", "tpiol.tpiol.com",
    "xs4.kso.co.uk", "zeus.nj.nec.com", "punch.purdue.edu",
    "san2.attens.net", "search.msn.com", "sac.overture.com",
    "66.237.109.194.ptr.us.xo.net",
    "67.108.223.130.ptr.us.xo.net",
    "67.106.152.131.ptr.us.xo.net",
]

def _whoisonline_get_domain(ip, host):
    """Variant of get_domain() used by whoisonline.  Differs from
    xlogfix_domain.php's get_domain in three ways: returns '(unknown)'
    instead of '?', treats ip==host (resolver returned no PTR / echoed
    the IP) as '(unknown)', and checks the FORCE-list of bot domains
    as a host-suffix match before the standard TLD-promotion logic."""
    if not host or ip == host:
        return '(unknown)'
    for forced in _WHOISONLINE_FORCE_DOMAINS:
        if host.endswith(forced):
            return forced
    result = get_domain(host)
    return '(unknown)' if (not result or result == '?') else result

def _whoisonline_checkforbot(conn, metrics_db, domain):
    """Is `domain` listed in metrics.exclude_list with type='domain'?
    Returns 1 or 0 (PHP int convention)."""
    if not domain:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM {metrics_db}.exclude_list "
            f"WHERE filter = %s AND type = 'domain'",
            (domain,))
        return 1 if (cur.fetchone()[0] or 0) > 0 else 0

def _whoisonline_get_count(conn, hub_db, db_prefix, domain, lat, lng):
    """Build the per-(domain, location) info-segment string for the XML.
    Mirrors get_count() in the PHP — same three COUNT queries
    (users/guests/bots), same '_br_' separator codes."""
    info = ''
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(DISTINCT username) FROM {hub_db}.{db_prefix}session_geo "
            f"WHERE guest = 0 AND domain = %s "
            f"AND ipLATITUDE = %s AND ipLONGITUDE = %s LIMIT 1",
            (domain, lat, lng))
        users = cur.fetchone()[0] or 0
        if users:
            info += f"_br_ - Users: {users}"

        cur.execute(
            f"SELECT COUNT(DISTINCT ip) FROM {hub_db}.{db_prefix}session_geo "
            f"WHERE guest = 1 AND domain = %s AND bot = 0 "
            f"AND ipLATITUDE = %s AND ipLONGITUDE = %s LIMIT 1",
            (domain, lat, lng))
        guests = cur.fetchone()[0] or 0
        if guests:
            info += f"_br_ - Guests: {guests}"

        cur.execute(
            f"SELECT COUNT(DISTINCT ip) FROM {hub_db}.{db_prefix}session_geo "
            f"WHERE guest = 1 AND domain = %s AND bot = 1 "
            f"AND ipLATITUDE = %s AND ipLONGITUDE = %s LIMIT 1",
            (domain, lat, lng))
        bots = cur.fetchone()[0] or 0
        if bots:
            info += f"_br_ - Bots: {bots}"
    return info + "_br_" if info else "_br_"

def _whoisonline_get_hosts(conn, hub_db, db_prefix, lat, lng):
    """Build the per-location info string: one segment per domain at
    that lat/lng, each with user/guest/bot counts.  Mirrors get_hosts()."""
    info = ''
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT(domain) FROM {hub_db}.{db_prefix}session_geo "
            f"WHERE ipLATITUDE = %s AND ipLONGITUDE = %s",
            (lat, lng))
        domains = [r[0] for r in cur.fetchall()]
    for d in domains:
        info += "_b_" + (str(d) if d is not None else "")
        info += "_bb_" + _whoisonline_get_count(conn, hub_db, db_prefix, d, lat, lng)
    # PHP does rtrim($info, '_br_').  rtrim with a string arg in PHP
    # strips any of the characters in the set — buggy here, but the
    # effect is "strip trailing _br_-like characters", which we
    # approximate as "strip a trailing _br_ if present".
    while info.endswith('_br_'):
        info = info[:-4]
    return info

def do_whoisonline(*, dry_run=False):
    """Refresh hub.jos_session_geo from jos_session, resolve DNS / domain /
    GeoIP for new IPs, and rewrite the whoisonline.xml file consumed by
    the hub's Google Maps widget.  Direct port of xlogfix_whoisonline.php.
    """
    cfg = db_config()
    hub_db    = cfg.get('hub_db', '')
    db_prefix = cfg.get('db_prefix', 'jos_')
    hub_dir   = cfg.get('hub_dir', '')
    metrics_db = cfg.get('metrics_db', '')
    if not hub_db or not hub_dir:
        msg = "[whoisonline] missing hub_db / hub_dir in access.cfg"
        log.info(msg)
        return 2

    map_dir = Path(hub_dir) / "app/site/stats/maps"
    if not map_dir.is_dir():
        map_dir = Path(hub_dir) / "site/stats/maps"
    xml_file = map_dir / "whoisonline.xml"
    if not map_dir.is_dir():
        msg = f"[whoisonline] map dir missing: {map_dir}"
        log.info(msg)
        return 2

    if dry_run:
        log.info(f"[whoisonline] dry-run: would update {hub_db}.{db_prefix}session_geo "
            f"and write {xml_file}")
        return 0

    conn = _open_db()
    try:
        # 1. clear stale sessions
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {hub_db}.{db_prefix}session_geo "
                f"WHERE (UNIX_TIMESTAMP() - time) > %s",
                (WHOISONLINE_IDLE_TIME,))

        # 2. populate from jos_session
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT IGNORE INTO {hub_db}.{db_prefix}session_geo "
                f"(ip, session_id, username, time, guest, userid) "
                f"SELECT ip, session_id, username, time, guest, userid "
                f"FROM {hub_db}.{db_prefix}session "
                f"WHERE (UNIX_TIMESTAMP() - time) < %s "
                f"GROUP BY ip, username",
                (WHOISONLINE_IDLE_TIME,))

        # 3. propagate known (host, domain) for IPs already resolved
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT ip, host, domain FROM {hub_db}.{db_prefix}session_geo "
                f"WHERE host <> '' AND host <> '(unknown)'")
            for ip, host, domain in cur.fetchall():
                cur.execute(
                    f"UPDATE {hub_db}.{db_prefix}session_geo "
                    f"SET host = %s, domain = %s WHERE ip = %s",
                    (host, domain, ip))

        # 4. DNS lookup for unresolved IPs (batched via aiodns rather than
        #    the PHP's per-IP fork/exec to `host`).
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT ip FROM {hub_db}.{db_prefix}session_geo "
                f"WHERE (host = '' OR host IS NULL) AND ip <> ''")
            unresolved = [r[0] for r in cur.fetchall()]

        if unresolved:
            log.info(f"[whoisonline] resolving {len(unresolved)} IP(s) via aiodns")
            import asyncio
            try:
                pairs = asyncio.run(_resolve_ips_async(
                    unresolved,
                    DNS_NAMESERVER,
                    min(len(unresolved), DNS_CONCURRENCY),
                    DNS_TIMEOUT))
            except ImportError as e:
                msg = (f"[whoisonline] aiodns unavailable ({e}); skipping DNS step")
                log.info(msg)
                pairs = [(ip, ip) for ip in unresolved]
            with conn.cursor() as cur:
                for ip, host in pairs:
                    host = host if host and host != '?' else ip
                    domain = _whoisonline_get_domain(ip, host)
                    cur.execute(
                        f"UPDATE {hub_db}.{db_prefix}session_geo "
                        f"SET host = %s, domain = %s WHERE ip = %s",
                        (host, domain, ip))

        # 5. GeoIP for sessions without ipLATITUDE
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT(ip), domain, bot "
                f"FROM {hub_db}.{db_prefix}session_geo "
                f"WHERE ipLATITUDE IS NULL")
            geo_targets = list(cur.fetchall())

        if geo_targets:
            log.info(f"[whoisonline] geo lookups for {len(geo_targets)} IP(s)")
        for n_ip, domain, bot in geo_targets:
            if not bot:
                bot = _whoisonline_checkforbot(conn, metrics_db, domain)
            data = _get_ip_geodata(conn, n_ip)
            if not data:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {hub_db}.{db_prefix}session_geo "
                    f"SET countrySHORT=%s, countryLONG=%s, ipREGION=%s, "
                    f"ipCITY=%s, ipLATITUDE=%s, ipLONGITUDE=%s, bot=%s "
                    f"WHERE ip = %s",
                    (data['countrySHORT'], data['countryLONG'],
                     data['ipREGION'], data['ipCITY'],
                     data['ipLATITUDE'], data['ipLONGITUDE'],
                     bot, n_ip))

        # 6. Write the XML for Google Maps consumption
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT ipLATITUDE, ipLONGITUDE, ipCITY, ipREGION, countrySHORT "
                f"FROM {hub_db}.{db_prefix}session_geo "
                f"WHERE ipLATITUDE <> '' "
                f"GROUP BY ipLATITUDE, ipLONGITUDE")
            locations = list(cur.fetchall())

        xml_lines = ["<markers>"]
        for lat, lng, city, region, country in locations:
            city_str = f"_b_{city}, {region}, {country}_bb_"
            info = _whoisonline_get_hosts(conn, hub_db, db_prefix, lat, lng)
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT bot FROM {hub_db}.{db_prefix}session_geo "
                    f"WHERE ipLATITUDE = %s AND ipLONGITUDE = %s "
                    f"ORDER BY bot DESC LIMIT 1",
                    (lat, lng))
                row = cur.fetchone()
                bot = (row[0] if row else 0) or 0
            xml_lines.append(
                f'<marker lat="{lat}" lng="{lng}" '
                f'info = "{city_str}_hr_{info}" bot = "{bot}"/>'
            )
        xml_lines.append("</markers>")
        xml_file.write_text("\n".join(xml_lines) + "\n")
        log.info(f"[whoisonline] wrote {xml_file} ({len(locations)} marker(s))")

        # 7. Final stale-session cleanup (PHP runs clear_stale_sessions twice)
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {hub_db}.{db_prefix}session_geo "
                f"WHERE (UNIX_TIMESTAMP() - time) > %s",
                (WHOISONLINE_IDLE_TIME,))

        return 0
    finally:
        conn.close()


def cmd_whoisonline(args):
    return do_whoisonline(dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# tick  (every-5-min cron entry: always updates whoisonline, starts a full
#        metrics run when near the half-hour boundary)
# ---------------------------------------------------------------------------

def cmd_tick(args):
    # Capture the minute now so we can decide on the metrics run
    # before whoisonline consumes any time.
    at_metrics_tick = (datetime.now().minute == 30)

    # Always update the who-is-online map — fast, no lock needed.
    do_whoisonline(dry_run=args.dry_run)

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
            log.info(f"[warn] {HZMETRICS_CONF}: {e}; using defaults")

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
    """Convenience alias for db_config()['hub_db']."""
    return db_config().get("hub_db", "")

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
                   timeout=DNS_TIMEOUT, dry_run=False):
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
        log.info(msg)
        return 1

    _, _, _, metrics_db = db_credentials()
    if db_key == "metrics":
        db_name = metrics_db
    elif db_key == "hub":
        db_name = hub_db_name()
    else:
        msg = f"[resolve-dns] unknown db_key {db_key!r}; expected 'metrics' or 'hub'"
        log.info(msg)
        return 2
    if not db_name:
        msg = f"[resolve-dns] could not resolve DB name for db_key={db_key!r} from access.cfg"
        log.info(msg)
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
                log.info(msg)
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

    # Use one pymysql connection for the whole flow: select, async resolve
    # (network-bound, releases the connection), then temp-table insert +
    # update join.  Temp table is per-connection so it has to be one conn.
    conn = _open_db(db_name)
    try:
        with conn.cursor() as cur:
            cur.execute(sel_sql)
            ips = [r[0] for r in cur.fetchall()]

            log.info(f"[resolve-dns] {db_name}.{table} {scope_label}: "
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
            log.info(f"[resolve-dns] resolved={resolved_count} no_ptr={no_ptr} "
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
            log.info(f"[resolve-dns] applied: {updated} rows updated in {table}")
            return 0
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# clean-bots  (DELETE rows in web/websessions matching exclude_list bot
#              patterns; ports xlogfix_clean.php)
# ---------------------------------------------------------------------------

CLEAN_BOTS_TABLES = ("web", "websessions")

def _findweeks(start_d, end_d):
    """Yield (chunk_start, chunk_end) date pairs covering [start_d, end_d)
    in ~7-day windows, with the PHP findWeeks() boundary convention
    preserved: each chunk's SQL semantics are `col > start AND col <= end`,
    and the very first chunk starts the day BEFORE start_d so that the
    first day of the period is captured (since `> start` is exclusive).
    """
    if start_d is None or end_d is None:
        raise ValueError("clean-bots requires a bounded date range "
                         "(use --all only with explicit bounds upstream)")
    chunk_start = start_d - timedelta(days=1)
    while chunk_start < end_d:
        chunk_end = chunk_start + timedelta(days=7)
        if chunk_end > end_d:
            chunk_end = end_d
        yield chunk_start, chunk_end
        chunk_start = chunk_end

def do_clean_bots(table, date_spec=None, *, all_dates=False,
                  dry_run=False):
    """DELETE rows in <table> whose domain or host matches an entry in
    foo_metrics.exclude_list (types 'domain' and 'host').  Faithful
    port of xlogfix_clean.php — same SQL shape, same week-chunked DELETEs,
    same boundary semantics, same source list (exclude_list, not exclude_list2).
    """
    if table not in CLEAN_BOTS_TABLES:
        msg = f"[clean-bots] table {table!r} not supported (expected one of {CLEAN_BOTS_TABLES})"
        log.info(msg)
        return 2

    _, _, _, metrics_db = db_credentials()

    if all_dates:
        msg = "[clean-bots] --all not supported (DELETE needs a bounded range)"
        log.info(msg)
        return 2

    if date_spec:
        try:
            start_d, end_d = parse_date_range(date_spec)
        except ValueError as e:
            msg = f"[clean-bots] {e}"
            log.info(msg)
            return 2
        if start_d is None or end_d is None:
            msg = "[clean-bots] open-ended ranges not supported"
            log.info(msg)
            return 2
    else:
        # default: current month-to-today (mirrors PHP behavior when no
        # argument is passed)
        today = date.today()
        start_d = today.replace(day=1)
        end_d   = today + timedelta(days=1)

    conn = _open_db(metrics_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT filter FROM exclude_list WHERE type='domain'")
            domain_filters = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT DISTINCT filter FROM exclude_list WHERE type='host'")
            host_filters = [r[0] for r in cur.fetchall()]

            chunks = list(_findweeks(start_d, end_d))
            log.info(f"[clean-bots] {metrics_db}.{table} {start_d}..{end_d}: "
                f"{len(domain_filters)} domain filter(s), {len(host_filters)} host filter(s), "
                f"{len(chunks)} week chunk(s)")

            if dry_run:
                for c_start, c_end in chunks:
                    log.info(f"  [dry-run] chunk {c_start} < datetime <= {c_end}")
                for f in domain_filters:
                    log.info(f"  [dry-run] domain = {f!r}")
                for f in host_filters:
                    log.info(f"  [dry-run] host LIKE {f!r}")
                return 0

            total_deleted = 0
            for c_start, c_end in chunks:
                for f in domain_filters:
                    cur.execute(
                        f"DELETE FROM {table} "
                        f"WHERE datetime > %s AND datetime <= %s AND domain = %s",
                        (c_start, c_end, f))
                    total_deleted += cur.rowcount
                for f in host_filters:
                    cur.execute(
                        f"DELETE FROM {table} "
                        f"WHERE datetime > %s AND datetime <= %s AND host LIKE %s",
                        (c_start, c_end, f))
                    total_deleted += cur.rowcount

            log.info(f"[clean-bots] deleted {total_deleted} rows from {table}")
            return 0
    finally:
        conn.close()

def cmd_clean_bots(args):
    return do_clean_bots(
        args.table, args.date_spec,
        all_dates=args.all,
        dry_run=args.dry_run,
    )


# ---------------------------------------------------------------------------
# import-hub-data  (refresh sessionlog_metrics and jos_xprofiles_metrics
#                   from the hub DB; ports xlogimport_tool_and_reg_user_data.php)
# ---------------------------------------------------------------------------

def do_import_hub_data(*, dry_run=False):
    """Copy tool session starts and registered-user profiles from the
    hub DB into the metrics DB.  Faithful port of
    xlogimport_tool_and_reg_user_data.php:

    1. INSERT IGNORE INTO {metrics_db}.sessionlog_metrics
       SELECT FROM {hub_db}.sessionlog  -- idempotent on sessnum PK
    2. DROP+CREATE LIKE+INSERT {metrics_db}.{prefix}xprofiles_metrics
       from {hub_db}.{prefix}xprofiles WHERE emailConfirmed > 0
       (rebuilt from scratch every run — see CLAUDE.md for rationale).
    """
    cfg = db_config()
    metrics_db = cfg.get("metrics_db", "")
    hub_db     = cfg.get("hub_db", "")
    db_prefix  = cfg.get("db_prefix", "jos_")
    if not metrics_db or not hub_db:
        msg = f"[import-hub-data] missing metrics_db / hub_db in access.cfg"
        log.info(msg)
        return 2

    stmts = [
        (
            f"INSERT IGNORE INTO {metrics_db}.sessionlog_metrics "
            f"(sessnum, user, ip, start, appname) "
            f"SELECT sessnum, username, remoteip, start, appname "
            f"FROM {hub_db}.sessionlog",
            "copy tool session starts → sessionlog_metrics",
        ),
        (
            f"DROP TABLE IF EXISTS {metrics_db}.{db_prefix}xprofiles_metrics",
            f"drop old {db_prefix}xprofiles_metrics",
        ),
        (
            f"CREATE TABLE {metrics_db}.{db_prefix}xprofiles_metrics "
            f"LIKE {hub_db}.{db_prefix}xprofiles",
            f"recreate {db_prefix}xprofiles_metrics schema from hub",
        ),
        (
            f"INSERT INTO {metrics_db}.{db_prefix}xprofiles_metrics "
            f"SELECT * FROM {hub_db}.{db_prefix}xprofiles "
            f"WHERE emailConfirmed > 0",
            "copy confirmed user profiles",
        ),
    ]

    if dry_run:
        for sql, desc in stmts:
            log.info(f"  [dry-run] {desc}")
            log.info(f"            {sql.split(' SELECT ')[0]} ...")
        return 0

    conn = _open_db()
    try:
        with conn.cursor() as cur:
            for sql, desc in stmts:
                log.info(f"[import-hub-data] {desc}")
                cur.execute(sql)
                log.info(f"                  rows affected: {cur.rowcount}")
        return 0
    finally:
        conn.close()

def cmd_import_hub_data(args):
    return do_import_hub_data(dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# import-auth  (parse cmsauth.log → userlogin; ports xlogimport_authlog.php)
# ---------------------------------------------------------------------------

# Old format: 2007-05-17 11:06:39 username 128.210.189.195 login
# New format: 2009-01-17 11:06:39 1234 [username] 128.210.189.195 login
_AUTH_PAT_NEW = re.compile(
    r'^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+(\d+)\s+(\[.+\])\s+([\.\d]+)\s+(\w+)\s*$'
)
_AUTH_PAT_OLD = re.compile(
    r'^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+(.+)\s+([\.\d]+)\s+(\w+)\s*$'
)

def _ip_excluded(ip, filters):
    """Mirror search_array() from func_misc.php: case-insensitive substring
    test of each filter against the candidate IP.  Filter list is small
    (typically 1-3 entries), so straightforward loop is fine."""
    lo = ip.lower()
    for f in filters:
        if f and f.lower() in lo:
            return True
    return False

def do_import_auth(input_file, *, batch_size=5000, dry_run=False):
    """Parse a cmsauth-format file and INSERT IGNORE login/simulation
    events into metrics.userlogin.  Faithful port of xlogimport_authlog.php
    against the source-tree version (which filters action ∈ {login,
    simulation}; master inserted every action).

    input_file: path to the staged auth log (typically
    /var/log/hubzero/metrics/_hub_auth.log) or '-' for stdin.
    """
    cfg = db_config()
    metrics_db = cfg.get("metrics_db", "")
    if not metrics_db:
        msg = "[import-auth] missing metrics_db in access.cfg"
        log.info(msg)
        return 2

    # Pull the IP exclusion list once
    conn = _open_db(metrics_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT filter FROM exclude_list WHERE type='ip'")
            ip_filters = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    rows = []
    unrec = 0
    skipped_action = 0
    skipped_filter = 0
    total = 0

    with _open_input(input_file) as src:
        for line in src:
            total += 1
            line = line.rstrip("\r\n")
            m = _AUTH_PAT_NEW.match(line)
            if m:
                dt = f"{m.group(1)} {m.group(2)}"
                uid = int(m.group(3))
                # Legacy: ltrim($x, '[') + rtrim($x, ']') — strips ALL
                # leading '[' and ALL trailing ']' (charlist semantics).
                # Python's lstrip / rstrip with a charset behave identically.
                user = m.group(4).strip().lstrip('[').rstrip(']')
                ip = m.group(5).strip()
                action = m.group(6).strip()
            else:
                m = _AUTH_PAT_OLD.match(line)
                if not m:
                    unrec += 1
                    continue
                dt = f"{m.group(1)} {m.group(2)}"
                uid = 0
                user = m.group(3).strip()
                ip = m.group(4).strip()
                action = m.group(5).strip()
            if not user:
                user = "-"
            # Legacy 1018cc2^ inserts every action type — no insert-time filter
            # to (login, simulation) here.  Action narrowing happens later via
            # migration #4 which purges non-(login,simulation) rows from
            # userlogin.  Keep insert-time behaviour byte-identical to pre-port.
            #
            # To restore the post-1018cc2 in-line filter (saves disk + later
            # purge work) re-enable the next 3 lines:
            #     if action not in ("login", "simulation"):
            #         skipped_action += 1
            #         continue
            if user in ("hubstatus", "hubadmin"):
                skipped_filter += 1
                continue
            if _ip_excluded(ip, ip_filters):
                skipped_filter += 1
                continue
            rows.append((dt, uid, user, ip, action))

    log.info(f"[import-auth] parsed {total} line(s); "
        f"login/simulation kept = {len(rows)}; "
        f"unrecognized = {unrec}; "
        f"filtered = {skipped_filter}; "
        f"other-action skipped = {skipped_action}")

    if dry_run or not rows:
        return 0

    inserted = 0
    conn = _open_db(metrics_db)
    try:
        with conn.cursor() as cur:
            sql = ("INSERT IGNORE INTO userlogin "
                   "(datetime, uidNumber, user, ip, action) "
                   "VALUES (%s, %s, %s, %s, %s)")
            for i in range(0, len(rows), batch_size):
                cur.executemany(sql, rows[i:i + batch_size])
                inserted += cur.rowcount
    finally:
        conn.close()

    log.info(f"[import-auth] inserted {inserted} new row(s) into userlogin "
        f"(others were duplicates rejected by INSERT IGNORE)")
    return 0

def cmd_import_auth(args):
    return do_import_auth(args.input_file, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# fill-user-info  (assign countrycitizen, countryresident, orgtype on
#                  toolstart / sessionlog_metrics rows by joining to hub
#                  user profiles; ports xlogfix_user_info.php)
# ---------------------------------------------------------------------------

# (column-in-target-table, profile_key-in-hub-user_profiles)
USER_INFO_PARAMS = [
    ("countrycitizen",  "countryorigin"),
    ("countryresident", "countryresident"),
    ("orgtype",         "orgtype"),
]

def do_fill_user_info(db_key, table, date_spec=None, *, all_dates=False,
                      dry_run=False):
    """Fill countrycitizen / countryresident / orgtype columns on rows
    in <table> by joining usernames against hub.jos_users +
    hub.jos_user_profiles.  Ports xlogfix_user_info.php.

    Optimization vs the PHP: original looped over week chunks and emitted
    one UPDATE per matching profile row.  The UPDATE's WHERE clause has no
    date filter (only user filter and column-is-empty), so the per-week
    loop was redundant.  We do one UPDATE per parameter via INNER JOIN,
    which produces an identical end state in 3 statements instead of N
    week-chunks × N profiles × 3 params.

    The CLI accepts a date_spec for compatibility with the old invocation
    pattern (__process_*.sh passes the month), but the value is ignored
    to match PHP semantics exactly — the UPDATE always considers all
    unfilled rows regardless of date.
    """
    cfg = db_config()
    metrics_db = cfg.get("metrics_db", "")
    hub_db     = cfg.get("hub_db", "")
    db_prefix  = cfg.get("db_prefix", "jos_")
    if not metrics_db or not hub_db:
        msg = "[fill-user-info] missing metrics_db / hub_db in access.cfg"
        log.info(msg)
        return 2

    if db_key == "metrics":
        db_name = metrics_db
    elif db_key == "hub":
        db_name = hub_db
    else:
        msg = f"[fill-user-info] unknown db_key {db_key!r}"
        log.info(msg)
        return 2

    if date_spec or all_dates:
        log.info(f"[fill-user-info] {db_name}.{table}: date arg ignored (matching "
            f"the PHP behaviour — UPDATE has no date filter, only user filter)")

    conn = _open_db()
    try:
        with conn.cursor() as cur:
            grand_total = 0
            for column, profile_key in USER_INFO_PARAMS:
                update_sql = (
                    f"UPDATE {db_name}.{table} t "
                    f"INNER JOIN {hub_db}.{db_prefix}users u "
                    f"  ON u.username = t.user "
                    f"INNER JOIN {hub_db}.{db_prefix}user_profiles up "
                    f"  ON up.user_id = u.id AND up.profile_key = %s "
                    f"SET t.{column} = UPPER(up.profile_value) "
                    f"WHERE (t.{column} IS NULL OR t.{column} = '') "
                    f"AND up.profile_value IS NOT NULL "
                    f"AND up.profile_value <> ''"
                )
                if dry_run:
                    log.info(f"  [dry-run] {column} <- profile_key={profile_key!r}")
                    continue
                cur.execute(update_sql, (profile_key,))
                log.info(f"[fill-user-info] {column}: {cur.rowcount} row(s) updated")
                grand_total += cur.rowcount
            if not dry_run:
                log.info(f"[fill-user-info] {db_name}.{table}: {grand_total} total updates")
        return 0
    finally:
        conn.close()

def cmd_fill_user_info(args):
    return do_fill_user_info(args.db_key, args.table, args.date_spec,
                             all_dates=args.all,
                             dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# identify-bots  (scan apache log for bot UAs, populate metrics.bot_useragents;
#                 ports xlogfix_identify_bots.php)
# ---------------------------------------------------------------------------

# Apache log patterns — same shape used in xlogimport_apache.php on the source
# branch.  Two formats are recognized: the older 14-field combined-ish format,
# and the newer 23-field format used on the reference host with PID and joomla fields.
_APACHE_PAT_NEW = re.compile(
    r'^(\d{4}-\d{2}-\d{2})\s+(\d+:\d{2}:\d{2})\s+([\w\-\d]+)\s+([\d]+)\s+(\S+)\s+'
    r'\"(.+)\"\s+([\-\d]+)\s+([\d]+)\s+([\w\-\.\d]+)\s+\"(.*)\"\s+\"(.*)\"\s+'
    r'([\w\-\.\d]+)\s+([\w\-\d]+)\s+([\w\-\d]+)\s+([\-\d]+)\s+'
    r'([^_].*)\s+([^_].*)\s+([^_].*)\s+([^_].*)\s+([^_].*)\s+([^_].*)\s+([^_].*)\s+([^_].*)\s*$'
)
_APACHE_PAT_OLD = re.compile(
    r'^(\d{4}-\d{2}-\d{2})\s+(\d+:\d{2}:\d{2})\s+([\w\-\d]+)\s+(\S+)\s+'
    r'\"(.+)\"\s+([\-\d]+)\s+([\d]+)\s+([\w\-\.\d]+)\s+\"(.*)\"\s+\"(.*)\"\s+'
    r'([\w\-\.\d]+)\s+([\w\-\d]+)\s+([\w\-\d]+)\s+(.*)$'
)

# group index of the user-agent capture in each pattern (1-based as PHP)
_APACHE_UA_GROUP_NEW = 11
_APACHE_UA_GROUP_OLD = 10

# Substring filters from xlogfix_identify_bots.php — case-insensitive match
# against the user-agent string.  Any UA containing one of these gets flagged.
BOT_UA_FILTERS = [
    "owler", "serpstatbot", "turnitin", "facebookexternalhit", "googleother",
    "feedfetcher", "msnbot", "gsa-crawler", "googlebot", "yandex",
    "spider", "bot", "search", "crawl", "archive", "harvest", "slurp",
    "feed", "nutch", "robot", "fetch", "findlinks",
]
# Whitelist overrides — remove these false positives after flagging.
BOT_UA_WHITELIST_LIKE = ["%searchtool%", "% feed/%"]

def _ua_is_bot(ua):
    lo = ua.lower()
    for f in BOT_UA_FILTERS:
        if f in lo:
            return True
    return False

def do_identify_bots(input_file, *, dry_run=False):
    """Scan an apache-format staged log file, collect unique user-agent
    strings that match any of the bot substring filters, and INSERT IGNORE
    them into metrics.bot_useragents.  Then DELETE two whitelist
    overrides (searchtool, ' feed/') that the substring filter would
    have incorrectly flagged.  Faithful port of xlogfix_identify_bots.php.
    """
    cfg = db_config()
    metrics_db = cfg.get("metrics_db", "")
    if not metrics_db:
        msg = "[identify-bots] missing metrics_db in access.cfg"
        log.info(msg)
        return 2

    unique_uas = set()
    total = 0
    unrec = 0

    with _open_input(input_file) as src:
        for line in src:
            total += 1
            line = line.rstrip("\r\n")
            m = _APACHE_PAT_NEW.match(line)
            if m:
                ua = m.group(_APACHE_UA_GROUP_NEW)
            else:
                m = _APACHE_PAT_OLD.match(line)
                if not m:
                    unrec += 1
                    continue
                ua = m.group(_APACHE_UA_GROUP_OLD)
            if ua:
                unique_uas.add(ua)

    matched = [ua for ua in unique_uas if _ua_is_bot(ua)]

    log.info(f"[identify-bots] parsed {total} line(s); "
        f"unique UAs = {len(unique_uas)}; "
        f"flagged as bot = {len(matched)}; "
        f"unrecognized = {unrec}")

    if dry_run or not matched:
        if dry_run and matched:
            for ua in matched[:5]:
                log.info(f"  [dry-run] would insert: {ua[:120]}")
            if len(matched) > 5:
                log.info(f"  [dry-run] ... and {len(matched) - 5} more")
        return 0

    conn = _open_db(metrics_db)
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT IGNORE INTO bot_useragents (useragent) VALUES (%s)",
                [(ua,) for ua in matched],
            )
            inserted = cur.rowcount
            # Whitelist overrides — match the PHP `OR useragent LIKE …` shape
            cur.execute(
                "DELETE FROM bot_useragents "
                "WHERE useragent LIKE %s OR useragent LIKE %s",
                BOT_UA_WHITELIST_LIKE,
            )
            removed = cur.rowcount
    finally:
        conn.close()

    log.info(f"[identify-bots] inserted {inserted} new bot UA(s); "
        f"removed {removed} whitelist override(s)")
    return 0

def cmd_identify_bots(args):
    return do_identify_bots(args.input_file, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# import-webhits  (aggregate per-day hit counts from apache log into
#                  metrics.webhits; ports xlogimport_webhits.php)
# ---------------------------------------------------------------------------

_SLASH_COLLAPSE = re.compile(r'/+')

def _search_array(needle, filters):
    """Mirror search_array() from func_misc.php: case-insensitive substring
    test — returns True if any filter is a substring of `needle`.  Used
    for IP, user-agent, URL, and host exclusion checks where the filter
    list comes from metrics.exclude_list."""
    if not needle:
        return False
    lo = needle.lower()
    for f in filters:
        if f and f.lower() in lo:
            return True
    return False

# Backwards-compatible alias for code that already called _ip_excluded.
_ip_excluded = _search_array

def do_import_webhits(input_file, *, dry_run=False):
    """Aggregate per-day hit counts from an apache staged log and
    INSERT one row per day into metrics.webhits.  Faithful port of
    xlogimport_webhits.php.

    Counted rows: status=200, bytes>0, method ∈ {GET,POST}, IP/UA/URL
    not matched by their respective exclude_list filters (substring,
    case-insensitive).  URL is normalised by collapsing repeated '/'.

    Note: webhits has no unique key; original PHP uses plain INSERT
    (not INSERT IGNORE), so re-running this on overlapping content adds
    duplicate (datetime, hits) rows.  This port preserves that semantic.
    """
    cfg = db_config()
    metrics_db = cfg.get("metrics_db", "")
    if not metrics_db:
        msg = "[import-webhits] missing metrics_db in access.cfg"
        log.info(msg)
        return 2

    # Pull filter lists once
    conn = _open_db(metrics_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT filter FROM exclude_list WHERE type='ip'")
            ip_filters = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT filter FROM exclude_list WHERE type='useragent'")
            ua_filters = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT filter FROM exclude_list WHERE type='url'")
            url_filters = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    daily_hits = defaultdict(int)
    total = 0
    unrec = 0

    with _open_input(input_file) as src:
        for line in src:
            total += 1
            line = line.rstrip("\r\n")
            m = _APACHE_PAT_NEW.match(line)
            if m:
                datestamp = m.group(1)
                firstline = m.group(6)
                return_code = m.group(7)
                bytes_str = m.group(8)
                ip = m.group(9)
                useragent = m.group(11)
            else:
                m = _APACHE_PAT_OLD.match(line)
                if not m:
                    unrec += 1
                    continue
                datestamp = m.group(1)
                firstline = m.group(5)
                return_code = m.group(6)
                bytes_str = m.group(7)
                ip = m.group(8)
                useragent = m.group(10)

            # Parse method/url/protocol — PHP edge case: when only one token in
            # the request line, treat it as URL with default GET method.
            parts = firstline.strip().split(None, 2)
            method = parts[0] if parts else ''
            url = parts[1] if len(parts) > 1 else ''
            if not url:
                url = method
                method = 'GET'
            url = _SLASH_COLLAPSE.sub('/', url)

            # Filter chain
            if return_code != "200":
                continue
            try:
                if int(bytes_str) <= 0:
                    continue
            except ValueError:
                continue
            if method != "GET" and method != "POST":
                continue
            if _search_array(ip, ip_filters):
                continue
            if _search_array(useragent, ua_filters):
                continue
            if _search_array(url, url_filters):
                continue

            daily_hits[datestamp] += 1

    log.info(f"[import-webhits] parsed {total} line(s); "
        f"counted {sum(daily_hits.values())} hit(s) across {len(daily_hits)} day(s); "
        f"unrecognized = {unrec}")

    if dry_run or not daily_hits:
        if dry_run:
            for d, h in sorted(daily_hits.items())[:5]:
                log.info(f"  [dry-run] {d}  hits={h}")
            if len(daily_hits) > 5:
                log.info(f"  [dry-run] ... and {len(daily_hits) - 5} more day(s)")
        return 0

    conn = _open_db(metrics_db)
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO webhits (datetime, hits) VALUES (%s, %s)",
                sorted(daily_hits.items()),
            )
            inserted = cur.rowcount
    finally:
        conn.close()

    log.info(f"[import-webhits] inserted {inserted} row(s) into webhits")
    return 0

def cmd_import_webhits(args):
    return do_import_webhits(args.input_file, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# fill-domain  (derive `domain` column from `host`; ports xlogfix_domain.php)
# ---------------------------------------------------------------------------

# get_domain() supporting sets, mirrors xlogfix_domain.php
_DOMAIN_NO2_3LEVEL = {"ub"}
_DOMAIN_MIL_3LEVEL = {"af", "army", "navy"}
_DOMAIN_INT_3LEVEL = {"com", "net", "org", "edu", "gov", "mil",
                     "ac", "co", "ne", "or", "ed"}
_DOMAIN_US_4LEVEL  = {"k12", "lib", "cc", "tec"}

# SLD-internal patterns (active only when the hostname has exactly 2 dot-parts):
# strip a 4+ dash/underscore prefix and keep the suffix.
_DOMAIN_SLD_PATTERNS = [
    re.compile(r'^(.+-.+-.+-.+)-(.+)$'),
    re.compile(r'^(.+_.+_.+_.+)-(.+)$'),
    re.compile(r'^(.+_.+_.+_.+)_(.+)$'),
    re.compile(r'^(.+-.+-.+-.+)_(.+)$'),
]

def get_domain(hostname):
    """Extract effective domain from a hostname.  Faithful port of
    get_domain() in xlogfix_domain.php — same TLD-promotion rules,
    same special-case ordering, same '?' sentinel for non-host inputs.
    """
    if not hostname or "." not in hostname:
        return "?"
    field = hostname.split(".")
    field.reverse()    # field[0] = TLD, field[1] = SLD, ...

    domain = field[0] if field else None

    if len(field) >= 2:
        domain = f"{field[1]}.{field[0]}"

        if len(field) >= 3:
            # 3-level promote: ccTLDs with various SLD patterns
            cond_2letter = (
                field[1] not in _DOMAIN_NO2_3LEVEL
                and len(field[1]) == 2 and len(field[0]) == 2
            )
            cond_int = (
                field[1] in _DOMAIN_INT_3LEVEL and len(field[0]) == 2
            )
            cond_mil = (
                field[1] in _DOMAIN_MIL_3LEVEL and field[0] == "mil"
            )
            if cond_2letter or cond_int or cond_mil:
                domain = f"{field[2]}.{field[1]}.{field[0]}"

            # 4-level: k12/lib/cc/tec under .us
            if len(field) >= 4 and field[2] in _DOMAIN_US_4LEVEL and field[0] == "us":
                domain = f"{field[3]}.{field[2]}.{field[1]}.{field[0]}"
        else:
            # exactly 2 fields — check SLD-internal hyphen/underscore patterns
            sld = field[1]
            for pat in _DOMAIN_SLD_PATTERNS:
                m = pat.match(sld)
                if m:
                    domain = f"{m.group(2)}.{field[0]}"
                    break

    if not domain or domain == ".":
        return "?"
    return domain


FILL_DOMAIN_TABLES = ("web", "toolstart", "sessionlog_metrics")

def do_fill_domain(db_key, table, date_spec=None, *, all_dates=False,
                   dry_run=False):
    """Derive the `domain` column from `host` for unfilled rows and bulk
    UPDATE the target table.  Ports xlogfix_domain.php — same eligibility
    (domain ∈ {'', '?', NULL} AND host <> ''), same get_domain() logic,
    same SQL date semantics (datecol >= start AND datecol < end).

    Optimization vs PHP: original issued one UPDATE per row (~millions
    per month on web).  This port pulls distinct hosts, computes the
    domain locally, and applies one JOIN-UPDATE via a temp table —
    identical end state in one statement instead of N.
    """
    cfg = db_config()
    metrics_db = cfg.get("metrics_db", "")
    hub_db     = cfg.get("hub_db", "")
    if not metrics_db:
        msg = "[fill-domain] missing metrics_db in access.cfg"
        log.info(msg)
        return 2

    if db_key == "metrics":
        db_name = metrics_db
    elif db_key == "hub":
        db_name = hub_db
    else:
        msg = f"[fill-domain] unknown db_key {db_key!r}"
        log.info(msg)
        return 2

    if table not in FILL_DOMAIN_TABLES:
        # Not strictly enforced by the PHP, but the only callers in the
        # pipeline are these three.  Warn but proceed.
        log.warning(f"[fill-domain] table {table!r} is not one of the "
                    f"usual targets ({FILL_DOMAIN_TABLES}); proceeding anyway")

    d_col = "start" if table == "sessionlog_metrics" else "datetime"

    # date range
    if all_dates:
        start_d = end_d = None
    elif date_spec:
        try:
            start_d, end_d = parse_date_range(date_spec)
        except ValueError as e:
            msg = f"[fill-domain] {e}"
            log.info(msg)
            return 2
    else:
        today = date.today()
        start_d = today.replace(day=1)
        end_d = today + timedelta(days=1)

    # PHP findWeeks() starts a week BEFORE the month begins (the legacy
    # boundary convention) — shift start_d back one day to match.
    if start_d is not None:
        start_d = start_d - timedelta(days=1)

    # SQL predicates — PHP uses `datecol >= start AND datecol < end`
    parts = []
    params = []
    if start_d is not None:
        parts.append(f"{d_col} >= %s")
        params.append(f"{start_d.isoformat()} 00:00:00")
    if end_d is not None:
        parts.append(f"{d_col} < %s")
        params.append(f"{end_d.isoformat()} 00:00:00")
    date_pred_sql = (" AND " + " AND ".join(parts)) if parts else ""
    date_params = tuple(params)

    scope_label = "ALL" if (start_d is None and end_d is None) \
        else f"{start_d if start_d else '...'}..{end_d if end_d else '...'}"

    conn = _open_db(db_name)
    try:
        with conn.cursor() as cur:
            select_sql = (
                f"SELECT DISTINCT LOWER(host) FROM {table} "
                f"WHERE host <> '' AND host IS NOT NULL "
                f"AND (domain = '' OR domain = '?' OR domain IS NULL)"
                f"{date_pred_sql}"
            )
            cur.execute(select_sql, date_params)
            hosts = [r[0] for r in cur.fetchall() if r[0]]

            log.info(f"[fill-domain] {db_name}.{table} {scope_label}: "
                f"{len(hosts)} distinct host(s) to derive domain from")

            if not hosts:
                return 0

            pairs = [(h, get_domain(h)) for h in hosts]

            if dry_run:
                for h, d in pairs[:5]:
                    log.info(f"  [dry-run] {h!r} -> {d!r}")
                if len(pairs) > 5:
                    log.info(f"  [dry-run] ... and {len(pairs) - 5} more")
                return 0

            # Bulk update via temp table + JOIN
            cur.execute(
                "CREATE TEMPORARY TABLE _domain_tmp ("
                "host VARCHAR(255) NOT NULL PRIMARY KEY, "
                "domain VARCHAR(255)) ENGINE=Memory"
            )
            cur.executemany(
                "INSERT INTO _domain_tmp (host, domain) VALUES (%s, %s)",
                pairs,
            )
            update_sql = (
                f"UPDATE {table} t "
                f"INNER JOIN _domain_tmp d ON LOWER(t.host) = d.host "
                f"SET t.domain = d.domain "
                f"WHERE (t.domain = '' OR t.domain = '?' OR t.domain IS NULL) "
                f"AND t.host <> '' AND t.host IS NOT NULL"
                f"{date_pred_sql.replace(d_col, 't.' + d_col)}"
            )
            cur.execute(update_sql, date_params)
            updated = cur.rowcount
            cur.execute("DROP TEMPORARY TABLE _domain_tmp")

        log.info(f"[fill-domain] updated {updated} row(s) in {table}")
        return 0
    finally:
        conn.close()

def cmd_fill_domain(args):
    return do_fill_domain(args.db_key, args.table, args.date_spec,
                          all_dates=args.all,
                          dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# import-apache  (parse apache log → metrics.web; ports xlogimport_apache.php)
# ---------------------------------------------------------------------------

# URL exclusion patterns — content with these suffixes (optionally with a
# query string) is not stored in the web table.
_CODE_SUFFIXES = "css|js"
_IMG_SUFFIXES  = "gif|jpeg|jpg|png|ps|ico"
_FONT_SUFFIXES = "svg|otf|ttf|woff|eot"
_EXCLUDE_SUFFIX_RE = re.compile(
    r'\.(' + '|'.join((_CODE_SUFFIXES, _IMG_SUFFIXES, _FONT_SUFFIXES)) +
    r')(\?.*=.*(\&.*=.*)*)*$',
    re.IGNORECASE,
)
_TEMPLATES_RE = re.compile(r'^(/app)*/templates/', re.IGNORECASE)
_ADMIN_RE     = re.compile(r'^/administrator/',    re.IGNORECASE)
_WEBDAV_RE    = re.compile(r'^/webdav/',           re.IGNORECASE)
_API_RE       = re.compile(r'^/api/',              re.IGNORECASE)
_CRON_RE      = re.compile(r'^/cron/tick/',        re.IGNORECASE)
_SVN_RE       = re.compile(r'/projects/.+?/svn/\!svn/', re.IGNORECASE)
_RESOURCES_RE = re.compile(r'^/resources/',        re.IGNORECASE)

# dnload flag triggers (matches the new-code addition to set web.dnload=1)
_DOWNLOAD_PATH_RE = re.compile(r'^/resources/.*/download/', re.IGNORECASE)
_DOWNLOAD_EXTS = (
    "txt|png|pdf|ppt|pptx|swf|docx|jpg|doc|zip|mp3|mbtiles|xml|xlsx|"
    "webm|mp4|xls|r|csv|nc4|template|tgz|mov|ipynb|py|rar|grd|tif|nc|har"
)
_DOWNLOAD_EXT_RE = re.compile(
    r'^/resources/.*\.(' + _DOWNLOAD_EXTS + r')([?#]|$)',
    re.IGNORECASE,
)

def _is_download_url(url):
    return bool(_DOWNLOAD_PATH_RE.match(url) or _DOWNLOAD_EXT_RE.match(url))

def _is_excluded_url(url):
    """True iff URL hits any exclusion rule (suffix or path)."""
    if _EXCLUDE_SUFFIX_RE.search(url): return True
    if _TEMPLATES_RE.match(url):       return True
    if _ADMIN_RE.match(url):           return True
    if _WEBDAV_RE.match(url):          return True
    if _API_RE.match(url):             return True
    if _CRON_RE.match(url):            return True
    if _SVN_RE.search(url):            return True
    return False

def do_import_apache(input_file, *, batch_size=5000, dry_run=False):
    """Parse an apache staged log file and INSERT eligible rows into
    metrics.web.  Ports xlogimport_apache.php (source-tree version with
    the dnload column set).

    Eligibility:
      - regex matches new or old apache log format
      - status=200, bytes>0, method ∈ {GET,POST}
      - IP / UA / URL not matched by exclude_list filters
      - useragent not present in metrics.bot_useragents (exact match)
      - URL not excluded by suffix/path rules, OR is under /resources/
    Sets dnload=1 for resource-download URL shapes; 0 otherwise.
    """
    cfg = db_config()
    metrics_db = cfg.get("metrics_db", "")
    if not metrics_db:
        msg = "[import-apache] missing metrics_db in access.cfg"
        log.info(msg)
        return 2

    # Load filter lists once
    conn = _open_db(metrics_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT filter FROM exclude_list WHERE type='ip'")
            ip_filters = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT filter FROM exclude_list WHERE type='useragent'")
            ua_filters = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT filter FROM exclude_list WHERE type='url'")
            url_filters = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT useragent FROM bot_useragents")
            bot_uas = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()

    log.info(f"[import-apache] loaded filters: "
        f"ip={len(ip_filters)} ua={len(ua_filters)} url={len(url_filters)} "
        f"bot_useragents={len(bot_uas)}")

    # Legacy 1018cc2^ does NOT set dnload at import time (the column itself
    # didn't exist yet — was added by the 1018cc2 refactor).  Insert without
    # the dnload column to preserve byte-for-byte parity; backfill-dnload
    # populates it in a separate pass.
    #
    # To restore the post-1018cc2 in-line dnload set (saves a backfill pass),
    # swap to the commented INSERT below and re-enable the `dnload` field +
    # `_is_download_url(url)` evaluation in the row append:
    #     INSERT INTO web (..., item_name, dnload) VALUES (... ,%s, %s)
    #     dnload = 1 if _is_download_url(url) else 0
    #     rows_buf.append((..., item_name, dnload))
    insert_sql = (
        "INSERT INTO web "
        "(datetime, content, ip, uidNumber, apache_pid, referrer, useragent, "
        "joomla_sessionid, site_cookie, auth_type, component_name, view_name, "
        "task_name, action_name, item_name) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
    )

    rows_buf = []
    total = 0
    parsed = 0
    inserted = 0
    unrec = 0
    skipped_bot = 0
    skipped_status = 0
    skipped_filter = 0
    skipped_url = 0
    # dnload_set retained for parity with the post-1018cc2 commented INSERT.
    dnload_set = 0

    conn = _open_db(metrics_db)
    try:
        with conn.cursor() as cur:
            with _open_input(input_file) as src:
                for line in src:
                    total += 1
                    line = line.rstrip("\r\n")

                    m = _APACHE_PAT_NEW.match(line)
                    if m:
                        datestamp = m.group(1)
                        timestamp = m.group(2)
                        pid       = m.group(4)
                        firstline = m.group(6)
                        ret_code  = m.group(7)
                        bytes_str = m.group(8)
                        ip        = m.group(9)
                        referrer  = m.group(10)
                        useragent = m.group(11)
                        uidNumber = m.group(15)
                        joomla_id = m.group(16)
                        st_cookie = m.group(17)
                        auth_type = m.group(18)
                        comp_name = m.group(19)
                        view_name = m.group(20)
                        task_name = m.group(21)
                        actn_name = m.group(22)
                        item_name = m.group(23)
                    else:
                        m = _APACHE_PAT_OLD.match(line)
                        if not m:
                            unrec += 1
                            continue
                        datestamp = m.group(1)
                        timestamp = m.group(2)
                        pid       = ''
                        firstline = m.group(5)
                        ret_code  = m.group(6)
                        bytes_str = m.group(7)
                        ip        = m.group(8)
                        referrer  = m.group(9)
                        useragent = m.group(10)
                        uidNumber = ''
                        joomla_id = ''
                        st_cookie = m.group(14)
                        auth_type = ''
                        comp_name = ''
                        view_name = ''
                        task_name = ''
                        actn_name = ''
                        item_name = ''
                    parsed += 1

                    # Normalize uidNumber: '' / '-' → 0, else int (fallback 0)
                    if not uidNumber or uidNumber == '-':
                        uid = 0
                    else:
                        try:
                            uid = int(uidNumber)
                        except ValueError:
                            uid = 0

                    # Parse request line — single-token fallback per PHP
                    parts = firstline.strip().split(None, 2)
                    method = parts[0] if parts else ''
                    url    = parts[1] if len(parts) > 1 else ''
                    if not url:
                        url = method
                        method = 'GET'
                    url = _SLASH_COLLAPSE.sub('/', url)

                    # Filter chain — same order as PHP
                    if ret_code != "200":
                        skipped_status += 1
                        continue
                    try:
                        if int(bytes_str) <= 0:
                            skipped_status += 1
                            continue
                    except ValueError:
                        skipped_status += 1
                        continue
                    if method != "GET" and method != "POST":
                        skipped_status += 1
                        continue
                    if _search_array(ip, ip_filters) or \
                       _search_array(useragent, ua_filters) or \
                       _search_array(url, url_filters):
                        skipped_filter += 1
                        continue
                    if useragent and useragent != '-' and useragent in bot_uas:
                        skipped_bot += 1
                        continue

                    # URL include: excluded by suffix/path UNLESS under /resources/
                    if _is_excluded_url(url) and not _RESOURCES_RE.match(url):
                        skipped_url += 1
                        continue

                    # Legacy 1018cc2^ omits dnload at insert time — see the
                    # insert_sql comment block above for the post-1018cc2 form.
                    rows_buf.append((
                        f"{datestamp} {timestamp}",
                        url, ip, uid, pid, referrer, useragent,
                        joomla_id, st_cookie, auth_type,
                        comp_name, view_name, task_name, actn_name, item_name,
                    ))

                    if len(rows_buf) >= batch_size and not dry_run:
                        cur.executemany(insert_sql, rows_buf)
                        inserted += cur.rowcount
                        rows_buf = []

            if rows_buf and not dry_run:
                cur.executemany(insert_sql, rows_buf)
                inserted += cur.rowcount
    finally:
        conn.close()

    log.info(f"[import-apache] parsed {parsed}/{total} (unrecognized={unrec}); "
        f"eligible={len(rows_buf) if dry_run else inserted}; "
        f"skipped: status={skipped_status} filter={skipped_filter} "
        f"bot={skipped_bot} url={skipped_url}; dnload-flagged={dnload_set}")
    return 0

def cmd_import_apache(args):
    return do_import_apache(args.input_file, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# Period date math — shared by andmore-usage and (eventually) summary
# ---------------------------------------------------------------------------

# Period codes (matches xlogfix_summary.php / xlogfix_andmore_usage.php)
PERIOD_CAL_YEAR   = 0   # current calendar year through end of <month>
PERIOD_MONTH      = 1   # the month itself
PERIOD_QUARTER    = 3   # current quarter through end of <month>
PERIOD_ROLLING_12 = 12  # 12 months ending after <month>
PERIOD_FISCAL_YR  = 13  # fiscal year Oct-Sep through end of <month>
PERIOD_ALL_TIME   = 14  # 1995-01-01 through end of <month>

def period_dates(yearmonth, period):
    """Compute (start, stop) date strings for a period centered on <yearmonth>.

    yearmonth: 'YYYY-MM' (extra characters tolerated for 'YYYY-MM-DD' or
        'YYYY-MM-00' inputs).
    period:    one of the PERIOD_* codes.
    Returns:   (start_str, stop_str) where start is the inclusive first
               day of the period and stop is the first day AFTER the
               period (exclusive).  Both formatted YYYY-MM-DD.

    Ports get_dates_for_period() from func_misc.php.
    """
    y = int(yearmonth[0:4])
    m = int(yearmonth[5:7])
    if not (1 <= m <= 12):
        raise ValueError(f"bad month in {yearmonth!r}")
    # first day of the month AFTER yearmonth
    if m == 12:
        ny, nm = y + 1, 1
    else:
        ny, nm = y, m + 1
    stop = f"{ny:04d}-{nm:02d}-01"

    if period == PERIOD_CAL_YEAR:
        start = f"{y:04d}-01-01"
    elif period == PERIOD_MONTH:
        start = f"{y:04d}-{m:02d}-01"
    elif period == PERIOD_QUARTER:
        qm = ((m - 1) // 3) * 3 + 1
        start = f"{y:04d}-{qm:02d}-01"
    elif period == PERIOD_ROLLING_12:
        # 12-month window ending at stop; start = stop minus 12 months
        sm = m - 11
        sy = y
        while sm < 1:
            sm += 12
            sy -= 1
        start = f"{sy:04d}-{sm:02d}-01"
    elif period == PERIOD_FISCAL_YR:
        start = f"{y if m >= 10 else y-1:04d}-10-01"
    elif period == PERIOD_ALL_TIME:
        start = "1995-01-01"
    else:
        raise ValueError(f"unknown period code {period}")
    return start, stop


# ---------------------------------------------------------------------------
# andmore-usage  (per-resource user counts → hub.jos_resource_stats;
#                 ports xlogfix_andmore_usage.php + helpers from func_andmore.php)
# ---------------------------------------------------------------------------

ANDMORE_PERIODS = (PERIOD_ROLLING_12, PERIOD_ALL_TIME, PERIOD_MONTH)

def _andmore_child_resources(cur, hub_db, db_prefix, parent_id):
    """Iteratively collect all descendant resource IDs of parent_id from
    hub.<prefix>resource_assoc.  Returns a list including parent_id itself."""
    visited = {int(parent_id)}
    frontier = {int(parent_id)}
    while frontier:
        cur.execute(
            f"SELECT DISTINCT child_id FROM {hub_db}.{db_prefix}resource_assoc "
            f"WHERE parent_id IN (" + ",".join(str(i) for i in frontier) + ") "
            f"AND child_id NOT IN ("  + ",".join(str(i) for i in visited)  + ")"
        )
        new_children = {int(r[0]) for r in cur.fetchall()}
        if not new_children:
            break
        visited |= new_children
        frontier = new_children
    return sorted(visited)

_ANDMORE_NUMERIC_PATH_RE = re.compile(r'^([0-9]+)(.+)$')
_ANDMORE_RESOURCES_RE    = re.compile(r'^/resources/(.+)$')
_ANDMORE_SITE_RESOURCES_RE = re.compile(r'^/site/resources/(.+)$')
_ANDMORE_LOCAL_RE        = re.compile(r'^/local/(.+)$')
_ANDMORE_SITE_RE         = re.compile(r'^/site/(.+)$')
_ANDMORE_TOPICS_RE       = re.compile(r'^/topics/(.+)$')
_ANDMORE_LM_FILE_RE      = re.compile(r'^lm/(.+)/(.+)\.(.+)$')
_ANDMORE_LM_RE           = re.compile(r'^lm/(.+)$')

def _andmore_paths(cur, hub_db, db_prefix, resid_list):
    """Build the SQL WHERE-clause OR chain that matches web.content rows
    belonging to a given resource ID set.  Faithful port of get_paths()
    in func_andmore.php — same path-style conditional logic.

    Returns a list of (sql_fragment, value) tuples suitable for parameter
    binding (sql_fragment uses %s for the value placeholder).  Empty list
    means no rows would match (no eligible paths).
    """
    if not resid_list:
        return []
    ids = ",".join(str(i) for i in resid_list)
    cur.execute(
        f"SELECT path, id FROM {hub_db}.{db_prefix}resources "
        f"WHERE path <> '' AND id IN ({ids}) AND path NOT LIKE 'http%'"
    )
    fragments = []
    for path, _rid in cur.fetchall():
        path = path.replace(' ', '%20')
        # numeric-leading: e.g. "2010/07/09423/2010.07.21-Lundstrom-NT101.pdf"
        if _ANDMORE_NUMERIC_PATH_RE.match(path):
            parts = path.split('/')
            last_part = parts[-1] if parts else ''
            second_last = parts[-2] if len(parts) >= 2 else ''
            if last_part == 'viewer.swf':
                # /site/resources/{path-without-viewer.swf}%
                prefix = path[:path.rfind('viewer.swf')]
                fragments.append(("content LIKE %s", f"/site/resources/{prefix}%"))
                # The PHP computed $path5 but only added $path4 to match_string.
            else:
                # /site/resources/<full path>
                fragments.append(("content = %s", f"/site/resources/{path}"))
                # /resources/<numeric-stripped second-last>/download/<last>
                fragments.append((
                    "content = %s",
                    f"/resources/{second_last.lstrip('0')}/download/{last_part}",
                ))
                # PHP also computed $path3 but only added $path1 and $path2.
        else:
            if (_ANDMORE_RESOURCES_RE.match(path)
                or _ANDMORE_SITE_RESOURCES_RE.match(path)
                or _ANDMORE_LOCAL_RE.match(path)
                or _ANDMORE_SITE_RE.match(path)):
                fragments.append(("content = %s", path))
            elif _ANDMORE_TOPICS_RE.match(path):
                fragments.append(("content LIKE %s", f"{path}%"))
            elif path.startswith("lm/"):
                m = _ANDMORE_LM_FILE_RE.match(path)
                if m:
                    # strip trailing /<file.ext>, match prefix/%
                    prefix = path[:path.rfind('/')]
                    fragments.append(("content LIKE %s", f"{prefix}/%"))
                else:
                    fragments.append(("content = %s", f"/site/resources/{path}"))
            else:
                fragments.append(("content = %s", f"/site/resources/{path}"))
    return fragments

def do_andmore_usage(yearmonth=None, *, dry_run=False):
    """Per-resource distinct-user counts → hub.jos_resource_stats.
    Faithful port of xlogfix_andmore_usage.php.

    For each published resource (jos_resources where published=1,
    standalone=1, type<>7):
      - Resolve the resource's URL paths plus all child-resource paths
      - For each period in (12, 14, 1):
          users = COUNT(DISTINCT ip, host) over web rows matching any
                  of the paths within the period's date window
          INSERT/UPDATE into hub.<prefix>resource_stats.
    """
    cfg = db_config()
    hub_db     = cfg.get("hub_db", "")
    metrics_db = cfg.get("metrics_db", "")
    db_prefix  = cfg.get("db_prefix", "jos_")
    if not hub_db or not metrics_db:
        msg = "[andmore-usage] missing hub_db / metrics_db in access.cfg"
        log.info(msg)
        return 2

    today = date.today()
    if yearmonth:
        ym = yearmonth[:7]   # accept YYYY-MM or YYYY-MM-DD
        processed_on = f"{ym}-01"
    else:
        ym = f"{today.year:04d}-{today.month:02d}"
        processed_on = f"{ym}-01"

    conn = _open_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT id, type FROM {hub_db}.{db_prefix}resources "
                f"WHERE published = 1 AND standalone = 1 AND type <> 7 "
                f"ORDER BY publish_up DESC"
            )
            resources = cur.fetchall()
            log.info(f"[andmore-usage] {len(resources)} published resource(s) to score")

            n_done = 0
            n_skipped_no_paths = 0
            n_upserts = 0
            for resid, restype in resources:
                children = _andmore_child_resources(cur, hub_db, db_prefix, resid)
                fragments = _andmore_paths(cur, hub_db, db_prefix, children)
                if not fragments:
                    n_skipped_no_paths += 1
                    continue

                # Build the OR chain for the match
                or_sql = " OR ".join(frag for frag, _ in fragments)
                or_params = [v for _, v in fragments]

                for period in ANDMORE_PERIODS:
                    start, stop = period_dates(ym, period)
                    cur.execute(
                        f"SELECT COUNT(DISTINCT ip, host) FROM {metrics_db}.web "
                        f"WHERE ({or_sql}) "
                        f"AND datetime >= %s AND datetime < %s",
                        or_params + [start, stop]
                    )
                    users = cur.fetchone()[0] or 0
                    if dry_run:
                        log.info(f"  [dry-run] resid={resid} period={period} "
                            f"window={start}..{stop} users={users}")
                        continue
                    cur.execute(
                        f"INSERT INTO {hub_db}.{db_prefix}resource_stats "
                        f"(resid, restype, users, datetime, period) "
                        f"VALUES (%s, %s, %s, %s, %s) "
                        f"ON DUPLICATE KEY UPDATE users = VALUES(users)",
                        (resid, restype, users, processed_on, period)
                    )
                    n_upserts += 1
                n_done += 1
                if n_done % 50 == 0:
                    log.info(f"  ...processed {n_done}/{len(resources)} resources")

            log.info(f"[andmore-usage] done: {n_done} resource(s) scored, "
                f"{n_skipped_no_paths} skipped (no eligible paths), "
                f"{n_upserts} resource_stats upsert(s)")
        return 0
    finally:
        conn.close()

def cmd_andmore_usage(args):
    return do_andmore_usage(args.yearmonth, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# fill-ipcountry  (assign ipcountry by IP geo lookup; direct port of
#                  xlogfix_ipcountry.php + get_ip_geodata helper).
#
# Per-IP, per-row semantics preserved exactly:
#   for each ~7-day chunk in the period (findWeeks shape):
#     SELECT DISTINCT(ip), COUNT(*) FROM <table> WHERE date in chunk
#       AND ipcountry IS NULL/empty ORDER BY hits desc
#     for each IP:
#       geo = get_ip_geodata(ip)
#       if geo.countrySHORT not in ('', '-'):
#         UPDATE <table> SET ipcountry=country WHERE ip=ip AND ipcountry IS NULL/empty
#
# Optimization (bulk cache, async HTTP, temp-table+JOIN UPDATE) is intentionally
# deferred to a follow-up commit so this port's A/B equivalence with the PHP
# can be argued from byte-identical SQL semantics first.
# ---------------------------------------------------------------------------

IPCOUNTRY_URL     = "https://help.hubzero.org/ipinfo/v1"
IPCOUNTRY_HUB_KEY = "_HUBZERO_OPNSRC_V1_"
IPCOUNTRY_TIMEOUT = 5     # seconds; PHP relies on default_socket_timeout (~60s)

# Fallback chain tried in order if the primary endpoint times out / errors.
# help.hubzero.org is the documented home; hubzero.org still serves a copy.
# https is preferred but http remains as a final fallback for legacy plumbing.
IPCOUNTRY_FALLBACKS = (
    "https://help.hubzero.org/ipinfo/v1",
    "https://hubzero.org/ipinfo/v1",
    "http://help.hubzero.org/ipinfo/v1",
    "http://hubzero.org/ipinfo/v1",
)

def _ip2long(ip_str):
    """Convert dotted-quad IPv4 to 32-bit int.  Returns None on bad input.
    Matches PHP ip2long() behavior for valid v4 addresses."""
    try:
        parts = ip_str.split('.')
        if len(parts) != 4:
            return None
        n = 0
        for p in parts:
            v = int(p)
            if not (0 <= v <= 255):
                return None
            n = (n << 8) | v
        return n
    except (ValueError, AttributeError):
        return None

def _ipgeo_defaults(n_ip):
    """Default geo_data dict (every field '-') matching the PHP."""
    return {
        'n_ip':         n_ip,
        'countrySHORT': '-',
        'countryLONG':  '-',
        'ipREGION':     '-',
        'ipCITY':       '-',
        'ipLATITUDE':   '-',
        'ipLONGITUDE':  '-',
    }

def _get_ip_geodata(conn, ip, *, url=IPCOUNTRY_URL, hub_key=IPCOUNTRY_HUB_KEY,
                   timeout=IPCOUNTRY_TIMEOUT, ttl_days=90):
    """Look up geo data for one IP.  Mirrors get_ip_geodata() in
    func_misc.php — checks the hub's metrics_ipgeo_cache (90-day TTL),
    falls through to HTTP, INSERTs into the cache on success."""
    n_ip = _ip2long(ip)
    geo = _ipgeo_defaults(n_ip)
    if n_ip is None:
        return geo

    cfg = db_config()
    hub_db    = cfg.get('hub_db', '')
    db_prefix = cfg.get('db_prefix', 'jos_')

    # --- cache lookup ---
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT countrySHORT, countryLONG, ipREGION, ipCITY, "
            f"ipLATITUDE, ipLONGITUDE, lookup_datetime "
            f"FROM {hub_db}.{db_prefix}metrics_ipgeo_cache "
            f"WHERE ip = %s "
            f"AND TO_DAYS(CURDATE()) - TO_DAYS(lookup_datetime) <= %s",
            (n_ip, ttl_days),
        )
        row = cur.fetchone()
        if row:
            geo['countrySHORT'] = row[0]
            geo['countryLONG']  = row[1]
            geo['ipREGION']     = row[2]
            geo['ipCITY']       = row[3]
            geo['ipLATITUDE']   = row[4]
            geo['ipLONGITUDE']  = row[5]
            return geo

    # --- HTTP fallback ---
    # Build the endpoint list: the explicitly-given `url` first, then
    # IPCOUNTRY_FALLBACKS (de-duped, preserving order).  Stops at first success.
    import urllib.request, xml.etree.ElementTree as ET
    endpoints = [url] + [u for u in IPCOUNTRY_FALLBACKS if u != url]
    root = None
    for ep in endpoints:
        full_url = f"{ep}/?&hub_key={hub_key}&n_ip={n_ip}"
        try:
            with urllib.request.urlopen(full_url, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                root = ET.fromstring(text)
            break
        except (urllib.request.URLError, ET.ParseError, TimeoutError, OSError) as e:
            log.warning(f"ipinfo {ep} failed ({e}); trying next fallback")
            continue
    if root is None:
        return geo

    status = (root.findtext('status') or '').strip()
    ipset  = root.find('ipset')
    if status == '_SUCCESS_' and ipset is not None and \
       (ipset.findtext('n_ip') or '').strip() == str(n_ip):
        geo['n_ip']         = int(ipset.findtext('n_ip') or n_ip)
        geo['countrySHORT'] = (ipset.findtext('countryCode') or '-').strip() or '-'
        geo['countryLONG']  = (ipset.findtext('countryName') or '-').strip() or '-'
        geo['ipREGION']     = (ipset.findtext('region')      or '-').strip() or '-'
        geo['ipCITY']       = (ipset.findtext('city')        or '-').strip() or '-'
        geo['ipLATITUDE']   = (ipset.findtext('lat')         or '-').strip() or '-'
        geo['ipLONGITUDE']  = (ipset.findtext('long')        or '-').strip() or '-'
        if geo['countrySHORT'] != '-':
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {hub_db}.{db_prefix}metrics_ipgeo_cache "
                    f"(ip, countrySHORT, countryLONG, ipREGION, ipCITY, "
                    f"ipLATITUDE, ipLONGITUDE) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    f"ON DUPLICATE KEY UPDATE "
                    f"countrySHORT=VALUES(countrySHORT), "
                    f"countryLONG=VALUES(countryLONG), "
                    f"ipREGION=VALUES(ipREGION), "
                    f"ipCITY=VALUES(ipCITY), "
                    f"ipLATITUDE=VALUES(ipLATITUDE), "
                    f"ipLONGITUDE=VALUES(ipLONGITUDE)",
                    (n_ip, geo['countrySHORT'], geo['countryLONG'],
                     geo['ipREGION'], geo['ipCITY'],
                     geo['ipLATITUDE'], geo['ipLONGITUDE']),
                )
    elif status == '_INVALID_KEY_OR_KEY-HUB_HOSTNAME_MISMATCH_':
        log.warning("HUBzero.org IP-Geo location key invalid for this host. "
                    "Check the hub registration / hub_key setting.")
    return geo

FILL_IPCOUNTRY_TABLES = ("web", "websessions", "toolstart", "sessionlog_metrics")

def do_fill_ipcountry(db_key, table, date_spec=None, *, all_dates=False,
                      url=IPCOUNTRY_URL, hub_key=IPCOUNTRY_HUB_KEY,
                      timeout=IPCOUNTRY_TIMEOUT, dry_run=False):
    """Direct port of xlogfix_ipcountry.php.  Per-IP / per-row, per-week-chunk
    SQL semantics preserved exactly — optimization is a separate concern.
    """
    cfg = db_config()
    metrics_db = cfg.get("metrics_db", "")
    hub_db     = cfg.get("hub_db", "")
    if not metrics_db:
        msg = "[fill-ipcountry] missing metrics_db in access.cfg"
        log.info(msg)
        return 2

    if db_key == "metrics":
        db_name = metrics_db
    elif db_key == "hub":
        db_name = hub_db
    else:
        msg = f"[fill-ipcountry] unknown db_key {db_key!r}"
        log.info(msg)
        return 2

    d_col = "start" if table == "sessionlog_metrics" else "datetime"

    if all_dates:
        msg = "[fill-ipcountry] --all not supported; specify a date or range"
        log.info(msg)
        return 2

    if date_spec:
        try:
            start_d, end_d = parse_date_range(date_spec)
        except ValueError as e:
            msg = f"[fill-ipcountry] {e}"
            log.info(msg)
            return 2
        if start_d is None or end_d is None:
            msg = "[fill-ipcountry] open-ended ranges not supported"
            log.info(msg)
            return 2
    else:
        today = date.today()
        start_d = today.replace(day=1)
        end_d   = today + timedelta(days=1)

    chunks = list(_findweeks(start_d, end_d))
    log.info(f"[fill-ipcountry] {db_name}.{table} {start_d}..{end_d}: "
        f"{len(chunks)} week chunk(s); url={url}")

    conn = _open_db()
    try:
        total_select = 0
        total_update = 0
        for c_start, c_end in chunks:
            with conn.cursor() as cur:
                # PHP SQL uses `> start AND <= end` (findWeeks boundary)
                cur.execute(
                    f"SELECT DISTINCT(ip) AS n_ip, COUNT(*) AS hits "
                    f"FROM {db_name}.{table} "
                    f"WHERE {d_col} > %s AND {d_col} <= %s "
                    f"AND (ipcountry = '' OR ipcountry IS NULL) "
                    f"GROUP BY n_ip ORDER BY hits DESC",
                    (c_start, c_end),
                )
                rows = cur.fetchall()
            total_select += len(rows)
            if not rows:
                continue

            log.info(f"  chunk {c_start}..{c_end}: {len(rows)} distinct IP(s)")
            if dry_run:
                continue

            for ip, _hits in rows:
                geo = _get_ip_geodata(conn, ip, url=url, hub_key=hub_key, timeout=timeout)
                country = geo['countrySHORT']
                if country and country != '-':
                    with conn.cursor() as cur:
                        cur.execute(
                            f"UPDATE {db_name}.{table} "
                            f"SET ipcountry = %s "
                            f"WHERE (ipcountry = '' OR ipcountry IS NULL) "
                            f"AND ip = %s",
                            (country, ip),
                        )
                        total_update += cur.rowcount
        log.info(f"[fill-ipcountry] done: {total_select} IP(s) considered, "
            f"{total_update} row(s) updated")
        return 0
    finally:
        conn.close()

def cmd_fill_ipcountry(args):
    return do_fill_ipcountry(
        args.db_key, args.table, args.date_spec,
        all_dates=args.all,
        url=args.url, hub_key=args.hub_key, timeout=args.timeout,
        dry_run=args.dry_run,
    )


# ---------------------------------------------------------------------------
# gen-tool-stats  (per-tool resource_stats_tools + resource_stats;
#                  direct port of gen_tool_stats.php)
# ---------------------------------------------------------------------------

GEN_TOOL_STATS_PERIODS = (0, 1, 3, 12, 13, 14)

def _get_tool_versions_aliases(cur, hub_db, db_prefix, alias):
    """Build the list of acceptable appname values for matching a tool's
    sessions: starts with the alias itself, then adds every distinct
    instance from jos_tool_version where toolname=alias (excluding _dev
    instances).  Ports get_tool_versions_aliases() in func_misc.php."""
    out = [alias]
    cur.execute(
        f"SELECT DISTINCT instance FROM {hub_db}.{db_prefix}tool_version "
        f"WHERE toolname = %s AND instance NOT LIKE %s",
        (alias, '%\\_dev'),
    )
    for (inst,) in cur.fetchall():
        if inst:
            out.append(inst)
    return out

def _compute_tool_stats(cur, hub_db, aliases, dstart, dstop):
    """Aggregate sessionlog+joblog metrics for an alias set in a date range.
    Returns a dict matching the columns in resource_stats_tools."""
    stats = {k: 0 for k in (
        'users', 'sessions', 'simulations', 'jobs',
        'avg_wall', 'tot_wall', 'avg_cpu', 'tot_cpu',
        'avg_view', 'tot_view', 'avg_wait', 'tot_wait',
        'avg_cpus', 'tot_cpus',
    )}
    placeholders = ",".join(["%s"] * len(aliases))
    base_args = tuple(aliases) + (dstart, dstop)

    # users
    cur.execute(
        f"SELECT COUNT(DISTINCT username) FROM {hub_db}.sessionlog "
        f"WHERE appname IN ({placeholders}) AND start > %s AND start < %s",
        base_args)
    stats['users'] = cur.fetchone()[0] or 0
    if not stats['users']:
        return stats   # PHP bails here too — all the rest stay 0

    # jobs (job > 0, event != '[waiting]')
    cur.execute(
        f"SELECT COUNT(*) FROM {hub_db}.joblog AS j, {hub_db}.sessionlog AS s "
        f"WHERE s.sessnum = j.sessnum AND s.appname IN ({placeholders}) "
        f"AND s.start > %s AND s.start < %s "
        f"AND j.event <> '[waiting]' AND j.job > 0",
        base_args)
    stats['jobs'] = cur.fetchone()[0] or 0

    # sessions
    cur.execute(
        f"SELECT COUNT(*) FROM {hub_db}.sessionlog "
        f"WHERE appname IN ({placeholders}) AND start > %s AND start < %s",
        base_args)
    stats['sessions'] = cur.fetchone()[0] or 0

    # simulations (event != 'application', superjob = 0)
    cur.execute(
        f"SELECT COUNT(*) FROM {hub_db}.sessionlog AS s, {hub_db}.joblog AS j "
        f"WHERE s.sessnum = j.sessnum AND s.appname IN ({placeholders}) "
        f"AND s.start > %s AND s.start < %s "
        f"AND j.event <> 'application' AND j.superjob = 0",
        base_args)
    stats['simulations'] = cur.fetchone()[0] or 0

    sims = stats['simulations']

    # walltime
    cur.execute(
        f"SELECT COALESCE(SUM(walltime), 0) FROM {hub_db}.sessionlog "
        f"WHERE appname IN ({placeholders}) AND start > %s AND start < %s",
        base_args)
    stats['tot_wall'] = float(cur.fetchone()[0] or 0)
    if stats['tot_wall'] and sims:
        stats['avg_wall'] = stats['tot_wall'] / sims

    # cputime (job > 0, event != '[waiting]')
    cur.execute(
        f"SELECT COALESCE(SUM(j.cputime), 0) FROM {hub_db}.joblog AS j, "
        f"{hub_db}.sessionlog AS s WHERE s.sessnum = j.sessnum "
        f"AND s.appname IN ({placeholders}) "
        f"AND s.start > %s AND s.start < %s "
        f"AND j.event <> '[waiting]' AND j.job > 0",
        base_args)
    stats['tot_cpu'] = float(cur.fetchone()[0] or 0)
    if stats['tot_cpu'] and sims:
        stats['avg_cpu'] = stats['tot_cpu'] / sims

    # viewtime
    cur.execute(
        f"SELECT COALESCE(SUM(viewtime), 0) FROM {hub_db}.sessionlog "
        f"WHERE appname IN ({placeholders}) AND start > %s AND start < %s",
        base_args)
    stats['tot_view'] = float(cur.fetchone()[0] or 0)
    if stats['tot_view'] and sims:
        stats['avg_view'] = stats['tot_view'] / sims

    # waittime (event == '[waiting]', job > 0)
    cur.execute(
        f"SELECT COALESCE(SUM(j.walltime), 0) FROM {hub_db}.joblog AS j, "
        f"{hub_db}.sessionlog AS s WHERE s.sessnum = j.sessnum "
        f"AND s.appname IN ({placeholders}) "
        f"AND s.start > %s AND s.start < %s "
        f"AND j.event = '[waiting]' AND j.job > 0",
        base_args)
    stats['tot_wait'] = float(cur.fetchone()[0] or 0)
    if stats['tot_wait'] and sims:
        stats['avg_wait'] = stats['tot_wait'] / sims

    # ncpus
    cur.execute(
        f"SELECT SUM(j.ncpus) FROM {hub_db}.sessionlog AS s, "
        f"{hub_db}.joblog AS j WHERE s.sessnum = j.sessnum "
        f"AND s.appname IN ({placeholders}) "
        f"AND s.start > %s AND s.start < %s",
        base_args)
    stats['tot_cpus'] = int(cur.fetchone()[0] or 0)
    if stats['tot_cpus'] and sims:
        stats['avg_cpus'] = round(stats['tot_cpus'] / sims)

    return stats

def _upsert_tool_stats_row(cur, hub_db, db_prefix, resid, stats, dthis, period, existing_id):
    if existing_id is None:
        cur.execute(
            f"INSERT INTO {hub_db}.{db_prefix}resource_stats_tools "
            f"(resid, restype, users, sessions, simulations, jobs, "
            f"avg_wall, tot_wall, avg_cpu, tot_cpu, avg_view, tot_view, "
            f"avg_wait, tot_wait, avg_cpus, tot_cpus, datetime, period, processed_on) "
            f"VALUES (%s, '7', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
            (resid, stats['users'], stats['sessions'], stats['simulations'],
             stats['jobs'], stats['avg_wall'], stats['tot_wall'],
             stats['avg_cpu'], stats['tot_cpu'], stats['avg_view'],
             stats['tot_view'], stats['avg_wait'], stats['tot_wait'],
             stats['avg_cpus'], stats['tot_cpus'], dthis, period))
    else:
        cur.execute(
            f"UPDATE {hub_db}.{db_prefix}resource_stats_tools "
            f"SET users=%s, sessions=%s, simulations=%s, jobs=%s, "
            f"avg_wall=%s, tot_wall=%s, avg_cpu=%s, tot_cpu=%s, "
            f"avg_view=%s, tot_view=%s, avg_wait=%s, tot_wait=%s, "
            f"avg_cpus=%s, tot_cpus=%s, processed_on=NOW() "
            f"WHERE id = %s",
            (stats['users'], stats['sessions'], stats['simulations'],
             stats['jobs'], stats['avg_wall'], stats['tot_wall'],
             stats['avg_cpu'], stats['tot_cpu'], stats['avg_view'],
             stats['tot_view'], stats['avg_wait'], stats['tot_wait'],
             stats['avg_cpus'], stats['tot_cpus'], existing_id))

def _propagate_to_resource_stats(cur, hub_db, db_prefix, dthis, period, resid):
    """Mirror the PHP's update_stats() — copy the (resid, restype, users,
    jobs, avg_wall, tot_wall, avg_cpu, tot_cpu) subset from
    resource_stats_tools into resource_stats."""
    cur.execute(
        f"SELECT id FROM {hub_db}.{db_prefix}resource_stats "
        f"WHERE restype = '7' AND datetime = %s AND period = %s AND resid = %s",
        (dthis, period, resid))
    existing = cur.fetchone()
    existing_id = existing[0] if existing else None

    cur.execute(
        f"SELECT resid, restype, users, jobs, avg_wall, tot_wall, avg_cpu, tot_cpu "
        f"FROM {hub_db}.{db_prefix}resource_stats_tools "
        f"WHERE datetime = %s AND period = %s AND resid = %s",
        (dthis, period, resid))
    rows = cur.fetchall()
    # PHP `dbquote()` quotes floats as strings — MySQL parses '488.5' and
    # rounds to INT half-away-from-zero (→ 489).  pymysql sends Python floats
    # as numeric literals — MySQL rounds those banker's-style (→ 488).
    # To match the legacy round, stringify floats before sending.
    def _stringify(row):
        return tuple(str(v) if isinstance(v, float) else v for v in row)

    for row in rows:
        row = _stringify(row)
        if existing_id is None:
            cur.execute(
                f"INSERT INTO {hub_db}.{db_prefix}resource_stats "
                f"(resid, restype, users, jobs, avg_wall, tot_wall, avg_cpu, tot_cpu, "
                f"datetime, period, processed_on) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                row + (dthis, period))
        else:
            cur.execute(
                f"UPDATE {hub_db}.{db_prefix}resource_stats "
                f"SET resid=%s, restype=%s, users=%s, jobs=%s, "
                f"avg_wall=%s, tot_wall=%s, avg_cpu=%s, tot_cpu=%s, "
                f"processed_on=NOW() WHERE id = %s",
                row + (existing_id,))

def do_gen_tool_stats(yearmonth=None, *, dry_run=False):
    """For each tool resource (jos_resources.type=7), aggregate session and
    job metrics across the standard period codes and UPSERT into
    hub.<prefix>resource_stats_tools, then propagate the subset to
    hub.<prefix>resource_stats.  Direct port of gen_tool_stats.php.
    """
    cfg = db_config()
    hub_db    = cfg.get('hub_db', '')
    db_prefix = cfg.get('db_prefix', 'jos_')
    if not hub_db:
        msg = "[gen-tool-stats] missing hub_db in access.cfg"
        log.info(msg)
        return 2

    if yearmonth:
        ym = yearmonth[:7]   # accept YYYY-MM or YYYY-MM-DD
    else:
        today = date.today()
        ym = f"{today.year:04d}-{today.month:02d}"
    dthis = f"{ym}-00 00:00:00"   # matches PHP get_dates()['dthis']

    conn = _open_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, alias FROM {hub_db}.{db_prefix}resources "
                f"WHERE type = '7' AND alias <> '' ORDER BY title"
            )
            tools = list(cur.fetchall())
            log.info(f"[gen-tool-stats] {len(tools)} tool resource(s); month {ym}")

            for resid, alias in tools:
                aliases = _get_tool_versions_aliases(cur, hub_db, db_prefix, alias)
                if not aliases:
                    continue

                for period in GEN_TOOL_STATS_PERIODS:
                    dstart, dstop = period_dates(ym, period)

                    cur.execute(
                        f"SELECT id FROM {hub_db}.{db_prefix}resource_stats_tools "
                        f"WHERE restype = '7' AND datetime = %s AND resid = %s AND period = %s",
                        (dthis, resid, period))
                    existing = cur.fetchone()
                    existing_id = existing[0] if existing else None

                    stats = _compute_tool_stats(cur, hub_db, aliases, dstart, dstop)

                    if dry_run:
                        log.info(f"  [dry-run] resid={resid} alias={alias!r} period={period} "
                            f"window={dstart}..{dstop} users={stats['users']} "
                            f"sims={stats['simulations']} jobs={stats['jobs']}")
                        continue

                    _upsert_tool_stats_row(cur, hub_db, db_prefix,
                                           resid, stats, dthis, period, existing_id)
                    _propagate_to_resource_stats(cur, hub_db, db_prefix,
                                                 dthis, period, resid)
        log.info(f"[gen-tool-stats] done")
        return 0
    finally:
        conn.close()

def cmd_gen_tool_stats(args):
    return do_gen_tool_stats(args.yearmonth, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# gen-tool-tops  (top-N breakdowns per tool stat row into
#                 jos_resource_stats_tools_topvals; ports gen_tool_tops.php)
# ---------------------------------------------------------------------------

# top codes
GEN_TOOL_TOP_COUNTRYRES = 1
GEN_TOOL_TOP_DOMAIN     = 2
GEN_TOOL_TOP_ORGTYPE    = 3

def _topvals_insert(cur, hub_db, db_prefix, stat_id, top, rank, name, value):
    cur.execute(
        f"INSERT INTO {hub_db}.{db_prefix}resource_stats_tools_topvals "
        f"(id, top, rank, name, value) VALUES (%s, %s, %s, %s, %s)",
        (stat_id, top, rank, name, value))

def _gen_topcountryres(cur, hub_db, metrics_db, db_prefix,
                       aliases, dstart, dstop, stat_id, users, top):
    # row 0: "Total Users"
    _topvals_insert(cur, hub_db, db_prefix, stat_id, top, 0, "Total Users", users)
    placeholders = ",".join(["%s"] * len(aliases))
    cur.execute(
        f"SELECT DISTINCT(countryresident) AS country, name, COUNT(DISTINCT user) AS cnt "
        f"FROM {metrics_db}.sessionlog_metrics "
        f"LEFT JOIN {metrics_db}.countries ON countryresident = code "
        f"WHERE appname IN ({placeholders}) AND start > %s AND start < %s "
        f"GROUP BY country ORDER BY cnt DESC LIMIT 10",
        tuple(aliases) + (dstart, dstop))
    rows = cur.fetchall()
    rank = 0
    for _code, country_name, cnt in rows:
        rank += 1
        label = country_name if country_name else "Unknown"
        _topvals_insert(cur, hub_db, db_prefix, stat_id, top, rank, label, cnt)
    if rank == 0:
        # PHP: if no rows, insert single "Unknown" with users count
        _topvals_insert(cur, hub_db, db_prefix, stat_id, top, 1, "Unknown", users)

def _gen_topdomains(cur, hub_db, metrics_db, db_prefix,
                    aliases, dstart, dstop, stat_id, users, top):
    _topvals_insert(cur, hub_db, db_prefix, stat_id, top, 0, "Total Users", users)
    placeholders = ",".join(["%s"] * len(aliases))
    cur.execute(
        f"SELECT DISTINCT(domain) AS dom, COUNT(DISTINCT user) AS cnt "
        f"FROM {metrics_db}.sessionlog_metrics "
        f"WHERE appname IN ({placeholders}) AND start > %s AND start < %s "
        f"GROUP BY dom ORDER BY cnt DESC LIMIT 10",
        tuple(aliases) + (dstart, dstop))
    rank = 0
    for dom, cnt in cur.fetchall():
        rank += 1
        label = dom if dom else "Unknown"
        _topvals_insert(cur, hub_db, db_prefix, stat_id, top, rank, label, cnt)

def _gen_orgtypes(cur, hub_db, metrics_db, db_prefix,
                  aliases, dstart, dstop, stat_id, users, top):
    _topvals_insert(cur, hub_db, db_prefix, stat_id, top, 0, "Total Users", users)
    placeholders = ",".join(["%s"] * len(aliases))
    cur.execute(
        f"SELECT DISTINCT(u.orgtype), COUNT(DISTINCT t.user) AS cnt "
        f"FROM {metrics_db}.sessionlog_metrics AS t, "
        f"{metrics_db}.{db_prefix}xprofiles_metrics AS u "
        f"WHERE t.user = u.username AND t.appname IN ({placeholders}) "
        f"AND t.start > %s AND t.start < %s "
        f"GROUP BY u.orgtype ORDER BY cnt DESC",
        tuple(aliases) + (dstart, dstop))
    rank = 0
    for orgtype, cnt in cur.fetchall():
        rank += 1
        label = orgtype if orgtype else "Unknown"
        _topvals_insert(cur, hub_db, db_prefix, stat_id, top, rank, label, cnt)

# (top_code → generator function)
_GEN_TOOL_TOPS = {
    GEN_TOOL_TOP_COUNTRYRES: _gen_topcountryres,
    GEN_TOOL_TOP_DOMAIN:     _gen_topdomains,
    GEN_TOOL_TOP_ORGTYPE:    _gen_orgtypes,
}

def do_gen_tool_tops(yearmonth=None, *, dry_run=False):
    """Top-N breakdowns (country / domain / orgtype) for each tool
    resource_stats_tools row of <yearmonth>.  Writes rows into
    hub.jos_resource_stats_tools_topvals.  Direct port of
    gen_tool_tops.php.
    """
    cfg = db_config()
    hub_db     = cfg.get('hub_db', '')
    metrics_db = cfg.get('metrics_db', '')
    db_prefix  = cfg.get('db_prefix', 'jos_')
    if not hub_db or not metrics_db:
        msg = "[gen-tool-tops] missing hub_db / metrics_db in access.cfg"
        log.info(msg)
        return 2

    if yearmonth:
        ym = yearmonth[:7]
    else:
        today = date.today()
        ym = f"{today.year:04d}-{today.month:02d}"
    dthis_str = f"{ym}-00"

    conn = _open_db()
    try:
        with conn.cursor() as cur:
            for top in (GEN_TOOL_TOP_COUNTRYRES, GEN_TOOL_TOP_DOMAIN, GEN_TOOL_TOP_ORGTYPE):
                cur.execute(
                    f"SELECT res_stats.id, res_stats.resid, res.alias, "
                    f"res_stats.users, res_stats.simulations, res_stats.period, "
                    f"LEFT(res_stats.datetime, 10) AS dthis "
                    f"FROM {hub_db}.{db_prefix}resource_stats_tools AS res_stats, "
                    f"{hub_db}.{db_prefix}resources AS res "
                    f"WHERE res_stats.resid = res.id "
                    f"AND res_stats.restype = '7' AND res.standalone = '1' "
                    f"AND res_stats.datetime = %s ORDER BY datetime DESC",
                    (dthis_str,))
                rows = list(cur.fetchall())
                log.info(f"[gen-tool-tops] top={top} processing {len(rows)} stat row(s) for {dthis_str}")

                for stat_id, resid, alias, users, sims, period, _dthis_str in rows:
                    aliases = _get_tool_versions_aliases(cur, hub_db, db_prefix, alias)
                    if not aliases:
                        continue
                    dstart, dstop = period_dates(ym, period)

                    if dry_run:
                        log.info(f"  [dry-run] top={top} id={stat_id} alias={alias!r} "
                            f"period={period} window={dstart}..{dstop} users={users}")
                        continue

                    # Clear existing topvals for this (id, top), then regenerate
                    cur.execute(
                        f"DELETE FROM {hub_db}.{db_prefix}resource_stats_tools_topvals "
                        f"WHERE id = %s AND top = %s",
                        (stat_id, top))
                    _GEN_TOOL_TOPS[top](cur, hub_db, metrics_db, db_prefix,
                                        aliases, dstart, dstop, stat_id, users, top)
        log.info("[gen-tool-tops] done")
        return 0
    finally:
        conn.close()

def cmd_gen_tool_tops(args):
    return do_gen_tool_tops(args.yearmonth, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# gen-tool-toplists  (per-period ranked lists across all tools into
#                     hub.jos_stats_topvals; ports gen_tool_toplists.php)
# ---------------------------------------------------------------------------

# (top_code, total label, resource_stats_tools metric column, total source)
#   total source:
#     'sessionlog' → COUNT(DISTINCT user) from metrics.sessionlog_metrics
#     'sum'        → SUM(<col>)            from hub.resource_stats_tools
TOPLISTS_SPEC = [
    (2, 'Total Simulation Users',            'users',    'sessionlog'),
    (5, 'Total Simulation Runs',             'jobs',     'sum'),
    (6, 'Total Simulation Wall Time',        'tot_wall', 'sum'),
    (7, 'Total Simulation CPU Time',         'tot_cpu',  'sum'),
    (8, 'Total Simulation Interaction Time', 'tot_view', 'sum'),
]
GEN_TOOL_TOPLISTS_PERIODS = (0, 1, 3, 12, 13, 14)

def do_gen_tool_toplists(yearmonth=None, *, dry_run=False):
    """Per-period top-tool ranked lists into hub.jos_stats_topvals.
    Direct port of gen_tool_toplists.php.

    For each of five "top" codes (2 users, 5 jobs, 6 wall, 7 cpu, 8 view)
    × six period codes (0/1/3/12/13/14):
      1. DELETE existing stats_topvals rows for (top, period, datetime)
      2. Compute total across the period and INSERT rank=0 "Total …" row
      3. SELECT all tool resources for that period+datetime ordered by
         the metric DESC; INSERT each as rank=1,2,… with name = "<resid>
         ~ <title>".
    """
    cfg = db_config()
    hub_db     = cfg.get('hub_db', '')
    metrics_db = cfg.get('metrics_db', '')
    db_prefix  = cfg.get('db_prefix', 'jos_')
    if not hub_db or not metrics_db:
        msg = "[gen-tool-toplists] missing hub_db / metrics_db in access.cfg"
        log.info(msg)
        return 2

    if yearmonth:
        ym = yearmonth[:7]
    else:
        today = date.today()
        ym = f"{today.year:04d}-{today.month:02d}"

    conn = _open_db()
    try:
        with conn.cursor() as cur:
            for top, label, col, total_src in TOPLISTS_SPEC:
                for period in GEN_TOOL_TOPLISTS_PERIODS:
                    dstart, dstop = period_dates(ym, period)
                    dthis = f"{ym}-00 00:00:00"

                    if dry_run:
                        log.info(f"  [dry-run] top={top} ({label}) period={period} "
                            f"window={dstart}..{dstop}")
                        continue

                    # 1. clear prior rows
                    cur.execute(
                        f"DELETE FROM {hub_db}.{db_prefix}stats_topvals "
                        f"WHERE top = %s AND datetime = %s AND period = %s",
                        (top, dthis, period))

                    # 2. compute total
                    if total_src == 'sessionlog':
                        cur.execute(
                            f"SELECT COUNT(DISTINCT user) FROM {metrics_db}.sessionlog_metrics "
                            f"WHERE start > %s AND start < %s",
                            (dstart, dstop))
                    else:  # 'sum'
                        cur.execute(
                            f"SELECT SUM({col}) FROM {hub_db}.{db_prefix}resource_stats_tools "
                            f"WHERE period = %s AND datetime = %s",
                            (period, dthis))
                    total = cur.fetchone()[0] or 0

                    cur.execute(
                        f"INSERT INTO {hub_db}.{db_prefix}stats_topvals "
                        f"(top, datetime, period, rank, name, value) "
                        f"VALUES (%s, %s, %s, %s, %s, %s)",
                        (top, dthis, period, 0, label, total))

                    # 3. ranked list
                    cur.execute(
                        f"SELECT res.title, rt.resid, rt.{col} AS cnt "
                        f"FROM {hub_db}.{db_prefix}resource_stats_tools AS rt, "
                        f"{hub_db}.{db_prefix}resources AS res "
                        f"WHERE res.id = rt.resid AND res.published = 1 "
                        f"AND period = %s AND datetime = %s "
                        f"ORDER BY cnt DESC",
                        (period, dthis))
                    rank = 1
                    for title, resid, cnt in cur.fetchall():
                        cur.execute(
                            f"INSERT INTO {hub_db}.{db_prefix}stats_topvals "
                            f"(top, datetime, period, rank, name, value) "
                            f"VALUES (%s, %s, %s, %s, %s, %s)",
                            (top, dthis, period, rank, f"{resid} ~ {title}", cnt or 0))
                        rank += 1
        log.info("[gen-tool-toplists] done")
        return 0
    finally:
        conn.close()

def cmd_gen_tool_toplists(args):
    return do_gen_tool_toplists(args.yearmonth, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# middleware-wall / middleware-cpu  (copy walltime/cputime from middleware
#                                    sessionlog+joblog into metrics.toolstart;
#                                    direct ports of the two Perl scripts)
# ---------------------------------------------------------------------------

# Original Perl scripts (xlogfix_middleware_wall.pl, xlogfix_middleware_cpu.pl)
# do a sorted-stream two-pointer merge between hub joblog×sessionlog and the
# metrics toolstart table.  In SQL terms this is:
#   - INSERT new toolstart rows for joblog records that don't have a matching
#     (datetime, user, ip) row already.
#   - UPDATE the walltime/cputime where the existing row had a negative
#     ("incomplete") value and the joblog now has a positive one.
# Two statements replace ~150 lines of Perl streaming.
#
# Note: toolstart.walltime / .cputime are FLOAT UNSIGNED on this schema, so
# the Perl's "-1 = unknown" sentinel was already broken at the SQL layer
# (the value gets coerced to 0 on insert in lenient mode, or rejected in
# strict mode).  The UPDATE branch `WHERE walltime < 0` therefore never
# fires in practice but is preserved here for fidelity.

# users that must not be counted as tool sessions (script-execution accounts)
_MIDDLEWARE_USER_FILTER = (
    "AND s.username <> 'gridstat' "
    "AND s.username NOT LIKE 'hctest%' "
)

def _do_middleware_copy(metric, *, dry_run=False):
    """Common implementation for both middleware-wall and middleware-cpu.

    metric is the joblog column to copy and the toolstart column to
    write — 'walltime' or 'cputime'.  Two statements per call:
    INSERT new rows, then UPDATE incomplete ones.
    """
    if metric not in ("walltime", "cputime"):
        raise ValueError(f"middleware_copy: unknown metric {metric!r}")

    cfg = db_config()
    hub_db     = cfg.get('hub_db', '')
    metrics_db = cfg.get('metrics_db', '')
    if not hub_db or not metrics_db:
        msg = f"[middleware-{metric}] missing hub_db / metrics_db in access.cfg"
        log.info(msg)
        return 2

    # Build the INSERT — only the metric column varies between wall/cpu.
    # Perl: `int($x + 0.5)` — round-half-up.  MariaDB's ROUND() on a DOUBLE
    # column uses banker's rounding (round-half-to-even) so 200.5 → 200,
    # which diverges from the legacy 201.  FLOOR(x + 0.5) reproduces the
    # Perl semantics exactly.
    #
    # wall.pl and cpu.pl differ in three ways:
    #   * wall.pl filters out joblog.event = '[waiting]'; cpu.pl does not
    #   * wall.pl INSERTs missing rows; cpu.pl only UPDATEs existing rows
    #     ("Do nothing as are just importing CPUtimes" — legacy comment)
    #   * wall.pl UPDATE condition is `t.walltime < 0 AND j.walltime > 0`,
    #     cpu.pl is `t.cputime <= 0 AND j.cputime > 0`  (catches cputime=0 too)
    if metric == "walltime":
        metric_select = (
            "CASE WHEN j.walltime < 0 THEN -1 "
            "     ELSE FLOOR(j.walltime + 0.5) END"
        )
        join_extra = " AND j.event <> '[waiting]'"
        update_check = "t.walltime < 0 AND j.walltime > 0"
        do_insert = True
    else:  # cputime
        metric_select = (
            "CASE WHEN j.cputime < 0 THEN -1 "
            "     ELSE FLOOR(j.cputime + 0.5) END"
        )
        join_extra = ""   # cpu.pl does not filter [waiting]
        update_check = "t.cputime <= 0 AND j.cputime > 0"
        do_insert = False

    insert_sql = (
        f"INSERT INTO {metrics_db}.toolstart "
        f"(datetime, success, user, ip, tool, execunit, {metric}) "
        f"SELECT j.start, '1', s.username, s.remoteip, s.appname, s.exechost, "
        f"       {metric_select} "
        f"FROM {hub_db}.joblog AS j "
        f"INNER JOIN {hub_db}.sessionlog AS s ON j.sessnum = s.sessnum "
        f"LEFT JOIN {metrics_db}.toolstart AS t "
        f"       ON t.datetime = j.start AND t.user = s.username AND t.ip = s.remoteip "
        f"WHERE t.id IS NULL "
        f"{_MIDDLEWARE_USER_FILTER}"
        f"{join_extra}"
    )
    update_sql = (
        f"UPDATE {metrics_db}.toolstart t "
        f"INNER JOIN {hub_db}.joblog j ON j.start = t.datetime "
        f"INNER JOIN {hub_db}.sessionlog s "
        f"       ON j.sessnum = s.sessnum AND s.username = t.user AND s.remoteip = t.ip "
        f"SET t.{metric} = FLOOR(j.{metric} + 0.5) "
        f"WHERE {update_check} "
        f"{_MIDDLEWARE_USER_FILTER}"
        f"{join_extra}"
    )

    if dry_run:
        if do_insert:
            log.info(f"  [dry-run] INSERT: would scan joblog×sessionlog vs toolstart")
        log.info(f"  [dry-run] UPDATE: {metric} where existing row has bad value and joblog has > 0")
        return 0

    conn = _open_db()
    try:
        with conn.cursor() as cur:
            inserted = 0
            if do_insert:
                cur.execute(insert_sql)
                inserted = cur.rowcount
            cur.execute(update_sql)
            updated = cur.rowcount
        log.info(f"[middleware-{metric}] inserted {inserted} new toolstart row(s), "
            f"updated {metric} on {updated}")
        return 0
    finally:
        conn.close()

def do_middleware_wall(*, dry_run=False):
    """Direct port of xlogfix_middleware_wall.pl — copy joblog.walltime
    into metrics.toolstart, inserting missing rows."""
    return _do_middleware_copy("walltime", dry_run=dry_run)

def do_middleware_cpu(*, dry_run=False):
    """Direct port of xlogfix_middleware_cpu.pl — copy joblog.cputime
    into metrics.toolstart, inserting missing rows."""
    return _do_middleware_copy("cputime", dry_run=dry_run)

def cmd_middleware_wall(args):
    return do_middleware_wall(dry_run=args.dry_run)

def cmd_middleware_cpu(args):
    return do_middleware_cpu(dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# logfix-session  (port of logfix_session.pl — web → websessions coalescer)
# ---------------------------------------------------------------------------

_SESSION_INACTIVE_SECS = 1800

def _iphost_jobs(conn_write, s_id, ip, host, dstart, dstop):
    """Direct port of iphost_jobs() in logfix_session.pl.

    Find successful toolstart rows for the session's IP/host in
    [dstart, dstop + 1799s], stamp their sessionid, return the count.
    """
    if not ip and not host:
        return 0
    where_parts = [
        "datetime >= %s",
        "UNIX_TIMESTAMP(datetime) <= UNIX_TIMESTAMP(%s) + 1799",
        "success = '1'",
    ]
    params = [dstart, dstop]
    if ip and host:
        where_parts.append("(ip = %s OR host = %s)")
        params.extend([ip, host])
    elif ip:
        where_parts.append("ip = %s")
        params.append(ip)
    else:
        where_parts.append("host = %s")
        params.append(host)
    where = " AND ".join(where_parts)

    with conn_write.cursor() as cur:
        cur.execute(f"SELECT id FROM toolstart WHERE {where}", params)
        ids = [r[0] for r in cur.fetchall()]
        if not ids:
            return 0
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(
            f"UPDATE toolstart SET sessionid = %s WHERE id IN ({placeholders})",
            [s_id, *ids],
        )
    return len(ids)


def _emit_websession(conn_write, s_id, s_datetime, s_ip, s_host,
                     s_duration, s_domain, s_jobs, s_webevents, event_ids):
    """INSERT IGNORE the websessions row and UPDATE web.sessionid for events."""
    with conn_write.cursor() as cur:
        cur.execute(
            "INSERT IGNORE INTO websessions "
            "(id, datetime, ip, host, duration, domain, jobs, webevents) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (s_id, s_datetime, s_ip or "", s_host or "",
             s_duration, s_domain or "", s_jobs, s_webevents),
        )
        if event_ids:
            placeholders = ",".join(["%s"] * len(event_ids))
            cur.execute(
                f"UPDATE web SET sessionid = %s WHERE id IN ({placeholders})",
                [s_id, *event_ids],
            )


def do_logfix_session(month=None, *, dry_run=False):
    """Direct port of logfix_session.pl.

    Walks the 4 fixed week ranges (day 1-8, 9-16, 17-24, 25-1 of next month);
    in each, streams web rows ordered by (ip, host, datetime) and emits a
    websessions row whenever IP/host changes or there is a >1800s gap.
    Bug-for-bug quirks preserved:
      * 'video' tracking is dead in the Perl — variables exist but never
        update, so the second timeout clause always fires when the gap
        condition does.
      * s_webevents is reset to 0 on session start but never incremented;
        always 0 in INSERTs.
      * The last in-flight session of week 3 is never flushed (loop ends
        without an emit if state is still set).
      * INSERT IGNORE on websessions, so duplicate ids are silently dropped.
    """
    import pymysql.cursors

    if month:
        try:
            y, m = month.split("-")
            year = int(y); mon = int(m)
        except Exception:
            raise ValueError(
                f"logfix-session: bad month {month!r}; expected YYYY-MM")
    else:
        now = datetime.now()
        year, mon = now.year, now.month

    # Build the 4 week ranges exactly as the Perl does (note the trailing
    # week crosses month/year boundaries; we keep the same integer math).
    weekbegin = [f"{year:04d}-{mon:02d}-01"]
    weekend   = []
    firstday    = 1
    lastweekday = firstday + 7
    weekend.append(f"{year:04d}-{mon:02d}-{lastweekday:02d}")
    cur_y, cur_m = year, mon
    for i in range(1, 4):
        firstday += 8
        weekbegin.append(f"{cur_y:04d}-{cur_m:02d}-{firstday:02d}")
        if i < 3:
            lastweekday = firstday + 7
            weekend.append(f"{cur_y:04d}-{cur_m:02d}-{lastweekday:02d}")
        else:
            if cur_m > 11:
                cur_m = 1
                cur_y += 1
            else:
                cur_m += 1
            weekend.append(f"{cur_y:04d}-{cur_m:02d}-01")

    log.info(f"[logfix-session] month={year:04d}-{mon:02d}")
    for i in range(4):
        log.info(f"  week {i}: {weekbegin[i]} .. {weekend[i]}")

    if dry_run:
        log.info("  [dry-run] not executing")
        return 0

    metrics_db = db_config().get('metrics_db', '')
    if not metrics_db:
        log.info("[logfix-session] missing metrics_db in access.cfg")
        return 2

    conn_read  = _open_db(metrics_db)
    conn_write = _open_db(metrics_db)

    total_sessions = 0
    total_events   = 0
    total_jobs     = 0

    try:
        with conn_write.cursor() as cur:
            cur.execute("SELECT MAX(id) FROM websessions")
            row = cur.fetchone()
            s_id = int(row[0]) if row and row[0] else 0

        # Session state spans weeks: Perl declares state vars at script scope
        # so an in-flight session at the end of one week can be flushed by an
        # IP change in the next.  We init once, not per-iteration.
        s_datetime       = None          # None = no active session
        s_datetimeint    = 0
        s_ip             = ""
        s_host           = ""
        s_domain         = ""
        s_webevents      = 0
        s_videoend       = 0
        s_events         = []
        prev_datetime    = None
        prev_datetimeint = 0

        for w in range(4):
            select_sql = (
                "SELECT id, datetime, content, ip, host, domain, "
                "       UNIX_TIMESTAMP(datetime) "
                "FROM web "
                "WHERE datetime >= %s AND datetime < %s "
                "  AND (sessionid = '0' OR sessionid IS NULL) "
                "  AND (ip <> '' OR (host <> '' AND host <> '?' AND host IS NOT NULL)) "
                "ORDER BY ip, host, datetime"
            )

            week_sessions    = 0
            week_events      = 0

            cur_read = conn_read.cursor(pymysql.cursors.SSCursor)
            try:
                cur_read.execute(select_sql, (weekbegin[w], weekend[w]))
                for row in cur_read:
                    rid, dt, content, ip, host, domain, dtint = row
                    ip     = ip or ""
                    host   = host or ""
                    domain = domain or ""
                    dtint  = int(dtint) if dtint is not None else 0

                    # End-of-session check.  Perl operator precedence:
                    #   $s_datetime && ($s_ip && $s_ip ne $ip)
                    #     || (!$s_ip && $s_host && $s_host ne $host)
                    end_found = False
                    if s_datetime is not None and (
                        (s_ip and s_ip != ip)
                        or (not s_ip and s_host and s_host != host)
                    ):
                        end_found = True
                    elif (s_ip or s_host) \
                         and (dtint - prev_datetimeint > _SESSION_INACTIVE_SECS) \
                         and (dtint - s_videoend       > _SESSION_INACTIVE_SECS):
                        end_found = True

                    if end_found:
                        if s_videoend > prev_datetimeint:
                            prev_datetimeint = s_videoend
                        s_duration = prev_datetimeint - s_datetimeint
                        s_id += 1
                        n_jobs = _iphost_jobs(
                            conn_write, s_id, s_ip, s_host,
                            s_datetime, prev_datetime)
                        _emit_websession(
                            conn_write, s_id, s_datetime,
                            s_ip, s_host, s_duration, s_domain,
                            n_jobs, s_webevents, s_events)
                        week_sessions += 1
                        week_events   += len(s_events)
                        total_jobs    += n_jobs
                        s_datetime = None

                    if s_datetime is None:
                        s_webevents  = 0
                        s_videoend   = 0
                        s_datetime   = dt
                        s_datetimeint = dtint
                        s_ip      = ""
                        s_host    = ""
                        s_domain  = ""
                        s_events  = []

                    if not s_ip and ip:
                        s_ip = ip
                    if not s_host and host:
                        s_host = host
                    if not s_domain and domain:
                        s_domain = domain

                    prev_datetime    = dt
                    prev_datetimeint = dtint
                    s_events.append(rid)
            finally:
                cur_read.close()

            log.info(f"  week {w}: emitted {week_sessions} session(s), "
                f"stamped {week_events} web event(s)")
            total_sessions += week_sessions
            total_events   += week_events

        log.info(f"[logfix-session] total: {total_sessions} session(s), "
            f"{total_events} web event(s), {total_jobs} toolstart job(s) linked")
        return 0
    finally:
        conn_write.close()
        conn_read.close()


def cmd_logfix_session(args):
    return do_logfix_session(args.month, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# summarize-month  (port of xlogfix_summary.php — the period summary table)
#
# Writes three summary tables: summary_user_vals (rowids 1,2,3,4,6,7,8),
# summary_simusage_vals (rowids 1..10), and summary_misc_vals (rowids 1..8).
# Each cell is keyed by (rowid, colid, datetime, period); the writer is a
# DELETE + INSERT pair so the run is idempotent.
#
# Known carry-over quirks preserved bug-for-bug for A/B parity:
#   * int_users rowid=3 (registered counterpart for interactive users) uses
#     a userlogin_lite query with NO date filter — values inflate as the
#     userlogin_lite table grows from prior runs.  Documented in CLAUDE.md.
#   * The same "no date filter" shape applies to int_users rowid=4 download
#     counterpart.
#   * jos_xprofiles_metrics reflects the current state of profiles, not the
#     state at activity time (rebuild on each summary by import-hub-data).
# ---------------------------------------------------------------------------

SUMMARY_PERIODS_DEFAULT = (
    PERIOD_ROLLING_12, PERIOD_MONTH, PERIOD_CAL_YEAR,
    PERIOD_QUARTER,    PERIOD_FISCAL_YR, PERIOD_ALL_TIME,
)

SUMMARY_SECTIONS = (
    "reg", "int", "dl", "total", "sim", "sim-usage", "misc",
)

# Org bucket lists (educational + "non-other" = edu + gov + industry).
_EDU_ORGTYPES = (
    "universityundergraduate", "universitygraduate", "universityfaculty",
    "universitystaff", "precollegestudent", "precollegefacultystaff",
    "university", "educational", "precollege",
)
_NON_OTHER_ORGTYPES = _EDU_ORGTYPES + ("government", "industry")
_EDU_ORGTYPES_SQL       = '"' + '","'.join(_EDU_ORGTYPES) + '"'
_NON_OTHER_ORGTYPES_SQL = '"' + '","'.join(_NON_OTHER_ORGTYPES) + '"'

# valfmt pattern for the standard 11-column residency+orgtype block.
_USERVAL_FMTS = {1:1, 2:1, 3:2, 4:2, 5:2, 6:2, 7:1, 8:2, 9:2, 10:2, 11:2}


def _summary_get_dates(dthis_, period):
    """Mirror get_dates() in func_misc.php — accept YYYY-MM-DD or YYYY-MM
    (DD must not be '00').  Returns (dstart, dstop, dthis_zero_day_str)."""
    m1 = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", dthis_)
    m2 = re.fullmatch(r"(\d{4})-(\d{2})",         dthis_)
    if m1 and m1.group(3) != "00":
        yearmonth = f"{m1.group(1)}-{m1.group(2)}"
        dthis_zero = f"{m1.group(1)}-{m1.group(2)}-00"
    elif m2:
        yearmonth = m2.group(0)
        dthis_zero = f"{m2.group(1)}-{m2.group(2)}-00"
    else:
        raise ValueError(f"summarize-month: invalid date {dthis_!r}")
    dstart, dstop = period_dates(yearmonth, period)
    return dstart, dstop, dthis_zero


def _summary_rebuild_userlogin_lite(cur, metrics_db):
    """DROP/CREATE/INDEX userlogin_lite from userlogin where action IN
    (login, simulation).  Done once at the start of a summary run."""
    cur.execute(f"DROP TABLE IF EXISTS {metrics_db}.userlogin_lite")
    cur.execute(
        f"CREATE TABLE {metrics_db}.userlogin_lite "
        f"SELECT * FROM {metrics_db}.userlogin "
        f'WHERE action = "login" OR action = "simulation"'
    )
    cur.execute(f"ALTER TABLE {metrics_db}.userlogin_lite ADD INDEX (`uidNumber`)")
    cur.execute(f"ALTER TABLE {metrics_db}.userlogin_lite ADD INDEX (datetime, `user`)")


def _summary_get_rappture_tools(cur, hub_db, db_prefix):
    """Build a comma-quoted alias list for the appname IN clause used by
    sim_usage.  Ports get_rappture_tools() + get_tool_versions_aliases().

    Step 1: seed with "workspace" and every directory found under /apps
    (excluding /apps/share and /apps/share64) that has a tool.xml.
    Step 2: expand by adding every distinct jos_tool_version.instance whose
    toolname is in the seed list (excluding %\\_dev instances).
    Returns the SQL fragment ready to drop inside `appname IN (...)`.
    """
    seeds = ["workspace"]
    try:
        out = subprocess.run(
            ["/bin/bash", "-c",
             "find /apps -maxdepth 4 -path /apps/share -prune "
             "-o -path /apps/share64 -prune "
             "-o -name tool.xml -print 2>/dev/null"],
            capture_output=True, text=True, timeout=60,
        )
        for line in out.stdout.splitlines():
            parts = line.split("/")
            # /apps/<tool>/...  → index 2 is the tool dir
            if len(parts) >= 3 and parts[2]:
                seeds.append(parts[2])
    except (OSError, subprocess.SubprocessError) as e:
        # /apps may be unmounted, find may be missing, the shell may
        # have failed — log so the summary's tool list isn't quietly
        # truncated to just ["workspace"] with no signal.
        log.warning(f"[summarize-month] /apps scan failed ({e}); "
                    f"tool seeds limited to {seeds}")
    # Dedup, preserving order.
    seen = set()
    seeds = [s for s in seeds if not (s in seen or seen.add(s))]
    aliases = list(seeds)

    if seeds:
        placeholders = ",".join(["%s"] * len(seeds))
        cur.execute(
            f"SELECT DISTINCT instance FROM {hub_db}.{db_prefix}tool_version "
            f"WHERE toolname IN ({placeholders}) AND instance NOT LIKE %s",
            (*seeds, "%\\_dev"),
        )
        for (inst,) in cur.fetchall():
            if inst:
                aliases.append(inst)
    # Quote and join.
    return '"' + '","'.join(aliases) + '"'


def _summary_continents(cur, metrics_db):
    """Return {'AS': '"CN","JP",...', 'EU': '...', 'NOT_AS_EU': '...'} —
    each value ready to drop inside an `IN (...)` clause.  Cached once
    per run; the PHP rebuilds them per cell."""
    out = {}
    for label, where in (
        ('AS',        'continent="AS"'),
        ('EU',        'continent="EU"'),
        ('NOT_AS_EU', 'continent NOT IN ("EU","AS")'),
    ):
        cur.execute(
            f"SELECT country FROM {metrics_db}.country_continent WHERE {where}")
        out[label] = '"' + '","'.join(
            r[0] for r in cur.fetchall() if r[0]
        ) + '"'
    return out


def _summary_build_login_ips(cur, metrics_db, dstart, dstop):
    """Materialise the registered-user IP set into login_ips_tmp and
    return a SELECT subquery ready to drop inside `ip NOT IN (...)`.
    Seeds with 127.0.0.1.  Ports build_login_ips_table() in func_misc."""
    cur.execute(f"DROP TEMPORARY TABLE IF EXISTS {metrics_db}.login_ips_tmp")
    cur.execute(
        f"CREATE TEMPORARY TABLE {metrics_db}.login_ips_tmp "
        f"(ip VARCHAR(45), INDEX (ip))")
    cur.execute(
        f"INSERT INTO {metrics_db}.login_ips_tmp (ip) VALUES (%s)",
        ("127.0.0.1",))
    cur.execute(
        f"SELECT DISTINCT ip FROM {metrics_db}.userlogin_lite "
        f'WHERE action IN ("login","simulation") '
        f"  AND datetime > %s AND datetime < %s",
        (dstart, dstop))
    rows = [r[0] for r in cur.fetchall() if r[0]]
    if rows:
        # Batch INSERT in chunks to keep packet size sane.
        for i in range(0, len(rows), 5000):
            chunk = rows[i:i+5000]
            placeholders = ",".join(["(%s)"] * len(chunk))
            cur.execute(
                f"INSERT INTO {metrics_db}.login_ips_tmp (ip) VALUES {placeholders}",
                chunk)
    return f"SELECT ip FROM {metrics_db}.login_ips_tmp"


def _summary_build_dl_users_period(cur, metrics_db, dstart, dstop):
    """Materialise dl_users_period_tmp — DISTINCT (ip, host, ipcountry) of
    websessions that have at least one matching dnload=1 web row in the
    period window, with the standard websession-duration/jobs/login_ips
    filter applied.  JOIN drives from the small web(dnload=1) side."""
    cur.execute(f"DROP TEMPORARY TABLE IF EXISTS {metrics_db}.dl_users_period_tmp")
    cur.execute(
        f"CREATE TEMPORARY TABLE {metrics_db}.dl_users_period_tmp "
        f"SELECT DISTINCT ws.ip, ws.host, ws.ipcountry "
        f"FROM {metrics_db}.web AS w "
        f"INNER JOIN {metrics_db}.websessions AS ws ON ws.id = w.sessionid "
        f"WHERE w.dnload = 1 "
        f"  AND ws.datetime > %s AND ws.datetime < %s "
        f'  AND ws.duration >= "0" AND ws.duration < "900" AND ws.jobs = "0" '
        f"  AND ws.ip NOT IN (SELECT ip FROM {metrics_db}.login_ips_tmp)",
        (dstart, dstop))
    cur.execute(
        f"ALTER TABLE {metrics_db}.dl_users_period_tmp ADD INDEX (ipcountry)")


def _summary_build_download_sessions(cur, metrics_db, dstart, dstop):
    """Materialise download_sessions_tmp — DISTINCT sessionid of every
    web row with dnload=1 in the period.  Returns False if empty."""
    cur.execute(f"DROP TEMPORARY TABLE IF EXISTS {metrics_db}.download_sessions_tmp")
    cur.execute(
        f"CREATE TEMPORARY TABLE {metrics_db}.download_sessions_tmp "
        f"(id INT, INDEX (id))")
    cur.execute(
        f"INSERT INTO {metrics_db}.download_sessions_tmp (id) "
        f"SELECT DISTINCT sessionid FROM {metrics_db}.web "
        f"WHERE dnload = 1 AND datetime > %s AND datetime < %s "
        f"  AND sessionid IS NOT NULL",
        (dstart, dstop))
    if cur.rowcount == 0:
        return False
    return True


def _summary_get_ip_list(cur, sql, params=()):
    """Run a SELECT ip … query, prepend '127.0.0.1', return a comma-quoted
    list for IN ().  Returns '' (empty string) if nothing matches."""
    cur.execute(sql, params)
    ips = ['127.0.0.1'] + [r[0] for r in cur.fetchall() if r[0]]
    return '"' + '","'.join(ips) + '"'


def _summary_delete_record(cur, table, rowid, colid, dthis, period):
    cur.execute(
        f"DELETE FROM {table} WHERE rowid = %s AND colid = %s "
        f"AND datetime = %s AND period = %s",
        (rowid, colid, dthis, period))


def _summary_insert_record(cur, table, rowid, colid, dthis, period, value, valfmt):
    cur.execute(
        f"INSERT INTO {table} VALUES (%s, %s, %s, %s, %s, %s)",
        (rowid, colid, dthis, period, value, valfmt))


def _summary_write_cell(cur, table, rowid, colid, dthis, period, value, valfmt):
    _summary_delete_record(cur, table, rowid, colid, dthis, period)
    _summary_insert_record(cur, table, rowid, colid, dthis, period, value, valfmt)


def _summary_11col_cells(country_col, orgtype_col, continents):
    """Return [(colid, extra_where_predicate), ...] for the standard
    11-column residency+orgtype layout — same for reg_users and sim_users."""
    return [
        (1,  ""),
        (2,  f'{country_col} <> ""'),
        (3,  f'{country_col} = "US"'),
        (4,  f'{country_col} IN ({continents["AS"]})'),
        (5,  f'{country_col} IN ({continents["EU"]})'),
        (6,  f'{country_col} <> "" AND {country_col} IN ({continents["NOT_AS_EU"]}) '
             f'AND {country_col} <> "US"'),
        (7,  f'{orgtype_col} <> ""'),
        (8,  f'{orgtype_col} IN ({_EDU_ORGTYPES_SQL})'),
        (9,  f'{orgtype_col} = "industry"'),
        (10, f'{orgtype_col} = "government"'),
        (11, f'{orgtype_col} NOT IN ({_NON_OTHER_ORGTYPES_SQL}) '
             f'AND {orgtype_col} <> ""'),
    ]


def _summary_reg_users(cur, hub_db, metrics_db, db_prefix,
                      dthis, dstart, dstop, period, continents):
    """summary_user_vals rowid=6 — registered users × 11 cols (port of reg_users).

    col=1 (Total) is a no-JOIN count from userlogin_lite — matches the
    legacy PHP which queries userlogin_lite directly without the
    xprofiles_metrics JOIN.  cols 2..11 add the xprofiles_metrics JOIN
    for residency / orgtype filters.
    """
    table = f"{metrics_db}.summary_user_vals"
    rowid = 6
    base_join = (
        f"FROM {metrics_db}.userlogin_lite AS ul, "
        f"     {metrics_db}.{db_prefix}xprofiles_metrics AS u "
        f"WHERE u.username = ul.user "
        f"  AND ul.datetime > %s AND ul.datetime < %s "
        f'  AND ul.action IN ("login","simulation")'
    )
    base_no_join = (
        f"FROM {metrics_db}.userlogin_lite "
        f"WHERE datetime > %s AND datetime < %s "
        f'  AND action IN ("login","simulation")'
    )
    def cell(extra, joined=True):
        if joined:
            sql = f"SELECT COUNT(DISTINCT ul.user) {base_join}"
        else:
            sql = f"SELECT COUNT(DISTINCT user) {base_no_join}"
        if extra:
            sql += f" AND {extra}"
        cur.execute(sql, (dstart, dstop))
        r = cur.fetchone()
        return (r[0] or 0) if r else 0

    for colid, extra in _summary_11col_cells("u.countryresident", "u.orgtype", continents):
        joined = colid != 1
        _summary_write_cell(cur, table, rowid, colid, dthis, period,
                            cell(extra, joined), _USERVAL_FMTS[colid])


def _summary_sim_users(cur, metrics_db, dthis, dstart, dstop, period, continents):
    """summary_user_vals rowid=2 — simulation users × 11 cols.

    Reads toolstart directly; countryresident / orgtype are already filled
    in by fill-user-info, so no JOIN needed."""
    table = f"{metrics_db}.summary_user_vals"
    rowid = 2
    base = (
        f"FROM {metrics_db}.toolstart "
        f"WHERE success = 1 "
        f"  AND datetime > %s AND datetime < %s"
    )
    def cell(extra):
        sql = f"SELECT COUNT(DISTINCT user) {base}"
        if extra:
            sql += f" AND {extra}"
        cur.execute(sql, (dstart, dstop))
        r = cur.fetchone()
        return (r[0] or 0) if r else 0

    for colid, extra in _summary_11col_cells("countryresident", "orgtype", continents):
        _summary_write_cell(cur, table, rowid, colid, dthis, period,
                            cell(extra), _USERVAL_FMTS[colid])


def _summary_total_users(cur, metrics_db, dthis, period):
    """summary_user_vals rowid=1 — derived per-cell SUM of rows 6, 7, 8
    after they have been written.  Mirrors total_users() exactly."""
    table = f"{metrics_db}.summary_user_vals"
    rowid = 1
    for colid in range(1, 12):
        valfmt = 1 if colid in (1, 2, 7) else 2
        cur.execute(
            f"SELECT SUM(value) FROM {table} "
            f"WHERE valfmt = %s AND colid = %s AND period = %s "
            f"  AND datetime = %s AND rowid IN (6,7,8)",
            (valfmt, colid, period, dthis))
        r = cur.fetchone()
        v = (r[0] or 0) if r else 0
        _summary_write_cell(cur, table, rowid, colid, dthis, period, v, valfmt)


_ORG_CLASS_BUCKET = {1: "edu", 2: "ind", 3: "gov", 6: "other"}


def _summary_int_users(cur, metrics_db, dthis, dstart, dstop, period,
                      continents, login_ips_subq):
    """summary_user_vals rowid=7 (unregistered interactive) + rowid=3
    (registered-user counterpart) × 11 cols.

    KNOWN BUG (carried over from PHP for A/B parity): the rowid=3
    userlogin_lite intersect query has no date filter, so the row=3
    counts inflate as userlogin_lite grows across runs.
    """
    table = f"{metrics_db}.summary_user_vals"

    # --- helper: residency-style cell for rowid=7 (websessions side) ---
    def ws_cell_7(country_extra):
        sql = (
            f"SELECT COUNT(DISTINCT ip, host) AS users "
            f"FROM {metrics_db}.websessions "
            f"WHERE datetime > %s AND datetime < %s "
            f'  AND duration >= "900" AND jobs = "0" '
            f"  AND ip NOT IN ({login_ips_subq}) "
        )
        if country_extra:
            sql += f" AND {country_extra}"
        cur.execute(sql, (dstart, dstop))
        r = cur.fetchone()
        return (r[0] or 0) if r else 0

    # --- helper: registered-counterpart delta for rowid=3 ---
    def ul_delta_3(country_extra):
        # IP list of interactive sessions matching country_extra (no login_ips filter).
        ip_sql = (
            f"SELECT DISTINCT ip FROM {metrics_db}.websessions "
            f"WHERE datetime > %s AND datetime < %s "
            f'  AND jobs = "0" AND duration >= "900"'
        )
        if country_extra:
            ip_sql += f" AND {country_extra}"
        ip_list = _summary_get_ip_list(cur, ip_sql, (dstart, dstop))
        if not ip_list:
            return 0
        # Bug-for-bug: no date filter here.
        cur.execute(
            f"SELECT COUNT(DISTINCT user) FROM {metrics_db}.userlogin_lite AS ul "
            f'WHERE (ul.action = "login" OR ul.action = "simulation") '
            f"  AND ul.ip IN ({ip_list})")
        r = cur.fetchone()
        return (r[0] or 0) if r else 0

    # --- residency cols 1..6 ---
    residency = [
        (1, ""),
        (2, 'ipcountry <> "" AND ipcountry <> "-"'),
        (3, 'ipcountry = "US"'),
        (4, f'ipcountry IN ({continents["AS"]})'),
        (5, f'ipcountry IN ({continents["EU"]})'),
        (6, f'ipcountry IN ({continents["NOT_AS_EU"]}) AND ipcountry <> "US"'),
    ]
    for colid, country_extra in residency:
        valfmt = _USERVAL_FMTS[colid]
        v7 = ws_cell_7(country_extra)
        _summary_write_cell(cur, table, 7, colid, dthis, period, v7, valfmt)
        v3 = v7 + ul_delta_3(country_extra)
        _summary_write_cell(cur, table, 3, colid, dthis, period, v3, valfmt)

    # --- organization cols 7..11: one GROUP BY query each side ---
    # rowid=7: websession-side bucket counts.
    cur.execute(
        f"SELECT COUNT(DISTINCT ws.ip, ws.host) AS users, dc.class AS class "
        f"FROM {metrics_db}.websessions AS ws "
        f"LEFT OUTER JOIN {metrics_db}.domainclass AS dc ON ws.domain = dc.domain "
        f"LEFT OUTER JOIN {metrics_db}.domainclasses AS dcs ON dc.class = dcs.class "
        f'WHERE ws.duration >= "900" AND ws.jobs = "0" '
        f"  AND ws.datetime > %s AND ws.datetime < %s "
        f'  AND dc.class > "0" AND dc.class <> "4" '
        f"  AND ws.ip NOT IN ({login_ips_subq}) "
        f"GROUP BY class ORDER BY class",
        (dstart, dstop))
    buckets7 = {"edu": 0, "ind": 0, "gov": 0, "other": 0}
    identified7 = 0
    for cnt, cls in cur.fetchall():
        cnt = int(cnt or 0)
        cls_str = str(cls) if cls is not None else ""
        if cls_str in ("1", "2", "3", "6"):
            buckets7[_ORG_CLASS_BUCKET[int(cls_str)]] = cnt
        identified7 += cnt
    _summary_write_cell(cur, table, 7, 7,  dthis, period, identified7,       1)
    _summary_write_cell(cur, table, 7, 8,  dthis, period, buckets7["edu"],   2)
    _summary_write_cell(cur, table, 7, 9,  dthis, period, buckets7["ind"],   2)
    _summary_write_cell(cur, table, 7, 10, dthis, period, buckets7["gov"],   2)
    _summary_write_cell(cur, table, 7, 11, dthis, period, buckets7["other"], 2)

    # rowid=3: registered-user side — JOIN userlogin_lite to websessions on ip.
    cur.execute(
        f"SELECT COUNT(DISTINCT ul.user) AS users, dc.class AS class "
        f"FROM {metrics_db}.userlogin_lite AS ul "
        f"LEFT OUTER JOIN {metrics_db}.websessions AS ws ON ul.ip = ws.ip "
        f"LEFT OUTER JOIN {metrics_db}.domainclass  AS dc ON ws.domain = dc.domain "
        f"LEFT OUTER JOIN {metrics_db}.domainclasses AS dcs ON dc.class = dcs.class "
        f'WHERE ws.jobs = "0" AND ws.duration >= "900" '
        f"  AND ws.datetime > %s AND ws.datetime < %s "
        f'  AND dc.class > "0" AND dc.class <> "4" '
        f'  AND (ul.action = "login" OR ul.action = "simulation") '
        f"GROUP BY class ORDER BY class",
        (dstart, dstop))
    buckets3 = {"edu": 0, "ind": 0, "gov": 0, "other": 0}
    identified3 = 0
    for cnt, cls in cur.fetchall():
        cnt = int(cnt or 0)
        cls_str = str(cls) if cls is not None else ""
        if cls_str in ("1", "2", "3", "6"):
            buckets3[_ORG_CLASS_BUCKET[int(cls_str)]] = cnt
        identified3 += cnt
    _summary_write_cell(cur, table, 3, 7,  dthis, period, identified3,       1)
    _summary_write_cell(cur, table, 3, 8,  dthis, period, buckets3["edu"],   2)
    _summary_write_cell(cur, table, 3, 9,  dthis, period, buckets3["ind"],   2)
    _summary_write_cell(cur, table, 3, 10, dthis, period, buckets3["gov"],   2)
    _summary_write_cell(cur, table, 3, 11, dthis, period, buckets3["other"], 2)


def _summary_download_users(cur, metrics_db, dthis, dstart, dstop, period,
                           continents, login_ips_subq):
    """summary_user_vals rowid=8 (download users, websession side) +
    rowid=4 (registered-user counterpart).  Uses dl_users_period_tmp
    and download_sessions_tmp (built ahead of this call by the orchestrator).

    KNOWN BUG (carried over from PHP for A/B parity): the rowid=4
    userlogin_lite intersect query has no date filter.
    """
    table = f"{metrics_db}.summary_user_vals"

    def dl_cell_8(country_extra):
        sql = (
            f"SELECT COUNT(DISTINCT ip, host) AS users "
            f"FROM {metrics_db}.dl_users_period_tmp"
        )
        if country_extra:
            sql += f" WHERE {country_extra}"
        cur.execute(sql)
        r = cur.fetchone()
        return (r[0] or 0) if r else 0

    def ul_delta_4(country_extra):
        # Legacy 1018cc2^ uses a DIFFERENT websessions filter for rowid=4 than
        # rowid=8: rowid=4 omits both `ws.ip NOT IN (login_ips)` and the
        # `duration < 900` cap, only requiring `jobs=0 AND duration >= 0` plus
        # a dnload-matching web row.  So we can't reuse dl_users_period_tmp
        # (which bakes those filters in for rowid=8) — query directly.
        ip_sql = (
            f"SELECT DISTINCT ws.ip "
            f"FROM {metrics_db}.websessions AS ws "
            f"INNER JOIN {metrics_db}.web AS w ON w.sessionid = ws.id "
            f"WHERE w.dnload = 1 "
            f"  AND ws.datetime > %s AND ws.datetime < %s "
            f'  AND ws.duration >= "0" AND ws.jobs = "0"'
        )
        params = [dstart, dstop]
        if country_extra:
            # country_extra is phrased against the bare `ipcountry` col name
            # (works against dl_users_period_tmp).  Qualify it for websessions.
            ip_sql += " AND " + country_extra.replace("ipcountry", "ws.ipcountry")
        ip_list = _summary_get_ip_list(cur, ip_sql, params)
        if not ip_list:
            return 0
        cur.execute(
            f"SELECT COUNT(DISTINCT user) FROM {metrics_db}.userlogin_lite AS ul "
            f'WHERE (ul.action = "login" OR ul.action = "simulation") '
            f"  AND ul.ip IN ({ip_list})")
        r = cur.fetchone()
        return (r[0] or 0) if r else 0

    residency = [
        (1, ""),
        (2, 'ipcountry <> "" AND ipcountry <> "-"'),
        (3, 'ipcountry = "US"'),
        (4, f'ipcountry IN ({continents["AS"]})'),
        (5, f'ipcountry IN ({continents["EU"]})'),
        (6, f'ipcountry IN ({continents["NOT_AS_EU"]}) AND ipcountry <> "US"'),
    ]
    for colid, country_extra in residency:
        valfmt = _USERVAL_FMTS[colid]
        v8 = dl_cell_8(country_extra)
        _summary_write_cell(cur, table, 8, colid, dthis, period, v8, valfmt)
        v4 = v8 + ul_delta_4(country_extra)
        _summary_write_cell(cur, table, 4, colid, dthis, period, v4, valfmt)

    # Organization breakdown — restrict to websessions whose id is in
    # download_sessions_tmp (rebuilt fresh per period).
    has_dl_sess = _summary_build_download_sessions(cur, metrics_db, dstart, dstop)

    # rowid=8 buckets — websessions side.
    buckets8 = {"edu": 0, "ind": 0, "gov": 0, "other": 0}
    identified8 = 0
    if has_dl_sess:
        cur.execute(
            f"SELECT COUNT(DISTINCT ws.ip, ws.host) AS users, dc.class AS class "
            f"FROM {metrics_db}.websessions AS ws "
            f"LEFT OUTER JOIN {metrics_db}.domainclass AS dc ON ws.domain = dc.domain "
            f"LEFT OUTER JOIN {metrics_db}.domainclasses AS dcs ON dc.class = dcs.class "
            f'WHERE ws.duration >= "0" AND ws.duration < "900" AND ws.jobs = "0" '
            f"  AND ws.datetime > %s AND ws.datetime < %s "
            f'  AND dc.class > "0" AND dc.class <> "4" '
            f"  AND ws.ip NOT IN ({login_ips_subq}) "
            f"  AND ws.id IN (SELECT id FROM {metrics_db}.download_sessions_tmp) "
            f"GROUP BY class ORDER BY class",
            (dstart, dstop))
        for cnt, cls in cur.fetchall():
            cnt = int(cnt or 0)
            cls_str = str(cls) if cls is not None else ""
            if cls_str in ("1", "2", "3", "6"):
                buckets8[_ORG_CLASS_BUCKET[int(cls_str)]] = cnt
            identified8 += cnt
    _summary_write_cell(cur, table, 8, 7,  dthis, period, identified8,       1)
    _summary_write_cell(cur, table, 8, 8,  dthis, period, buckets8["edu"],   2)
    _summary_write_cell(cur, table, 8, 9,  dthis, period, buckets8["ind"],   2)
    _summary_write_cell(cur, table, 8, 10, dthis, period, buckets8["gov"],   2)
    _summary_write_cell(cur, table, 8, 11, dthis, period, buckets8["other"], 2)

    # rowid=4 buckets — userlogin_lite intersect via ws.ip.
    buckets4 = {"edu": 0, "ind": 0, "gov": 0, "other": 0}
    identified4 = 0
    if has_dl_sess:
        cur.execute(
            f"SELECT COUNT(DISTINCT ul.user) AS users, dc.class AS class "
            f"FROM {metrics_db}.userlogin_lite AS ul "
            f"LEFT OUTER JOIN {metrics_db}.websessions AS ws ON ul.ip = ws.ip "
            f"LEFT OUTER JOIN {metrics_db}.domainclass  AS dc ON ws.domain = dc.domain "
            f"LEFT OUTER JOIN {metrics_db}.domainclasses AS dcs ON dc.class = dcs.class "
            f'WHERE ws.jobs = "0" AND ws.duration >= "0" '
            f"  AND ws.datetime > %s AND ws.datetime < %s "
            f'  AND dc.class > "0" AND dc.class <> "4" '
            f"  AND ws.id IN (SELECT id FROM {metrics_db}.download_sessions_tmp) "
            f'  AND (ul.action = "login" OR ul.action = "simulation") '
            f"GROUP BY class ORDER BY class",
            (dstart, dstop))
        for cnt, cls in cur.fetchall():
            cnt = int(cnt or 0)
            cls_str = str(cls) if cls is not None else ""
            if cls_str in ("1", "2", "3", "6"):
                buckets4[_ORG_CLASS_BUCKET[int(cls_str)]] = cnt
            identified4 += cnt
    _summary_write_cell(cur, table, 4, 7,  dthis, period, identified4,       1)
    _summary_write_cell(cur, table, 4, 8,  dthis, period, buckets4["edu"],   2)
    _summary_write_cell(cur, table, 4, 9,  dthis, period, buckets4["ind"],   2)
    _summary_write_cell(cur, table, 4, 10, dthis, period, buckets4["gov"],   2)
    _summary_write_cell(cur, table, 4, 11, dthis, period, buckets4["other"], 2)


def _summary_sim_usage(cur, hub_db, metrics_db, dthis, dstart, dstop, period,
                      rappture_tools_sql):
    """summary_simusage_vals rowid=1..10 — counts and time aggregates."""
    table = f"{metrics_db}.summary_simusage_vals"
    colid = 1

    cur.execute(
        f"SELECT COUNT(DISTINCT user) FROM {metrics_db}.toolstart "
        f"WHERE success=1 AND datetime > %s AND datetime < %s",
        (dstart, dstop))
    sim_users = (cur.fetchone() or (0,))[0] or 0
    _summary_write_cell(cur, table, 1, colid, dthis, period, sim_users, 3)
    if not sim_users:
        return

    cur.execute(
        f"SELECT COUNT(user) FROM {metrics_db}.toolstart "
        f"WHERE success=1 AND datetime > %s AND datetime < %s",
        (dstart, dstop))
    sim_jobs = (cur.fetchone() or (0,))[0] or 0
    _summary_write_cell(cur, table, 2, colid, dthis, period, sim_jobs, 4)

    # CPU Time — Non-Rappture + Rappture (joblog side).
    cur.execute(
        f"SELECT COALESCE(SUM(cputime),0) FROM {hub_db}.sessionlog "
        f"WHERE start > %s AND start < %s "
        f"  AND appname NOT IN ({rappture_tools_sql})",
        (dstart, dstop))
    cpu_non_rapp = (cur.fetchone() or (0,))[0] or 0
    cur.execute(
        f"SELECT COALESCE(SUM(j.cputime),0) "
        f"FROM {hub_db}.joblog j, {hub_db}.sessionlog s "
        f"WHERE s.sessnum = j.sessnum "
        f"  AND s.start > %s AND s.start < %s "
        f'  AND j.event <> "[waiting]" AND j.job > 0 '
        f'  AND s.username <> "gridstat" AND s.username NOT LIKE "hctest%%" '
        f"  AND s.appname IN ({rappture_tools_sql})",
        (dstart, dstop))
    cpu_rapp = (cur.fetchone() or (0,))[0] or 0
    _summary_write_cell(cur, table, 3, colid, dthis, period,
                        cpu_non_rapp + cpu_rapp, 5)

    cur.execute(
        f"SELECT SUM(walltime) FROM {hub_db}.sessionlog "
        f"WHERE start > %s AND start < %s "
        f'  AND username <> "gridstat" AND username NOT LIKE "hctest%%"',
        (dstart, dstop))
    walltime = (cur.fetchone() or (0,))[0] or 0
    _summary_write_cell(cur, table, 4, colid, dthis, period, walltime, 5)

    cur.execute(
        f"SELECT SUM(viewtime) FROM {hub_db}.sessionlog "
        f"WHERE start > %s AND start < %s "
        f'  AND username <> "gridstat" AND username NOT LIKE "hctest%%"',
        (dstart, dstop))
    viewtime = (cur.fetchone() or (0,))[0] or 0
    _summary_write_cell(cur, table, 5, colid, dthis, period, viewtime, 5)

    cur.execute(
        f"SELECT COUNT(*) FROM ("
        f"  SELECT DISTINCT username AS users, SUM(cputime) AS total_cputime "
        f"  FROM {hub_db}.sessionlog "
        f"  WHERE start > %s AND start < %s "
        f"  GROUP BY users "
        f'  HAVING total_cputime >= "600" '
        f'    AND username <> "gridstat" AND username NOT LIKE "hctest%%"'
        f") AS SUBTABLE",
        (dstart, dstop))
    cpu_10min = (cur.fetchone() or (0,))[0] or 0
    _summary_write_cell(cur, table, 6, colid, dthis, period, cpu_10min, 3)

    avg_jobs = sim_jobs / sim_users
    _summary_write_cell(cur, table, 7, colid, dthis, period, avg_jobs, 4)

    # Average days between first and last simulation × 86400 = seconds.
    cur.execute(
        f"SELECT SUM(TO_DAYS(w.window_max) - TO_DAYS(a.alltime_min)) AS total_days "
        f"FROM ("
        f"  SELECT user, MAX(datetime) AS window_max "
        f"  FROM {metrics_db}.toolstart "
        f'  WHERE success = 1 AND datetime > %s AND datetime > "1995-01-01" '
        f"    AND datetime < %s "
        f"  GROUP BY user HAVING COUNT(*) > 1"
        f") AS w "
        f"JOIN ("
        f"  SELECT user, MIN(datetime) AS alltime_min "
        f"  FROM {metrics_db}.toolstart "
        f'  WHERE success = 1 AND datetime > "1995-01-01" '
        f"  GROUP BY user"
        f") AS a ON a.user = w.user "
        f"WHERE TO_DAYS(w.window_max) > TO_DAYS(a.alltime_min)",
        (dstart, dstop))
    total_days = int((cur.fetchone() or (0,))[0] or 0)
    avg_days = (total_days / sim_users) * 86400
    _summary_write_cell(cur, table, 8, colid, dthis, period, avg_days, 5)

    cur.execute(
        f"SELECT COUNT(*) FROM ("
        f"  SELECT DISTINCT user AS USERS, COUNT(*) AS sims "
        f"  FROM {metrics_db}.toolstart "
        f"  WHERE success=1 AND datetime > %s AND datetime < %s "
        f'  GROUP BY users HAVING sims >= 10'
        f") AS SUBTABLE",
        (dstart, dstop))
    repeat_10 = (cur.fetchone() or (0,))[0] or 0
    _summary_write_cell(cur, table, 9, colid, dthis, period, repeat_10, 3)

    # Repeat users with > 3 months between first and last simulation.
    cur.execute(
        f"SELECT COUNT(*) AS repeat_users FROM ("
        f"  SELECT w.user,"
        f"    TO_DAYS(w.window_max) - TO_DAYS(a.alltime_min) AS spread,"
        f"    TO_DAYS(a.alltime_min) AS min_days "
        f"  FROM ("
        f"    SELECT user, MAX(datetime) AS window_max "
        f"    FROM {metrics_db}.toolstart "
        f"    WHERE success = 1 AND datetime > %s AND datetime < %s "
        f"    GROUP BY user"
        f"  ) AS w "
        f"  JOIN ("
        f"    SELECT user, MIN(datetime) AS alltime_min "
        f"    FROM {metrics_db}.toolstart "
        f"    WHERE success = 1 "
        f"    GROUP BY user"
        f"  ) AS a ON a.user = w.user"
        f") AS sub "
        f"WHERE sub.spread >= 90 AND sub.min_days > 0",
        (dstart, dstop))
    repeat_3mo = (cur.fetchone() or (0,))[0] or 0
    _summary_write_cell(cur, table, 10, colid, dthis, period, repeat_3mo, 3)


def _summary_misc_usage(cur, metrics_db, db_prefix,
                       dthis, dstart, dstop, period):
    """summary_misc_vals rowid=1..8 — domains, sessions, hits, new accounts."""
    table = f"{metrics_db}.summary_misc_vals"
    colid = 1

    def one(rowid, valfmt, sql, params):
        cur.execute(sql, params)
        r = cur.fetchone()
        # Legacy db_fetch + dbquote(NULL) write empty string when SUM()
        # returns NULL on an empty window.  COUNT() never returns NULL,
        # so this only affects the SUM(duration)/SUM(hits) callsites.
        v = (r[0] if r and r[0] is not None else '')
        _summary_write_cell(cur, table, rowid, colid, dthis, period, v, valfmt)

    one(1, 1,
        f"SELECT COUNT(DISTINCT domain) FROM {metrics_db}.websessions "
        f"WHERE datetime > %s AND datetime < %s",
        (dstart, dstop))
    one(2, 1,
        f"SELECT COUNT(datetime) AS sessions FROM {metrics_db}.websessions "
        f"WHERE datetime > %s AND datetime < %s "
        f'  AND (duration >= "900" OR jobs > "0")',
        (dstart, dstop))
    one(3, 5,
        f"SELECT SUM(duration) FROM {metrics_db}.websessions "
        f"WHERE datetime > %s AND datetime < %s "
        f'  AND (duration >= "900" OR jobs > "0")',
        (dstart, dstop))
    one(4, 1,
        f"SELECT COUNT(DISTINCT ip, host) FROM {metrics_db}.websessions "
        f"WHERE datetime > %s AND datetime < %s "
        f'  AND (duration >= "0" OR jobs > "0")',
        (dstart, dstop))
    one(5, 1,
        f"SELECT COUNT(datetime) FROM {metrics_db}.websessions "
        f"WHERE datetime > %s AND datetime < %s "
        f'  AND (duration >= "0" OR jobs > "0")',
        (dstart, dstop))
    one(6, 1,
        f"SELECT COUNT(DISTINCT uidNumber) "
        f"FROM {metrics_db}.{db_prefix}xprofiles_metrics "
        f"WHERE registerDate > %s AND registerDate < %s",
        (dstart, dstop))

    # Max user logins on a single day, stored as 'N users on YYYY-MM-DD'.
    cur.execute(
        f"SELECT LEFT(datetime,10) AS day, COUNT(DISTINCT user) AS logins "
        f"FROM {metrics_db}.userlogin_lite "
        f"WHERE datetime > %s AND datetime < %s "
        f'  AND action IN ("login","simulation") '
        f"GROUP BY day ORDER BY logins DESC LIMIT 1",
        (dstart, dstop))
    row = cur.fetchone()
    if row:
        data = f"{row[1]} users on {row[0]}"
    else:
        # PHP leaves $ondate/$maxusers unset → notice-suppressed null
        # concatenation produced "0 users on " — preserve that.
        data = " users on "
    _summary_write_cell(cur, table, 7, colid, dthis, period, data, 6)

    one(8, 1,
        f"SELECT SUM(hits) FROM {metrics_db}.webhits "
        f"WHERE datetime > %s AND datetime < %s",
        (dstart, dstop))


def do_summarize_month(yearmonth=None, *, only=None, periods=None,
                       dry_run=False):
    """Port of xlogfix_summary.php.  Drops/rebuilds userlogin_lite, loads
    the rappture-tool alias set and the AS/EU/NOT-AS-EU country lists,
    then loops the six periods and writes summary_*_vals for each.

    only:    iterable subset of SUMMARY_SECTIONS (default: all)
    periods: iterable subset of period codes (default: SUMMARY_PERIODS_DEFAULT)
    """
    if not yearmonth:
        # PHP default: last month.
        today = date.today()
        if today.month == 1:
            yearmonth = f"{today.year - 1:04d}-12"
        else:
            yearmonth = f"{today.year:04d}-{today.month - 1:02d}"
    dthis_input = f"{yearmonth}-01"

    sections = tuple(only) if only else SUMMARY_SECTIONS
    bad = [s for s in sections if s not in SUMMARY_SECTIONS]
    if bad:
        raise ValueError(f"summarize-month: unknown sections {bad}; "
                         f"valid: {SUMMARY_SECTIONS}")
    period_codes = tuple(periods) if periods else SUMMARY_PERIODS_DEFAULT

    cfg = db_config()
    hub_db     = cfg.get('hub_db', '')
    metrics_db = cfg.get('metrics_db', '')
    db_prefix  = cfg.get('db_prefix', 'jos_')
    if not hub_db or not metrics_db:
        log.info("[summarize-month] missing hub_db / metrics_db in access.cfg")
        return 2

    log.info(f"[summarize-month] month={yearmonth} sections={','.join(sections)} "
        f"periods={','.join(str(p) for p in period_codes)}")

    if dry_run:
        for p in period_codes:
            dstart, dstop, dthis = _summary_get_dates(dthis_input, p)
            log.info(f"  [dry-run] period={p} dthis={dthis} {dstart} .. {dstop}")
        return 0

    conn = _open_db(metrics_db)
    try:
        with conn.cursor() as cur:
            # One-shot prep.
            log.info("[summarize-month] rebuilding userlogin_lite")
            _summary_rebuild_userlogin_lite(cur, metrics_db)

            need_sim_usage = "sim-usage" in sections
            rappture_tools_sql = ""
            if need_sim_usage:
                log.info("[summarize-month] loading rappture tool aliases")
                rappture_tools_sql = _summary_get_rappture_tools(
                    cur, hub_db, db_prefix)

            log.info("[summarize-month] caching country/continent lists")
            continents = _summary_continents(cur, metrics_db)

            for period in period_codes:
                dstart, dstop, dthis = _summary_get_dates(dthis_input, period)
                log.info(f"  period={period} dthis={dthis} {dstart} .. {dstop}")

                # login_ips_tmp is rebuilt per-period (the IN-clause shrinks
                # over short periods).  Always needed by int_users / dl users
                # rowid=7,8 sections; cheap, so build unconditionally.
                login_ips_subq = _summary_build_login_ips(
                    cur, metrics_db, dstart, dstop)

                # dl_users_period_tmp is only needed for the "dl" section.
                if "dl" in sections:
                    _summary_build_dl_users_period(
                        cur, metrics_db, dstart, dstop)

                # Order matches the PHP: reg, int, dl, total, sim, sim-usage, misc.
                if "reg" in sections:
                    _summary_reg_users(
                        cur, hub_db, metrics_db, db_prefix,
                        dthis, dstart, dstop, period, continents)
                if "int" in sections:
                    _summary_int_users(
                        cur, metrics_db,
                        dthis, dstart, dstop, period,
                        continents, login_ips_subq)
                if "dl" in sections:
                    _summary_download_users(
                        cur, metrics_db,
                        dthis, dstart, dstop, period,
                        continents, login_ips_subq)
                if "total" in sections:
                    _summary_total_users(cur, metrics_db, dthis, period)
                if "sim" in sections:
                    _summary_sim_users(
                        cur, metrics_db,
                        dthis, dstart, dstop, period, continents)
                if "sim-usage" in sections:
                    _summary_sim_usage(
                        cur, hub_db, metrics_db,
                        dthis, dstart, dstop, period, rappture_tools_sql)
                if "misc" in sections:
                    _summary_misc_usage(
                        cur, metrics_db, db_prefix,
                        dthis, dstart, dstop, period)

        log.info("[summarize-month] done")
        return 0
    finally:
        conn.close()


def cmd_summarize_month(args):
    only = None
    if args.only:
        only = tuple(s.strip() for s in args.only.split(",") if s.strip())
    periods = None
    if args.periods:
        periods = tuple(int(p) for p in args.periods.split(",") if p.strip())
    return do_summarize_month(
        args.yearmonth,
        only=only, periods=periods,
        dry_run=args.dry_run,
    )


def cmd_resolve_dns(args):
    return do_resolve_dns(
        args.db_key, args.table, args.date_spec,
        all_dates=args.all,
        nameserver=args.nameserver,
        concurrency=args.concurrency,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


# ---------------------------------------------------------------------------
# run  (autonomous daily / catch-up mode)
# ---------------------------------------------------------------------------

def cmd_run(args):
    dry_run = args.dry_run

    if not dry_run:
        if not acquire_lock():
            log.info(f"[run] still running — skipping.")
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
            log.info(f"[run] nothing to do")
            return

        if not dry_run:
            log.debug(f"=== hzmetrics.py run @ {datetime.now()} ===")

        # Always import pending days for the current month first.
        if current_pending:
            log.info(f"[run] importing {len(current_pending)} pending day(s) for {today_str}")
            for date_str in current_pending:
                log.info(f"--- {date_str} ---")
                do_import_day(date_str, dry_run)

        if backlog_months:
            # Catch-up mode: one backlog month per tick.
            target    = backlog_months[0]
            remaining = len(backlog_months)
            log.info(f"[run] catch-up: {remaining} backlog month(s) — processing {target}")
            if not dry_run:
                log.debug(f"catch-up: {target} ({remaining} remaining)")
            days = pending_days_for_month(target)
            for date_str in days:
                log.info(f"--- {date_str} ---")
                do_import_day(date_str, dry_run)
            log.info(f">>> analyzing {target}")
            do_analyze(target, dry_run)
            log.info(f">>> summarizing {target}")
            do_summarize(target, dry_run)

        else:
            # Normal mode: analyze current month once per day, then check previous month.
            if not analyzed_today:
                log.info(f"[run] analyzing current month {today_str}")
                do_analyze(today_str, dry_run)
                if not dry_run:
                    update_state(analyzed=today_date)

            if prev_needs_work:
                log.info(f"[run] {prev} complete — analyzing and summarizing")
                do_analyze(prev, dry_run)
                do_summarize(prev, dry_run)
            elif not is_month_summarized(prev):
                last    = last_day_of_month(prev)
                days_in = date.today().day
                if days_in > 5:
                    log.warning(f"[run] {prev} last day ({last}) never arrived "
                                f"({days_in}d into {today_str}) — summarizing without it")
                    do_analyze(prev, dry_run)
                    do_summarize(prev, dry_run)
                else:
                    log.info(f"[run] {prev} last day ({last}) not yet imported — deferring")

        log.info(">>> done")

    finally:
        if not dry_run:
            release_lock()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Metrics pipeline manager")
    sub = parser.add_subparsers(dest="command")

    p_tick = sub.add_parser("tick", help="Every-5-min cron entry: whoisonline always, metrics run at :30")
    p_tick.set_defaults(func=cmd_tick)
    p_tick.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_run = sub.add_parser("run", help="Autonomous daily/catch-up metrics run (called by tick at :30)")
    p_run.set_defaults(func=cmd_run)
    p_run.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_woo = sub.add_parser("whoisonline", help="Update real-time session geo map")
    p_woo.set_defaults(func=cmd_whoisonline)
    p_woo.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_status = sub.add_parser("status", help="Show pipeline state")
    p_status.set_defaults(func=cmd_status)

    p_process = sub.add_parser("process", help="Import logs, analyze, and summarize for a month (normal usage)")
    p_process.set_defaults(func=cmd_process)
    grp = p_process.add_mutually_exclusive_group()
    grp.add_argument("--next",  action="store_true",  help="Use the oldest pending month")
    grp.add_argument("--month", metavar="YYYY-MM",    help="Specify a month")
    grp.add_argument("--day",   metavar="YYYY-MM-DD", help="Specify a single day")
    p_process.add_argument("--force",   action="store_true", help="Skip order and current-month checks")
    p_process.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_import = sub.add_parser("import", help="Raw log ingestion only — fetch, import, archive")
    p_import.set_defaults(func=cmd_import)
    grp2 = p_import.add_mutually_exclusive_group()
    grp2.add_argument("--next",  action="store_true",  help="Use the oldest pending month")
    grp2.add_argument("--month", metavar="YYYY-MM",    help="Specify a month")
    grp2.add_argument("--day",   metavar="YYYY-MM-DD", help="Specify a single day")
    p_import.add_argument("--force",    action="store_true", help="Skip order and current-month checks")
    p_import.add_argument("--dry-run",  action="store_true", help="Show what would be done without doing it")

    p_analyze = sub.add_parser("analyze", help="Run enrichment and stats for a completed month")
    p_analyze.set_defaults(func=cmd_analyze)
    p_analyze.add_argument("--month",   metavar="YYYY-MM", required=True)
    p_analyze.add_argument("--force",   action="store_true", help="Run even if month is not yet complete")
    p_analyze.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_summarize = sub.add_parser("summarize", help="Run rolling-window aggregation for a completed month")
    p_summarize.set_defaults(func=cmd_summarize)
    p_summarize.add_argument("--month",   metavar="YYYY-MM", required=True)
    p_summarize.add_argument("--force",   action="store_true", help="Run even if month is not yet complete")
    p_summarize.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_setup = sub.add_parser("setup-db", help="Create metrics database and all tables (idempotent)")
    p_setup.set_defaults(func=cmd_setup_db)
    p_setup.add_argument("--dry-run", action="store_true", help="Show statements without executing")

    p_migrate = sub.add_parser("migrate", help="Show or apply schema migrations")
    p_migrate.set_defaults(func=cmd_migrate)
    p_migrate.add_argument("--apply", action="store_true", help="Apply all pending migrations")

    p_dnload = sub.add_parser("backfill-dnload", help="Populate web.dnload flag for historical rows")
    p_dnload.set_defaults(func=cmd_backfill_dnload)
    p_dnload.add_argument("--start", metavar="YYYY-MM", type=_arg_yyyymm,
        help="Only process months >= this (default: all)")
    p_dnload.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_geo = sub.add_parser("fill-geo", help="Backfill missing GeoIP country data")
    p_geo.set_defaults(func=cmd_fill_geo)
    grp_geo = p_geo.add_mutually_exclusive_group(required=True)
    grp_geo.add_argument("--month", metavar="YYYY-MM", help="Fill a specific month")
    grp_geo.add_argument("--all",   action="store_true", help="Fill all months with missing GeoIP data")
    p_geo.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_hub = sub.add_parser("import-hub-data",
        help="Copy sessionlog and xprofiles from the hub DB into the metrics DB "
             "(ports xlogimport_tool_and_reg_user_data.php)")
    p_hub.set_defaults(func=cmd_import_hub_data)
    p_hub.add_argument("--dry-run", action="store_true",
        help="Show statements without executing")

    p_iauth = sub.add_parser("import-auth",
        help="Parse a cmsauth-format file and INSERT IGNORE login/simulation rows "
             "into metrics.userlogin (ports xlogimport_authlog.php)")
    p_iauth.set_defaults(func=cmd_import_auth)
    p_iauth.add_argument("input_file", metavar="FILE",
        help="path to staged auth log, or '-' for stdin")
    p_iauth.add_argument("--dry-run", action="store_true",
        help="Parse and report counts, but don't INSERT")

    p_mw = sub.add_parser("middleware-wall",
        help="Copy joblog.walltime into metrics.toolstart "
             "(direct port of xlogfix_middleware_wall.pl)")
    p_mw.set_defaults(func=cmd_middleware_wall)
    p_mw.add_argument("--dry-run", action="store_true",
        help="Show statements without executing")

    p_mc = sub.add_parser("middleware-cpu",
        help="Copy joblog.cputime into metrics.toolstart "
             "(direct port of xlogfix_middleware_cpu.pl)")
    p_mc.set_defaults(func=cmd_middleware_cpu)
    p_mc.add_argument("--dry-run", action="store_true",
        help="Show statements without executing")

    p_ls = sub.add_parser("logfix-session",
        help="Coalesce web rows into websessions in 4 fixed week windows "
             "of the month (direct port of logfix_session.pl)")
    p_ls.set_defaults(func=cmd_logfix_session)
    p_ls.add_argument("month", nargs="?", default=None, metavar="YYYY-MM",
        help="Month to process (default: current month)")
    p_ls.add_argument("--dry-run", action="store_true",
        help="Show the week boundaries; don't INSERT/UPDATE")

    p_fl = sub.add_parser("fetch-logs",
        help="Concatenate daily apache/cmsauth logs into staging files "
             "(port of __fetch_apache_and_auth_log.sh)")
    p_fl.set_defaults(func=cmd_fetch_logs)
    p_fl.add_argument("date", nargs="?", default=None, metavar="YYYYMMDD",
        help="Only fetch files whose name contains this substring "
             "(default: every file in daily/)")
    p_fl.add_argument("--dry-run", action="store_true",
        help="List matched files; don't read/write anything")

    p_al = sub.add_parser("archive-logs",
        help="gzip daily logs in place and move them to imported/ "
             "(port of __archive_apache_and_auth_log.sh)")
    p_al.set_defaults(func=cmd_archive_logs)
    p_al.add_argument("date", nargs="?", default=None, metavar="YYYYMMDD",
        help="Only archive files whose name contains this substring "
             "(default: every file in daily/)")
    p_al.add_argument("--dry-run", action="store_true",
        help="List matched files; don't gzip/move anything")

    p_sm = sub.add_parser("summarize-month",
        help="Compute the per-period summary_*_vals tables for a month "
             "(port of xlogfix_summary.php)")
    p_sm.set_defaults(func=cmd_summarize_month)
    p_sm.add_argument("yearmonth", nargs="?", default=None, metavar="YYYY-MM",
        help="Month to score (default: last month)")
    p_sm.add_argument("--only", default=None, metavar="LIST",
        help="Comma-separated subset of sections to run. "
             "Choices: reg,int,dl,total,sim,sim-usage,misc (default: all)")
    p_sm.add_argument("--periods", default=None, metavar="LIST",
        help="Comma-separated subset of period codes to run. "
             "Codes: 0=cal-year, 1=month, 3=quarter, 12=rolling-12, "
             "13=fiscal-year, 14=all-time (default: all six)")
    p_sm.add_argument("--dry-run", action="store_true",
        help="Show period windows; don't run any worker")

    p_gtl = sub.add_parser("gen-tool-toplists",
        help="Per-period ranked lists across all tools into hub.jos_stats_topvals "
             "(ports gen_tool_toplists.php)")
    p_gtl.set_defaults(func=cmd_gen_tool_toplists)
    p_gtl.add_argument("yearmonth", nargs="?", default=None, metavar="YYYY-MM",
        help="Month to process (default: current month)")
    p_gtl.add_argument("--dry-run", action="store_true",
        help="Report what would be regenerated without DELETE/INSERT")

    p_gtt = sub.add_parser("gen-tool-tops",
        help="Top-N breakdowns (country / domain / orgtype) per tool stat row "
             "into hub.jos_resource_stats_tools_topvals (ports gen_tool_tops.php)")
    p_gtt.set_defaults(func=cmd_gen_tool_tops)
    p_gtt.add_argument("yearmonth", nargs="?", default=None, metavar="YYYY-MM",
        help="Month to process (default: current month)")
    p_gtt.add_argument("--dry-run", action="store_true",
        help="Report what would be regenerated without DELETE/INSERT")

    p_gts = sub.add_parser("gen-tool-stats",
        help="Per-tool session/job aggregates into hub.jos_resource_stats_tools "
             "and resource_stats (ports gen_tool_stats.php)")
    p_gts.set_defaults(func=cmd_gen_tool_stats)
    p_gts.add_argument("yearmonth", nargs="?", default=None, metavar="YYYY-MM",
        help="Month to score (default: current month)")
    p_gts.add_argument("--dry-run", action="store_true",
        help="Report computed counts without UPSERT")

    p_ic = sub.add_parser("fill-ipcountry",
        help="Look up ipcountry for unresolved rows and bulk-update target table "
             "(direct port of xlogfix_ipcountry.php)")
    p_ic.set_defaults(func=cmd_fill_ipcountry)
    p_ic.add_argument("db_key", choices=["metrics", "hub"],
        help="Target DB ('metrics' or 'hub')")
    p_ic.add_argument("table", choices=list(FILL_IPCOUNTRY_TABLES),
        help="Target table")
    p_ic.add_argument("date_spec", nargs="?", default=None, metavar="DATE_OR_RANGE",
        help="YYYY | YYYY-MM | YYYY-MM-DD or '<start>..<end>' (default: current month)")
    p_ic.add_argument("--all", action="store_true",
        help="Not supported here — geo lookup needs a bounded window")
    p_ic.add_argument("--url", default=IPCOUNTRY_URL,
        help=f"hubzero ipinfo endpoint (default {IPCOUNTRY_URL})")
    p_ic.add_argument("--hub-key", default=IPCOUNTRY_HUB_KEY, dest="hub_key",
        help=f"hub_key parameter (default {IPCOUNTRY_HUB_KEY})")
    p_ic.add_argument("--timeout", type=float, default=IPCOUNTRY_TIMEOUT,
        help=f"HTTP timeout seconds (default {IPCOUNTRY_TIMEOUT})")
    p_ic.add_argument("--dry-run", action="store_true",
        help="Show what would be looked up; don't HTTP / UPDATE")

    p_am = sub.add_parser("andmore-usage",
        help="Per-resource distinct-user counts into hub.jos_resource_stats "
             "(ports xlogfix_andmore_usage.php)")
    p_am.set_defaults(func=cmd_andmore_usage)
    p_am.add_argument("yearmonth", nargs="?", default=None, metavar="YYYY-MM",
        help="Month to score (default: current month)")
    p_am.add_argument("--dry-run", action="store_true",
        help="Report counts without UPSERT-ing into resource_stats")

    p_ia = sub.add_parser("import-apache",
        help="Parse an apache-format file and INSERT eligible rows into "
             "metrics.web (ports xlogimport_apache.php)")
    p_ia.set_defaults(func=cmd_import_apache)
    p_ia.add_argument("input_file", metavar="FILE",
        help="path to staged apache log, or '-' for stdin")
    p_ia.add_argument("--dry-run", action="store_true",
        help="Parse and report counts; don't INSERT")

    p_fd = sub.add_parser("fill-domain",
        help="Derive `domain` column from `host` and bulk-update target table "
             "(ports xlogfix_domain.php)")
    p_fd.set_defaults(func=cmd_fill_domain)
    p_fd.add_argument("db_key", choices=["metrics", "hub"],
        help="Target DB ('metrics' or 'hub')")
    p_fd.add_argument("table",
        help="Target table (web | toolstart | sessionlog_metrics)")
    p_fd.add_argument("date_spec", nargs="?", default=None, metavar="DATE_OR_RANGE",
        help="YYYY | YYYY-MM | YYYY-MM-DD or '<start>..<end>' (default: current month)")
    p_fd.add_argument("--all", action="store_true",
        help="No date filter (use with care on large tables)")
    p_fd.add_argument("--dry-run", action="store_true",
        help="Show derivations; don't UPDATE")

    p_wh = sub.add_parser("import-webhits",
        help="Aggregate per-day hit counts from an apache log into metrics.webhits "
             "(ports xlogimport_webhits.php)")
    p_wh.set_defaults(func=cmd_import_webhits)
    p_wh.add_argument("input_file", metavar="FILE",
        help="path to staged apache log, or '-' for stdin")
    p_wh.add_argument("--dry-run", action="store_true",
        help="Parse and report counts; don't INSERT")

    p_bots = sub.add_parser("identify-bots",
        help="Scan an apache-format log and populate metrics.bot_useragents "
             "(ports xlogfix_identify_bots.php)")
    p_bots.set_defaults(func=cmd_identify_bots)
    p_bots.add_argument("input_file", metavar="FILE",
        help="path to staged apache log, or '-' for stdin")
    p_bots.add_argument("--dry-run", action="store_true",
        help="Parse and report counts; don't INSERT or DELETE")

    p_ui = sub.add_parser("fill-user-info",
        help="Fill countrycitizen / countryresident / orgtype on toolstart / "
             "sessionlog_metrics from hub user profiles (ports xlogfix_user_info.php)")
    p_ui.set_defaults(func=cmd_fill_user_info)
    p_ui.add_argument("db_key", choices=["metrics", "hub"],
        help="Target DB ('metrics' or 'hub')")
    p_ui.add_argument("table",
        help="Target table (typically toolstart or sessionlog_metrics)")
    p_ui.add_argument("date_spec", nargs="?", default=None, metavar="DATE_OR_RANGE",
        help="Accepted for CLI compat; ignored — UPDATE has no date filter")
    p_ui.add_argument("--all", action="store_true",
        help="Accepted for CLI compat; ignored")
    p_ui.add_argument("--dry-run", action="store_true",
        help="Show statements without executing")

    p_clean = sub.add_parser("clean-bots",
        help="DELETE rows in web/websessions matching exclude_list bot patterns (ports xlogfix_clean.php)")
    p_clean.set_defaults(func=cmd_clean_bots)
    p_clean.add_argument("table", choices=list(CLEAN_BOTS_TABLES),
        help="Target table (web | websessions)")
    p_clean.add_argument("date_spec", nargs="?", default=None, metavar="DATE_OR_RANGE",
        help="YYYY | YYYY-MM | YYYY-MM-DD or '<start>..<end>' (default: current month)")
    p_clean.add_argument("--all", action="store_true",
        help="Not supported — DELETE needs an explicit bounded range")
    p_clean.add_argument("--dry-run", action="store_true",
        help="Show the chunks and filters; don't DELETE anything")

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
    p_dns.set_defaults(func=cmd_resolve_dns)
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
    if not getattr(args, "func", None):
        parser.print_help()
        return
    setup_logging()
    log.debug(f"=== hzmetrics.py {' '.join(sys.argv[1:])} ===")
    args.func(args)

if __name__ == "__main__":
    main()
