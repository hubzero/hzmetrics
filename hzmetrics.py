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
  hzmetrics.py rebuild-webhits [--month YYYY-MM | --all] [--dry-run]
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
  hzmetrics.py init                                                   # one-shot install bootstrap (dirs + DB + migrate)
  hzmetrics.py doctor [--fix]                                         # diagnose install health; --fix attempts repair
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
# safely invoke `python3 /opt/hubzero/metrics/bin/hzmetrics.py` regardless.
#
# Only fires when invoked as a script (`__name__ == "__main__"`).  When a
# test does `import hzmetrics` under an older interpreter the relaunch would
# os.execv() the test runner itself with the wrong argv (`python3 -m unittest
# …` collapses unhelpfully through execv), so importers stay on whatever
# interpreter loaded them and the test author is responsible for using a
# 3.10+ python.
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

if __name__ == "__main__":
    _relaunch_if_needed()


import argparse
import gzip
import logging
import re
import time
from collections import defaultdict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("hzmetrics")


def _pipeline_paths():
    """Detect site name and APACHELOGDIR from /etc/hubzero.conf and the
    presence of /etc/apache2 vs /etc/httpd.  Falls back to safe defaults
    so the module still imports outside a deployed hub host.

    `site_explicit` is True iff `/etc/hubzero.conf` was readable and
    contained a `site = …` line.  Self-bootstrap checks this and aborts
    when running as apache without an explicit site (filename and DB
    prefixes would all collide on the literal string "hub")."""
    site = "hub"
    site_explicit = False
    try:
        with open("/etc/hubzero.conf") as f:
            for line in f:
                m = re.match(r"\s*site\s*=\s*(\S+)", line)
                if m:
                    site = m.group(1).strip()
                    site_explicit = True
                    break
    except (FileNotFoundError, PermissionError, OSError):
        pass
    if Path("/etc/apache2").is_dir():
        apache_log_dir = Path("/var/log/apache2")
    else:
        apache_log_dir = Path("/var/log/httpd")
    return {
        "site":            site,
        "site_explicit":   site_explicit,
        "apache_log_dir": apache_log_dir,
        "cms_log_dir":    Path("/var/log/hubzero"),
        "metrics_log_dir": Path("/var/log/hubzero/metrics"),
    }


_PIPELINE_PATHS    = _pipeline_paths()
SITE               = _PIPELINE_PATHS["site"]
SITE_EXPLICIT      = _PIPELINE_PATHS["site_explicit"]
APACHE_LOG_DIR     = _PIPELINE_PATHS["apache_log_dir"]
CMS_LOG_DIR        = _PIPELINE_PATHS["cms_log_dir"]
HTTPD_DAILY        = APACHE_LOG_DIR / "daily"
HTTPD_HOLDING      = APACHE_LOG_DIR / "daily.holding"
HZ_DAILY           = CMS_LOG_DIR / "daily"
HZ_HOLDING         = CMS_LOG_DIR / "daily.holding"
HTTPD_IMPORTED     = APACHE_LOG_DIR / "imported"
HZ_IMPORTED        = CMS_LOG_DIR / "imported"
HZ_METRICS_STAGING = _PIPELINE_PATHS["metrics_log_dir"]
STAGED_APACHE      = HZ_METRICS_STAGING / "_hub_apache.log"
STAGED_AUTH        = HZ_METRICS_STAGING / "_hub_auth.log"

# Install root — everything except the human-facing log file lives
# under HZMETRICS_HOME, a self-contained tree owned by the service user.
# Defaults to /opt/hubzero/metrics; can be relocated entirely via the
# HZMETRICS_HOME env var (used by tests + dev installs).  See
# docs/deployment.md for the rationale and the migration sequence
# from the pre-2026 split layout (/etc/hubzero-metrics, /var/run/hzmetrics).
HZMETRICS_HOME = Path(os.environ.get("HZMETRICS_HOME", "/opt/hubzero/metrics"))

# `manage.log` is a historic name from the manage.py-era pre-rename;
# kept for path-stability so operators' logrotate / monitoring configs
# don't break.  Lives under /var/log/hubzero/metrics so operators find
# it where Unix convention says to look — the rest of the install can
# move under HZMETRICS_HOME but logs stay sysadmin-discoverable.
# Override with HZMETRICS_LOG (see setup_logging).
LOG         = HZ_METRICS_STAGING / "manage.log"

LOCK_FILE   = HZMETRICS_HOME / "state" / "hzmetrics.pid"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _IsoFormatter(logging.Formatter):
    """Logger output uses ISO 8601 with the `T` date/time separator,
    millisecond precision, and a local-timezone offset suffix
    (e.g. `2026-05-21T14:05:49.123-04:00`).  Single machine-parseable
    form across stderr, file, and syslog handlers; unambiguous
    regardless of the operator's timezone or how their downstream
    tooling parses timestamps."""

    _LOCAL_TZ = datetime.now().astimezone().tzinfo

    def formatTime(self, record, datefmt=None):  # noqa: ARG002
        dt = datetime.fromtimestamp(record.created, tz=self._LOCAL_TZ)
        return dt.isoformat(timespec="milliseconds")


def setup_logging() -> None:
    """Configure the `hzmetrics` logger with ISO 8601 timestamps on three
    handlers:

      - stderr (INFO+) — picked up by cron's MAILTO or systemd journal
        when run from a unit / timer.
      - syslog (INFO+) via /dev/log on LOG_LOCAL0 facility — operators
        configure rsyslog / syslog-ng / journald rules to route from
        there.  Failures (no /dev/log, daemon down) are non-fatal:
        we fall back to the other two handlers.
      - file (DEBUG+) at HZMETRICS_LOG (defaults to LOG) — the
        authoritative grep-able audit log.  HZMETRICS_LOG override
        used by the A/B test harness (developer UID, not apache).

    Idempotent: re-invocation replaces any previously installed handlers
    so test setups can call it more than once."""
    log.setLevel(logging.DEBUG)
    for h in list(log.handlers):
        log.removeHandler(h)

    fmt = _IsoFormatter("%(asctime)s %(levelname)-5s %(message)s")

    stream = logging.StreamHandler()
    stream.setLevel(logging.INFO)
    stream.setFormatter(fmt)
    log.addHandler(stream)

    # Syslog — INFO+ to LOG_LOCAL0 via the local Unix socket.  Operators
    # route this to a hub-specific file or central logger via rsyslog
    # rules.  We embed our own ISO timestamp in the message body so the
    # application clock is preserved even if rsyslog reformats its own
    # header timestamp.
    try:
        from logging.handlers import SysLogHandler
        syslog = SysLogHandler(address="/dev/log",
                               facility=SysLogHandler.LOG_LOCAL0)
        syslog.ident = "hzmetrics: "
        syslog.setLevel(logging.INFO)
        syslog.setFormatter(fmt)
        log.addHandler(syslog)
    except (OSError, ImportError) as e:
        # /dev/log missing or syslog daemon down — log to the other
        # handlers and continue.  The stderr + file handlers above are
        # still in place by this point, so the warning is captured.
        log.warning("syslog handler not available: %s", e)

    log_path = Path(os.environ.get("HZMETRICS_LOG", str(LOG)))
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        log.addHandler(file_handler)
    except OSError as e:
        log.warning("could not open %s for append: %s", log_path, e)

def dated_files(directory: str | Path, pattern: str,
                *, recursive: bool = False) -> list[tuple[str, Path]]:
    """Return sorted list of (date_str, Path) for files matching pattern in directory.

    With recursive=True, descends into subdirectories — used to pick up logs
    that a sysadmin tucked into year-subdirs like daily/YYYY/ as informal
    organization."""
    results = []
    glob_fn = Path(directory).rglob if recursive else Path(directory).glob
    for p in glob_fn(pattern):
        if p.is_dir():
            continue
        for part in p.name.replace("-", ".").replace("_", ".").split("."):
            if len(part) == 8 and part.isdigit():
                results.append((part, p))
                break
    return sorted(results)

def _source_dirs(kind: str) -> list[tuple[Path, bool]]:
    """Return [(dir, recursive), ...] of all places we look for pending
    source logs of the given kind ("access" or "auth"), in priority order.

    Priority matters when the same YYYYMMDD shows up in more than one place:
    the first hit wins (so daily/ beats daily.holding/), the duplicate is
    logged and skipped.  All listed dirs may be missing on disk; absent dirs
    are silently skipped.
    """
    if kind == "access":
        return [
            (HTTPD_DAILY,    True),   # daily/  and  daily/YYYY/
            (HTTPD_HOLDING,  False),  # daily.holding/  (flat)
        ]
    if kind == "auth":
        return [
            (HZ_DAILY,       True),
            (HZ_HOLDING,     False),
        ]
    raise ValueError(f"unknown kind: {kind!r}")

def _source_pattern(kind: str) -> str:
    if kind == "access":
        return f"{SITE}-access*log*"
    if kind == "auth":
        return "cmsauth*log*"
    raise ValueError(f"unknown kind: {kind!r}")

def enumerate_log_sources(kind: str) -> list[tuple[str, Path]]:
    """Return sorted [(YYYYMMDD, Path), ...] for every pending source log
    of the given kind, across all locations it may live in:

      - daily/<pattern>                  (current standard)
      - daily/<YYYY>/<pattern>           (informal sysadmin year-subdir layout)
      - daily.holding/<pattern>          (alternate staging from logrotate)

    If the same YYYYMMDD appears in more than one place, the higher-priority
    location wins (see _source_dirs); duplicates are logged at WARNING and
    skipped.  Used by the orchestrator to find work regardless of how files
    got placed on disk."""
    pattern = _source_pattern(kind)
    seen: dict[str, Path] = {}
    for src_dir, recurse in _source_dirs(kind):
        if not src_dir.exists():
            continue
        for date_str, path in dated_files(src_dir, pattern, recursive=recurse):
            if date_str in seen:
                if seen[date_str] != path:
                    log.warning(
                        f"duplicate {kind} log for {date_str}: "
                        f"keeping {seen[date_str]}, ignoring {path}"
                    )
                continue
            seen[date_str] = path
    return sorted(seen.items())

# Both apache and cmsauth pending logs surface the same date as work to
# do — do_import_day handles each kind independently, so the orchestrator
# only needs the union of date strings.  Auth-only days are real on hubs
# where apache logs were rotated away or never existed (the geodynamics
# pre-2022 backlog: 428 cmsauth files, 0 access files).
_LOG_KINDS_FOR_DISCOVERY = ("access", "auth")


def _enumerate_all_pending_days() -> list[str]:
    """Sorted, deduped YYYYMMDD list of days with at least one pending
    source file of any kind."""
    days: set[str] = set()
    for kind in _LOG_KINDS_FOR_DISCOVERY:
        for d, _ in enumerate_log_sources(kind):
            days.add(d)
    return sorted(days)


def pending_days_for_month(month_str: str) -> list[str]:
    """Sorted list of date strings (across all source dirs and log kinds)
    for the given YYYY-MM.  Returns a day if EITHER an access log OR an
    auth log is pending for it; do_import_day handles each kind on its
    own."""
    yyyymm = month_str.replace("-", "")
    return [d for d in _enumerate_all_pending_days() if d.startswith(yyyymm)]

def oldest_pending_month() -> str | None:
    """YYYY-MM of the earliest pending source log anywhere, or None —
    drives `process --next` and the catch-up loop.  Searches daily/,
    daily/YYYY/, and daily.holding/ across both access and auth log
    kinds."""
    days = _enumerate_all_pending_days()
    if not days:
        return None
    d = days[0]
    return f"{d[:4]}-{d[4:6]}"

def last_imported_date() -> str | None:
    """YYYYMMDD of the most recently archived access log, or None — used
    by check_order to refuse out-of-order imports."""
    files = dated_files(HTTPD_IMPORTED, f"{SITE}-access*log*")
    return files[-1][0] if files else None

def is_current_month(month_str: str) -> bool:
    """True if `month_str` (YYYY-MM) is the calendar month we're in right
    now — guards against scoring an in-flight month whose data is still
    arriving (see _require_complete_month)."""
    return month_str == date.today().strftime("%Y-%m")

def _require_complete_month(month: str, force: bool) -> None:
    """Abort with a clear error if `month` is the current calendar month
    and the caller hasn't explicitly opted in via --force.  Used by the
    analyze / summarize entrypoints to refuse to score an in-flight
    month, which would yield wrong rolling-window numbers."""
    if is_current_month(month) and not force:
        log.error(f"{month} is the current month and not yet complete.")
        log.error(f"  Use --force to override.")
        raise SystemExit(1)

def _arg_yyyymm(s: str) -> str:
    """argparse `type=` validator: accept 'YYYY-MM', reject anything else.

    Used on CLI args whose value flows into SQL string interpolation, so a
    malformed value can't widen into an injection vector."""
    if not re.fullmatch(r"\d{4}-\d{2}", s):
        raise argparse.ArgumentTypeError(f"expected YYYY-MM, got {s!r}")
    return s

def _arg_sql_identifier(s: str) -> str:
    """argparse `type=` validator: accept SQL-identifier shape only —
    `[A-Za-z_][A-Za-z0-9_]*` — and reject anything else.

    Used on CLI args (table names) that get interpolated into SQL as
    identifiers, since identifiers cannot be parameterized via %s.
    Argparse `choices=` is preferable where the set of valid tables is
    small and fixed; this regex validator is for the open-ended cases
    (e.g., `resolve-dns <table>` works against any table with a date
    column).
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s):
        raise argparse.ArgumentTypeError(
            f"expected SQL identifier (table name), got {s!r}")
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
    pending = [d for d, _ in enumerate_log_sources("access")]
    if pending and date_str > pending[0]:
        log.error(f"{date_str} is not the oldest pending day.")
        log.error(f"  Oldest pending: {pending[0]}")
        log.error(f"  Use --force to override.")
        raise SystemExit(1)
    last = last_imported_date()
    if last and date_str < last:
        log.error(f"{date_str} is older than the most recently imported log ({last}).")
        log.error(f"  Use --force to override.")
        raise SystemExit(1)

def previous_month(month_str: str) -> str:
    """Return the YYYY-MM that immediately precedes `month_str`, rolling
    over from January to the previous December."""
    y, m = int(month_str[:4]), int(month_str[5:7])
    m -= 1
    if m == 0:
        m, y = 12, y - 1
    return f"{y:04d}-{m:02d}"

def next_month(month_str: str) -> str:
    """Return the YYYY-MM that immediately follows `month_str`, rolling
    over from December to the next January."""
    y, m = int(month_str[:4]), int(month_str[5:7])
    m += 1
    if m == 13:
        m, y = 1, y + 1
    return f"{y:04d}-{m:02d}"

def months_in_range(start: str, end: str) -> list[str]:
    """Return YYYY-MM strings from `start` through `end` inclusive, in
    chronological order.  Returns an empty list if start > end."""
    if start > end:
        return []
    out = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur = next_month(cur)
    return out

def last_day_of_month(month_str: str) -> str:
    """Return YYYYMMDD for the last calendar day of the given YYYY-MM."""
    y, m = int(month_str[:4]), int(month_str[5:7])
    last = (datetime(y, m, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return last.strftime("%Y%m%d")

def is_month_fully_imported(month_str: str) -> bool:
    """True if the last calendar day of month_str is present in imported/."""
    last = last_day_of_month(month_str)
    return any(d == last for d, _ in dated_files(HTTPD_IMPORTED, f"{SITE}-access*log*"))


def is_month_complete(month_str: str) -> bool:
    """True if month_str's input data has structurally arrived: either
    the last calendar day's log file is in imported/, OR `web` has at
    least one row in the *next* month (which demonstrates that import
    time has crossed the month boundary).

    The second signal is the safety net.  Without it, a logrotate skip
    on the very last day of a month would leave the month flagged
    "not complete" forever — the legacy fallback was "if calendar day
    of the current month > 5, summarize anyway," but calendar-based
    fallbacks are flaky (operator running manual ticks, tz drift, etc).
    Checking for next-month data instead is data-driven: if the
    pipeline has already imported anything dated after this month, the
    month is definitively past, regardless of any one specific file."""
    if is_month_fully_imported(month_str):
        return True
    return month_has_data(next_month(month_str))

def is_month_summarized(month_str: str) -> bool:
    """True if summarize-month has produced *any* rows for `month_str` —
    used as a cheap "did we touch this month?" check.

    "Summarized" specifically means at least one summary_user_vals row
    exists at `datetime = '<YYYY-MM>-00'` with `period = 1` (PERIOD_MONTH,
    see PERIOD_* constants).  The `-00` is the legacy PHP convention for
    "this whole month" — datetime '2025-07-00' means July 2025 as a unit,
    not a real day.

    See also is_month_fully_summarized() for the strict completeness check
    the catchup state machine wants."""
    _, _, _, metrics_db = db_credentials()
    count = mysql_scalar(
        f"SELECT COUNT(*) FROM {metrics_db}.summary_user_vals "
        f"WHERE datetime = %s AND period = 1;",  # period = 1 = PERIOD_MONTH
        (month_str + "-00",),
    )
    return bool(count)


# The six period codes summarize-month writes.  Defined here so the
# completeness check below stays in sync with the canonical list — the
# real PERIOD_* constants are 3000+ lines below in hzmetrics.py and
# importing them at module top would mean forward references.
_PERIOD_CODES_FOR_FULL_CHECK = (1, 0, 3, 12, 13, 14)

# Summary tables that actually receive rows during a complete summarize-month.
# summary_andmore_vals is excluded — its data lands in the hub DB
# (jos_resource_stats), not in metrics, so it's perpetually empty here.
_SUMMARY_VALS_TABLES = ("summary_user_vals", "summary_misc_vals", "summary_simusage_vals")


def is_month_fully_summarized(month_str: str) -> bool:
    """True iff every period code (1, 0, 3, 12, 13, 14) has at least one
    row in each of summary_user_vals / summary_misc_vals /
    summary_simusage_vals at `datetime = 'YYYY-MM-00'`.

    Strict end-to-end check: distinguishes "summarize ran and finished"
    from "summarize started, wrote some period-1 cells, then died" —
    which we've observed in the live DB for 2025-07 (only 55 of the usual
    462 summary_user_vals rows present)."""
    _, _, _, metrics_db = db_credentials()
    dt = f"{month_str}-00"
    for table in _SUMMARY_VALS_TABLES:
        for period in _PERIOD_CODES_FOR_FULL_CHECK:
            count = mysql_scalar(
                f"SELECT COUNT(*) FROM {metrics_db}.{table} "
                f"WHERE datetime = %s AND period = %s;",
                (dt, period),
            )
            if not count:
                return False
    return True


def period_incomplete_months(today_str: str) -> list[str]:
    """Months (before today_str) that have SOME summary rows but are
    missing at least one period code — i.e. summarize touched them but
    didn't write the full set of periods.  Sorted oldest-first.

    This is the cheap, table-wide complement to is_month_fully_summarized:
    one GROUP BY per summary table (a few hundred rows each), versus 18
    point COUNT()s per candidate month.  It catches:

      - months below the rebuild cursor that a catchup pass only ever
        period-1-summarized (the pre-2022 cmsauth backlog: imported and
        period-1'd, but never rebuilt to full periods because
        catchup_started was pinned ahead of them); and
      - a month whose summarize crashed after some — but not all —
        period codes were written.

    It does NOT catch a month that has all 6 period codes present but is
    missing rowids *within* a period (a finer partial-crash shape); the
    forward cursor walk covers those, and is_month_fully_summarized is
    the strict per-month check when precision matters.  Months with zero
    summary rows are intentionally excluded — a true data gap is not the
    same as an incomplete summarize, and GROUP BY only returns months
    that have at least one row anyway."""
    _, _, _, metrics_db = db_credentials()
    full = len(_PERIOD_CODES_FOR_FULL_CHECK)
    months: set = set()
    for table in _SUMMARY_VALS_TABLES:
        rows = mysql_query(
            f"SELECT DATE_FORMAT(datetime, '%%Y-%%m') AS ym "
            f"FROM {metrics_db}.{table} "
            f"GROUP BY ym HAVING COUNT(DISTINCT period) < %s",
            (full,),
        )
        for (ym,) in rows:
            if ym and ym < today_str:
                months.add(ym)
    return sorted(months)


def month_has_source(month_str: str) -> bool:
    """True if any pending source log file exists for the given YYYY-MM,
    anywhere the discovery layer looks (daily/, daily/YYYY/, daily.holding/).

    "Pending" means not-yet-imported — sources already moved to imported/
    don't count.  Used by the catchup decision matrix to ask: "is there
    fresh data to ingest for this month, or are we deciding what to do
    with already-imported state?"  """
    return bool(pending_days_for_month(month_str))


# Base tables that hold per-row activity for a single month.  Used to
# detect "this month was already imported at some point" even when the
# source log has been archived off the host (which happened to all the
# 2024 access logs on geodynamics: rows present in `web` / `userlogin`
# but daily/ + imported/ + daily.holding/ are all empty for those dates).
_BASE_DATA_TABLES = ("web", "userlogin", "webhits", "websessions")


def month_has_data(month_str: str) -> bool:
    """True if any base table has at least one row in the given YYYY-MM.

    Cheap probe — `LIMIT 1` on each indexed datetime column.  Used by
    the catchup decision matrix to distinguish "fresh month, just import"
    from "rows already present in DB; need wipe-or-trust decision."""
    _, _, _, metrics_db = db_credentials()
    y, m = int(month_str[:4]), int(month_str[5:7])
    start = f"{month_str}-01"
    end = f"{y + 1:04d}-01-01" if m == 12 else f"{y:04d}-{m + 1:02d}-01"
    for table in _BASE_DATA_TABLES:
        if mysql_scalar(
            f"SELECT 1 FROM {metrics_db}.{table} "
            f"WHERE datetime >= %s AND datetime < %s LIMIT 1;",
            (start, end),
        ):
            return True
    return False


def month_has_orphaned_stamps(month_str: str) -> bool:
    """True iff any `web` OR `toolstart` row in this month carries a
    `sessionid` stamp that doesn't reference an extant `websessions.id`.
    Surfaces the cleanup landmine: wiping websessions without resetting
    web.sessionid (or the tool-session stamps in toolstart) leaves
    ghost stamps that the next logfix-session subsequently skips (its
    predicate is `WHERE sessionid IS NULL OR sessionid = '0'`), so
    months end up with vastly fewer sessions than the data warrants.

    Probes both base tables that logfix-session stamps: `web` (every
    websessions-emit UPDATEs the matching event rows) and `toolstart`
    (the _iphost_jobs pass stamps tool-launch rows for the period-3
    sessions metric).  Web-only checking would miss a toolstart drift
    if the safety net is ever asked to catch a future invariant break.

    Cheap LIMIT 1 probes per side — both use the sessionid index.
    Called by the catchup decision matrix to force a
    reset-and-resummarize on any month whose derived tables have
    drifted from the base tables."""
    _, _, _, metrics_db = db_credentials()
    y, m = int(month_str[:4]), int(month_str[5:7])
    start = f"{month_str}-01"
    end = f"{y + 1:04d}-01-01" if m == 12 else f"{y:04d}-{m + 1:02d}-01"
    if mysql_scalar(
        f"SELECT 1 FROM {metrics_db}.web w "
        f"WHERE w.datetime >= %s AND w.datetime < %s "
        f"  AND w.sessionid IS NOT NULL AND w.sessionid <> '0' "
        f"  AND NOT EXISTS ("
        f"    SELECT 1 FROM {metrics_db}.websessions ws "
        f"    WHERE ws.id = w.sessionid"
        f"  ) "
        f"LIMIT 1;",
        (start, end),
    ):
        return True
    return bool(mysql_scalar(
        f"SELECT 1 FROM {metrics_db}.toolstart t "
        f"WHERE t.datetime >= %s AND t.datetime < %s "
        f"  AND t.sessionid IS NOT NULL AND t.sessionid <> '0' "
        f"  AND NOT EXISTS ("
        f"    SELECT 1 FROM {metrics_db}.websessions ws "
        f"    WHERE ws.id = t.sessionid"
        f"  ) "
        f"LIMIT 1;",
        (start, end),
    ))


def _reset_month_for_resummarize(month_str: str, dry_run: bool = False) -> None:
    """Clear derived state for one month — `web.sessionid` stamps, all
    `websessions` rows for the month, the month's `summary_*_vals`
    cells, AND the month's `webhits` rows — leaving the base `web` /
    `userlogin` rows intact.  Used by the catchup decision matrix in
    three cases:

      - operator marked the month dirty (after a bulk DELETE/UPDATE);
      - `month_has_orphaned_stamps()` detected drift between `web` and
        `websessions`;
      - `source ✓ data ✓` branch, after additively importing pending
        files, to force `logfix-session` to recompute sessions from
        the combined (existing + new) base rows.

    `webhits` is derived — one row per day = COUNT(*) of kept web rows.
    Reset deletes the month's webhits rows and then re-aggregates from
    current `web` (via do_rebuild_webhits) so the table is left
    consistent with the post-reset state of web.  Without that
    delete-then-rebuild, a marked-dirty month would summarize with
    stale (pre-bulk-edit) hit counts.

    Rebuild lives here (not in do_analyze) so analyze stays a no-op on
    derived tables that haven't been wiped — test fixtures that seed
    webhits with hand-chosen values to exercise summarize don't get
    their seed replaced on every analyze run.

    Never touches base tables.  Their only safe deletion path is
    `forget-import` against an `imported_sources` PK range — datetime
    inference is forbidden because filenames are sortable identifiers,
    not data dictionaries (the `datetime` of rows a file imported is
    that file's business, recorded in `imported_sources.pk_start..pk_end`,
    not inferrable from the filename)."""
    _, _, _, metrics_db = db_credentials()
    y, m = int(month_str[:4]), int(month_str[5:7])
    start = f"{month_str}-01"
    end = f"{y + 1:04d}-01-01" if m == 12 else f"{y:04d}-{m + 1:02d}-01"
    dt_summary = f"{month_str}-00"

    log.info(f"[reset] {month_str}: resetting web.sessionid + wiping derived tables")
    if dry_run:
        log.info(f"  [dry-run] UPDATE web SET sessionid=NULL WHERE datetime in [{start},{end})")
        log.info(f"  [dry-run] DELETE FROM websessions WHERE datetime in [{start},{end})")
        log.info(f"  [dry-run] DELETE FROM webhits WHERE datetime in [{start},{end})")
        for table in _SUMMARY_VALS_TABLES + ("summary_andmore_vals",):
            log.info(f"  [dry-run] DELETE FROM {table} WHERE datetime = '{dt_summary}'")
        return

    # web.sessionid reset is chunked — a 1.4 M-row update on the
    # geodynamics hub took ~3 min at 100 k/chunk; doing it in one
    # statement risks the undo log + replication lag.
    conn = _open_db()
    try:
        chunk = 100_000
        total = 0
        with conn.cursor() as cur:
            while True:
                cur.execute(
                    f"UPDATE {metrics_db}.web SET sessionid = NULL "
                    f"WHERE datetime >= %s AND datetime < %s "
                    f"  AND sessionid IS NOT NULL AND sessionid <> '0' "
                    f"LIMIT {chunk};",
                    (start, end),
                )
                n = cur.rowcount
                if not n:
                    break
                total += n
        log.info(f"[reset] {month_str}: cleared sessionid on {total} web row(s)")

        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {metrics_db}.websessions "
                f"WHERE datetime >= %s AND datetime < %s;",
                (start, end),
            )
            cur.execute(
                f"DELETE FROM {metrics_db}.webhits "
                f"WHERE datetime >= %s AND datetime < %s;",
                (start, end),
            )
            for table in _SUMMARY_VALS_TABLES + ("summary_andmore_vals",):
                cur.execute(
                    f"DELETE FROM {metrics_db}.{table} WHERE datetime = %s;",
                    (dt_summary,),
                )
    finally:
        conn.close()

    # Repopulate webhits from current `web` so the table is left
    # consistent (rather than empty) after reset.  Cheap COUNT(*) per
    # day.  Outside the connection above because do_rebuild_webhits
    # opens its own connection via mysql_exec.
    do_rebuild_webhits(month_str, dry_run=dry_run)


_lock_fd: int | None = None  # held open across acquire/release so flock survives


def _init_start_epoch() -> int | None:
    """Wall-clock Unix epoch at which the current environment's PID 1
    started.  Computed as `btime + init_starttime_jiffies // CLK_TCK`,
    so it captures both "host boot" (bare metal — PID 1 is kernel init,
    starts at ~0 jiffies after boot) AND "container start" (container
    runtime — PID 1 is the entrypoint, starts N jiffies after the host
    boot).  Every meaningful "the environment changed" case (reboot,
    container restart) yields a different value.

    Used to disambiguate stale PID files: an entry whose stored
    init_start_epoch differs from the live one is definitively from a
    prior environment and cannot be the current holder.

    Returns None on non-Linux hosts that don't expose /proc; callers
    must fall back to treating environment-id as unknown."""
    try:
        btime = None
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                btime = int(line.split()[1])
                break
        if btime is None:
            return None
        # /proc/1/stat field layout:
        #   pid (comm) state ppid pgrp session tty_nr tpgid flags
        #   minflt cminflt majflt cmajflt utime stime cutime cstime
        #   priority nice num_threads itrealvalue starttime ...
        # starttime is field 22 (1-indexed).  `comm` may contain
        # whitespace and parens; split after the last `)` to be safe.
        stat = Path("/proc/1/stat").read_text()
        after_comm = stat.rsplit(")", 1)[1].split()
        # after_comm[0] = state ; field 22 overall = after_comm[19]
        init_jiffies = int(after_comm[19])
        hz = os.sysconf("SC_CLK_TCK")
        return btime + init_jiffies // hz
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _process_looks_like_hzmetrics(pid: int) -> bool:
    """True iff /proc/<pid>/cmdline mentions hzmetrics, suggesting the
    PID still belongs to a running pipeline instance and not a recycled
    OS PID that's now some unrelated process.  Cheap probe — used to
    log a more informative "stale" message when the lock acquire finds
    a leftover PID file."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return False
    return b"hzmetrics" in cmdline


def _diagnose_stale_lock_file() -> None:
    """Inspect any pre-existing LOCK_FILE content at acquire time and
    log what we found.  Purely diagnostic — by the time this is called,
    flock has already been granted to us (the kernel released any prior
    holder's lock when its process died, or the file is leftover from
    a prior environment and no one holds the flock).  This just tells
    the operator what the stale content represented.

    Expected file format (post-2026 lock-file v2):
       <pid> <init_start_epoch> <iso_acquired>
    """
    try:
        body = LOCK_FILE.read_text().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return
    if not body:
        return
    parts = body.split()
    if not parts:
        return
    try:
        prior_pid = int(parts[0])
    except ValueError:
        return
    prior_env = None
    if len(parts) >= 2:
        try:
            prior_env = int(parts[1])
        except ValueError:
            prior_env = None

    cur_env = _init_start_epoch()
    if prior_env is not None and cur_env is not None and prior_env != cur_env:
        log.info(f"[lock] cleaning up stale lock file from prior environment "
                 f"(PID {prior_pid}, init_start_epoch {prior_env} ≠ "
                 f"current {cur_env}) — host reboot or container restart")
        return

    if _process_looks_like_hzmetrics(prior_pid):
        # Process exists AND looks like an hzmetrics instance, yet
        # flock was granted to us — means the prior holder closed its
        # FD without unlinking, which shouldn't happen in normal exit
        # but could on a forcible SIGKILL right after acquire.  Note
        # but don't block.
        log.info(f"[lock] flock was free but PID {prior_pid} still "
                 f"appears to be an hzmetrics process — likely a forcibly "
                 f"killed prior instance that lost its file")
        return

    # PID either doesn't exist or now belongs to an unrelated process
    # (PID recycling).  Most common stale case after kill -9 / OOM.
    try:
        os.kill(prior_pid, 0)
        alive = True
    except (ProcessLookupError, PermissionError):
        alive = False
    if alive:
        log.info(f"[lock] cleaning up stale lock file: PID {prior_pid} "
                 f"is alive but is not an hzmetrics process (recycled PID)")
    else:
        log.info(f"[lock] cleaning up stale lock file: PID {prior_pid} "
                 f"is no longer running")


def acquire_lock() -> bool:
    """Try to acquire an advisory lock on LOCK_FILE.  Returns True if acquired,
    False if another instance already holds it.

    Uses fcntl.flock — kernel-managed, so the lock releases automatically if
    the process dies (no stale-lock detection needed) and concurrent
    acquire_lock calls from racing ticks are serialized atomically by the
    kernel rather than by a check-then-write that has a TOCTOU window.

    The file's contents on a successful acquire are three space-separated
    fields on one line:
       <pid> <init_start_epoch> <iso_acquired_timestamp>
    All three are purely diagnostic (`cat $HZMETRICS_HOME/state/hzmetrics.pid`
    to see who holds it); the lock itself is the flock, not the file
    existence or the file content.  `init_start_epoch` is the wall-clock
    Unix epoch at which the current environment's PID 1 started — it
    changes on host reboot AND on container restart, so consumers can
    distinguish "current holder" from "stale entry survived any kind of
    environment reset" without needing to test the flock.  Before
    writing fresh content we inspect any existing entry and log what
    was stale and why — purely diagnostic but useful for postmortems.

    LOCK_FILE.parent is created on demand if the service-user can write
    to HZMETRICS_HOME (the install is self-contained under that tree).
    """
    global _lock_fd
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        log.error(f"cannot create {LOCK_FILE.parent} — permission denied.")
        log.error(f"  Run once as root: install -d -o apache -g apache -m 0750 "
                  f"{HZMETRICS_HOME} {LOCK_FILE.parent}")
        raise SystemExit(1) from None

    import fcntl
    fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return False
    # We hold the flock now; anything previously in the file is by
    # definition stale.  Log what it was before overwriting.
    _diagnose_stale_lock_file()
    env_epoch = _init_start_epoch()
    env_field = str(env_epoch) if env_epoch is not None else "?"
    acquired_iso = datetime.now().astimezone().isoformat(timespec="milliseconds")
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()} {env_field} {acquired_iso}\n".encode())
    os.fsync(fd)
    _lock_fd = fd
    return True

def release_lock() -> None:
    """Drop the flock and unlink LOCK_FILE.  Safe to call without a prior
    acquire — does nothing if no lock is held.  (Kernel would release
    the flock on process exit anyway; this is for the tidiness of the
    on-disk pid file.)

    Order matters: unlink BEFORE releasing the flock, otherwise there is
    a race window where a second process can acquire the flock on the
    same inode, write its PID into the file, and then we unlink the
    file *they* now own — which lets a third process create+lock a
    fresh file and believe it's the sole holder while the second still
    thinks it has the lock.  Unlinking while we still hold the flock
    closes that window.
    """
    global _lock_fd
    if _lock_fd is not None:
        try:
            LOCK_FILE.unlink()
        except FileNotFoundError:
            pass
        import fcntl
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(_lock_fd)
        _lock_fd = None
    else:
        # No lock held — still try to tidy a stale PID file from a prior
        # crashed run, but harmlessly noop if it isn't there.
        try:
            LOCK_FILE.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Self-bootstrap.  Run at the top of cron entry points (cmd_run, cmd_tick)
# when the process is owned by the service user — creates any missing
# install / log / staging directory and the metrics database if absent, so
# a fresh access.cfg + an empty $HZMETRICS_HOME is enough to start a hub.
# Strictly gated to the apache (RHEL/Rocky) or www-data (Debian/Ubuntu)
# UID: dev shells, root, and the A/B harness do nothing, which keeps
# shared state out of their reach.
# ---------------------------------------------------------------------------

# The named user is whoever runs the HUBzero web server.  We check
# euid identity (not effective name) so a privilege drop via setuid
# is honored.
_SERVICE_USERS = ("apache", "www-data")


def _running_as_service_user() -> bool:
    """True iff our euid maps to one of the known HUBzero web-server
    user names.  Returns False on KeyError (uid not in /etc/passwd) so
    rootless / namespaced environments are treated as not-service."""
    import pwd
    try:
        return pwd.getpwuid(os.geteuid()).pw_name in _SERVICE_USERS
    except KeyError:
        return False


# Expected directory tree.  Every entry must exist (and be writable) for
# the pipeline to make progress.  Order matters only insofar as a missing
# parent is the most useful thing to report first — pathlib's mkdir
# parents=True will create chains transparently as long as we have
# permission on the topmost existing ancestor.
def _expected_dirs() -> list:
    return [
        HZMETRICS_HOME,
        HZMETRICS_HOME / "bin",
        HZMETRICS_HOME / "conf",
        HZMETRICS_HOME / "state",
        HZ_METRICS_STAGING,  # /var/log/hubzero/metrics
        HZ_DAILY,            # /var/log/hubzero/daily
        HZ_IMPORTED,         # /var/log/hubzero/imported
        HTTPD_DAILY,         # /var/log/{httpd,apache2}/daily
        HTTPD_IMPORTED,      # /var/log/{httpd,apache2}/imported
    ]


def _bootstrap_dirs() -> None:
    """Create any missing entry in _expected_dirs().  Logs each fresh
    create.  Aborts with a clear next-step on PermissionError — the
    typical cause is HZMETRICS_HOME (under /opt) or a /var/log subtree
    that the service user can't write to because the parent isn't
    apache-owned yet."""
    for d in _expected_dirs():
        if d.is_dir():
            continue
        try:
            d.mkdir(parents=True, exist_ok=True)
            log.info(f"[bootstrap] created {d}")
        except PermissionError:
            log.error(
                f"[bootstrap] cannot create {d} — permission denied.\n"
                f"  Run once as root to seat the install:\n"
                f"    sudo make install-bootstrap   "
                f"(creates {HZMETRICS_HOME} owned by the web-server user)\n"
                f"  And ensure /var/log/hubzero/ exists and is writable by "
                f"the web-server user."
            )
            raise SystemExit(2) from None


def _bootstrap_database() -> None:
    """Run the setup-db baseline DDL and apply any pending migrations.
    METRICS_DB_DDL[0] is itself `CREATE DATABASE IF NOT EXISTS` so this
    works against an empty server as well as one with an established
    schema — every statement is idempotent.

    Aborts on any DDL failure: better to fail loudly than to enter
    the steady-state loop with a half-built schema."""
    cfg = db_config()
    metrics_db = cfg.get("metrics_db", "")
    if not metrics_db:
        log.error("[bootstrap] access.cfg has no metrics_db — set it and retry")
        raise SystemExit(2)

    for stmt in METRICS_DB_DDL:
        sql = stmt.format(metrics_db=metrics_db)
        if mysql_exec(sql) != 0:
            log.error("[bootstrap] setup-db DDL failed — aborting")
            raise SystemExit(2)

    _apply_pending_migrations(metrics_db, source="bootstrap", required_only=True)


def _self_bootstrap() -> None:
    """Idempotent self-repair invoked at the top of cron entry points.
    Splits cleanly so the unit tests can exercise each phase:

      1. Identity gate — only the apache / www-data UID does anything.
      2. Site-name gate — refuses to proceed if /etc/hubzero.conf had
         no `site = …` line; the site string is woven into filenames
         (e.g. `${SITE}-access*log*`) and the DB-prefix expectations,
         so a silent fallback to the literal "hub" would collide on
         every multi-hub host.
      3. Filesystem repair — mkdir every entry in _expected_dirs() that
         doesn't already exist.
      4. Database repair — CREATE DATABASE IF NOT EXISTS, baseline DDL,
         migrate apply.
    """
    if not _running_as_service_user():
        return
    if not SITE_EXPLICIT:
        log.error(
            "[bootstrap] site name missing from /etc/hubzero.conf.\n"
            "  Expected a line of the form `site = <hubname>`.\n"
            "  Refusing to proceed — the site name is the prefix for "
            "every staged log filename and several DB conventions."
        )
        raise SystemExit(2)
    _bootstrap_dirs()
    _bootstrap_database()


# State lives in `<metrics_db>.pipeline_state` (a simple key/value table).
# Previously it was a one-line-per-key file at /var/run/hzmetrics/hzmetrics.state.
# The DB location survives reboots (tmpfs wipes /var/run on most distros),
# enables atomic multi-key updates, and shows up in standard mysqldumps so
# operators don't lose orchestrator state when restoring backups.
#
# The flock-based lock at LOCK_FILE stays on disk — kernel-managed
# dead-process release is hard to replicate cleanly in SQL.
#
# Tracked keys today:
#   analyzed=YYYY-MM-DD     — last day cmd_run invoked do_analyze (daily guard)
#   mode=normal|catchup|rebuild  (added in Phase C)
#   catchup_started=YYYY-MM
#   rebuild_cursor=YYYY-MM

def _state_table(metrics_db: str) -> str:
    return f"{metrics_db}.pipeline_state"

def _ensure_state_table(metrics_db: str) -> None:
    """Create pipeline_state if missing — covers the upgrade window
    before `hzmetrics migrate --apply` has been run.  Idempotent; cheap
    enough to call on every read/write."""
    mysql_exec(
        f"CREATE TABLE IF NOT EXISTS {_state_table(metrics_db)} ("
        "  k VARCHAR(64) NOT NULL,"
        "  v VARCHAR(255) NOT NULL,"
        "  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP "
        "    ON UPDATE CURRENT_TIMESTAMP,"
        "  PRIMARY KEY (k)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb3;"
    )

def read_state() -> dict[str, str]:
    """Return the {key: value} dict from pipeline_state."""
    _, _, _, metrics_db = db_credentials()
    _ensure_state_table(metrics_db)
    return {k: v for k, v in mysql_query(
        f"SELECT k, v FROM {_state_table(metrics_db)};"
    )}

def update_state(**kwargs: object) -> None:
    """Upsert key=value pairs.  Single SQL statement so an in-flight
    `cmd_run` either sees all the new values or none (ON DUPLICATE KEY
    UPDATE is row-atomic; one statement = one transaction)."""
    if not kwargs:
        return
    _, _, _, metrics_db = db_credentials()
    _ensure_state_table(metrics_db)
    values = ", ".join(["(%s, %s)"] * len(kwargs))
    params: list = [x for k, v in kwargs.items() for x in (k, str(v))]
    mysql_exec(
        f"INSERT INTO {_state_table(metrics_db)} (k, v) VALUES {values} "
        f"ON DUPLICATE KEY UPDATE v=VALUES(v);",
        tuple(params),
    )


# `pipeline_state.dirty_months` is a comma-separated YYYY-MM list.  Set
# by `mark-dirty` (operator-driven, after bulk DELETE/UPDATE on web),
# read by the catchup decision matrix; each entry is cleared once that
# month finishes a successful reset+resummarize pass.
def get_dirty_months() -> set[str]:
    s = read_state().get("dirty_months", "") or ""
    return {m for m in s.split(",") if m}


def add_dirty_months(months) -> None:
    cur = get_dirty_months()
    new = cur | {m for m in months if m}
    if new == cur:
        return
    update_state(dirty_months=",".join(sorted(new)))


def clear_dirty_month(month: str) -> None:
    cur = get_dirty_months()
    if month not in cur:
        return
    cur.discard(month)
    update_state(dirty_months=",".join(sorted(cur)))


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
    required: bool = True            # False = performance/optional; skipped by init/bootstrap

MIGRATIONS = [
    Migration(
        id=1,
        description="Index web(dnload) — applied by backfill-dnload May 2026",
        sql="ALTER TABLE {metrics_db}.web ADD INDEX dnload (dnload);",
        check_sql="SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='web' AND index_name='dnload';",
        required=False,
    ),
    Migration(
        id=2,
        description="Composite index web(sessionid, dnload) — covering index for download_users JOIN",
        sql="ALTER TABLE {metrics_db}.web ADD INDEX web_sessionid_dnload (sessionid, dnload);",
        check_sql="SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='web' AND index_name='web_sessionid_dnload';",
        required=False,
    ),
    Migration(
        id=3,
        description="Composite index websessions(datetime, jobs, duration, ipcountry) — filter pushdown for int/download_users",
        sql="ALTER TABLE {metrics_db}.websessions ADD INDEX ws_datetime_jobs_dur_country (datetime, jobs, duration, ipcountry);",
        check_sql="SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='websessions' AND index_name='ws_datetime_jobs_dur_country';",
        required=False,
    ),
    Migration(
        id=4,
        description="Purge userlogin rows with action not in (login, simulation) — detect/invalid/logout are never queried",
        sql="DELETE FROM {metrics_db}.userlogin WHERE action NOT IN ('login', 'simulation');",
        check_sql="SELECT COUNT(*) FROM {metrics_db}.userlogin WHERE action NOT IN ('login', 'simulation');",
        check_expect=0,
        required=False,
    ),
    Migration(
        id=5,
        description="Index websessions(domain) — speeds up domainclass JOIN in download org queries",
        sql="ALTER TABLE {metrics_db}.websessions ADD INDEX ws_domain (domain);",
        check_sql="SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='websessions' AND index_name='ws_domain';",
        required=False,
    ),
    Migration(
        id=6,
        description="Index websessions(jobs, ipcountry, duration) — period-14 all-time download_users filter",
        sql="ALTER TABLE {metrics_db}.websessions ADD INDEX ws_jobs_country_dur (jobs, ipcountry, duration);",
        check_sql="SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='{metrics_db}' AND table_name='websessions' AND index_name='ws_jobs_country_dur';",
        required=False,
    ),
    Migration(
        id=7,
        description="Create pipeline_state — orchestrator state moves from /var/run file to DB",
        sql=(
            "CREATE TABLE IF NOT EXISTS {metrics_db}.pipeline_state ("
            "  k VARCHAR(64) NOT NULL,"
            "  v VARCHAR(255) NOT NULL,"
            "  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP "
            "    ON UPDATE CURRENT_TIMESTAMP,"
            "  PRIMARY KEY (k)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb3;"
        ),
        check_sql=(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='{metrics_db}' AND table_name='pipeline_state';"
        ),
    ),
]


# Engine conversions to InnoDB.  MyISAM table-level locks block summarize's
# DELETE+INSERT-per-cell readers; converting the small / actively-rewritten
# tables to InnoDB gets row-level locking and MVCC.  web and websessions
# stay MyISAM (too large for ALTER in the 5 GB /var/mysqltmp tmpfs);
# *_baseline_jul2025 frozen tables stay MyISAM (read-only, no benefit).
# Already-InnoDB tables (exclude_list2, migrations, pipeline_state) are
# not listed.  userlogin_lite is listed even though some live DBs already
# have it as InnoDB — the check_sql auto-detects and skips.
_INNODB_CONVERT_TABLES = [
    # Tier 1: lookup / reference (small, read-mostly)
    "bot_useragents",
    "classes",
    "classvals",
    "continents",
    "countries",
    "country_continent",
    "domainclass",
    "domainclasses",
    "exclude_list",
    "regions",
    "regionvals",
    "summary_andmore",
    "summary_misc",
    "summary_simusage",
    "summary_user",
    "tops",
    "topvals",
    "totalvals",
    "webhits",
    # Tier 2: actively-written summary tables (DELETE+INSERT per cell)
    "summary_andmore_vals",
    "summary_misc_vals",
    "summary_simusage_vals",
    "summary_user_vals",
    # Tier 3: per-analyze rebuild (DROP+CREATE each analyze run)
    "jos_xprofiles_metrics",
    "sessionlog_metrics",
    "toolstart",
    "userlogin_lite",
    # Tier 4: userlogin — large until migration 4 + OPTIMIZE shrinks it;
    # the ALTER ENGINE rewrites the table, so it also reclaims deleted-row
    # space if OPTIMIZE wasn't run separately.
    "userlogin",
]

for _i, _tbl in enumerate(_INNODB_CONVERT_TABLES):
    MIGRATIONS.append(Migration(
        id=8 + _i,
        description=(
            f"Convert {_tbl} to InnoDB — row-level locking + MVCC; "
            f"unblocks summarize readers"
        ),
        sql=f"ALTER TABLE {{metrics_db}}.{_tbl} ENGINE=InnoDB;",
        check_sql=(
            "SELECT COUNT(*) FROM information_schema.tables "
            f"WHERE table_schema='{{metrics_db}}' "
            f"AND table_name='{_tbl}' AND engine='InnoDB';"
        ),
        required=False,
    ))
del _i, _tbl


# Performance — fill-domain bottleneck.  fill-domain's JOIN-UPDATE was
# blocked on LOWER(t.host) preventing any index use; observed at 170s
# for 56k rows on a 2022-01 backfill (~3 ms/row).  Two-step fix:
#   1. normalise existing host values to lowercase (do_resolve_dns now
#      writes lowercase too, so future inserts stay clean)
#   2. add an index on host so the now-LOWER-less JOIN can lookup.
MIGRATIONS.extend([
    Migration(
        id=36,
        description="Backfill web.host to lowercase (fill-domain JOIN normalisation)",
        sql=("UPDATE {metrics_db}.web SET host = LOWER(host) "
             "WHERE host IS NOT NULL AND host <> '' "
             "  AND BINARY host <> BINARY LOWER(host);"),
        check_sql=("SELECT COUNT(*) FROM {metrics_db}.web "
                   "WHERE host IS NOT NULL AND host <> '' "
                   "  AND BINARY host <> BINARY LOWER(host);"),
        check_expect=0,
        required=False,
    ),
    Migration(
        id=37,
        description="Index web(host) — fill-domain JOIN, after migration 36 normalisation",
        sql="ALTER TABLE {metrics_db}.web ADD INDEX web_host (host(255));",
        check_sql=(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema='{metrics_db}' AND table_name='web' "
            "  AND index_name='web_host';"
        ),
        required=False,
    ),
    Migration(
        id=38,
        description="Backfill websessions.host to lowercase (fill-domain JOIN normalisation)",
        sql=("UPDATE {metrics_db}.websessions SET host = LOWER(host) "
             "WHERE host IS NOT NULL AND host <> '' "
             "  AND BINARY host <> BINARY LOWER(host);"),
        check_sql=("SELECT COUNT(*) FROM {metrics_db}.websessions "
                   "WHERE host IS NOT NULL AND host <> '' "
                   "  AND BINARY host <> BINARY LOWER(host);"),
        check_expect=0,
        required=False,
    ),
    Migration(
        id=39,
        description="Index websessions(host) — fill-domain JOIN, after migration 38 normalisation",
        sql="ALTER TABLE {metrics_db}.websessions ADD INDEX ws_host (host(255));",
        check_sql=(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema='{metrics_db}' AND table_name='websessions' "
            "  AND index_name='ws_host';"
        ),
        required=False,
    ),
    Migration(
        id=40,
        description=("Convert websessions to InnoDB — concurrent reads + MVCC, "
                     "crash safety, faster random updates than 414 MB MyISAM"),
        sql="ALTER TABLE {metrics_db}.websessions ENGINE=InnoDB;",
        check_sql=(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='{metrics_db}' AND table_name='websessions' "
            "  AND engine='InnoDB';"
        ),
        required=False,
    ),
    Migration(
        id=41,
        description=("Convert web to InnoDB — concurrent reads via MVCC, "
                     "faster random updates for fill-domain / fill-ipcountry "
                     "than 10 GB MyISAM (web is the largest table, ~16 M rows)"),
        sql="ALTER TABLE {metrics_db}.web ENGINE=InnoDB;",
        check_sql=(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='{metrics_db}' AND table_name='web' "
            "  AND engine='InnoDB';"
        ),
        required=False,
    ),
    Migration(
        id=42,
        description=("Covering index web(datetime, ip) — lets resolve-dns's "
                     "SELECT DISTINCT ip … WHERE datetime range be served "
                     "entirely from the index, skipping the 8 GB heap"),
        sql="CREATE INDEX web_dt_ip ON {metrics_db}.web (datetime, ip);",
        check_sql=(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema='{metrics_db}' AND table_name='web' "
            "  AND index_name='web_dt_ip';"
        ),
        required=False,
    ),
    Migration(
        id=43,
        description=("Index web(dnload, datetime) — turns the dnload-helper "
                     "queries in summarize from a full date-range scan with "
                     "heap-fetch-and-filter into an index-only seek across "
                     "the dnload=1 slice.  Cut per-month rebuild ticks from "
                     "~45 min back to a few minutes on the geodynamics audit"),
        sql="CREATE INDEX web_dnload_dt ON {metrics_db}.web (dnload, datetime);",
        check_sql=(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema='{metrics_db}' AND table_name='web' "
            "  AND index_name='web_dnload_dt';"
        ),
        required=False,
    ),
    Migration(
        id=44,
        description=("imported_sources table — tracks per-file import "
                     "atomicity for crash recovery.  filename UNIQUE per "
                     "target table prevents duplicate inserts on retry "
                     "after a post-commit, pre-move crash; pk_start/pk_end "
                     "bound the inserted rows so forget-import (or any "
                     "recovery) can DELETE the data via an indexed PK "
                     "range — works at nanoHUB 800M-row scale because the "
                     "range scan is bounded by file size, not table size."),
        sql=("CREATE TABLE IF NOT EXISTS {metrics_db}.imported_sources ("
             "  id BIGINT PRIMARY KEY AUTO_INCREMENT,"
             "  filename VARCHAR(255) NOT NULL,"
             "  target_table ENUM('web','userlogin','webhits') NOT NULL,"
             "  pk_start BIGINT NULL,"
             "  pk_end BIGINT NULL,"
             "  row_count BIGINT NOT NULL DEFAULT 0,"
             "  imported_at DATETIME NOT NULL,"
             "  UNIQUE KEY uniq_filename_target (filename, target_table)"
             ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb3;"),
        check_sql=(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='{metrics_db}' AND table_name='imported_sources';"
        ),
    ),
])


# Collation normalisation to utf8mb3_general_ci — bring the two latin1
# stragglers in line with the rest of the metrics schema (and the hub
# DB upstream).  The bulk of the schema is already utf8mb3; these two
# are pre-existing legacy tables that never got cleaned up.
#
# ipcitylatlong is a 3 M-row ip→city geo table that hzmetrics doesn't
# query — included purely for schema consistency.  userlogin_lite is
# DROPped + recreated from `userlogin` (utf8mb3) on every
# summarize-month run, so it self-heals on the next analyze; the
# migration entry just brings the current rows into line immediately
# rather than waiting for that cycle.
_UTF8MB3_NORMALISE_TABLES = [
    "ipcitylatlong",
    "userlogin_lite",
]

for _i, _tbl in enumerate(_UTF8MB3_NORMALISE_TABLES):
    MIGRATIONS.append(Migration(
        id=45 + _i,
        description=(
            f"Convert {_tbl} from latin1_swedish_ci to "
            f"utf8mb3_general_ci — schema collation normalisation"
        ),
        sql=(
            f"ALTER TABLE {{metrics_db}}.{_tbl} "
            f"CONVERT TO CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci;"
        ),
        check_sql=(
            "SELECT COUNT(*) FROM information_schema.tables "
            f"WHERE table_schema='{{metrics_db}}' "
            f"AND table_name='{_tbl}' "
            f"AND table_collation='utf8mb3_general_ci';"
        ),
        required=False,
    ))
del _i, _tbl


# NB: on a host where /var has less than ~2.4× the existing web.MY{D,I}
# size free, the direct ALTER TABLE in migration #41 can overflow the
# datadir transient (old MyISAM + new InnoDB live side-by-side during
# the copy).  Operators with tight disk should drop secondary indexes
# first, then ALTER, then re-add — see tests/legacy comments in the
# migration source for the staged form.  Migration #41 is the
# happy-path single-statement variant.

MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {metrics_db}.migrations (
    id INT NOT NULL,
    description VARCHAR(255),
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);
"""

def ensure_migrations_table(metrics_db: str) -> None:
    if mysql_exec(MIGRATIONS_TABLE_SQL.format(metrics_db=metrics_db)) != 0:
        log.error(f"failed to create {metrics_db}.migrations table; aborting")
        raise SystemExit(1)
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
            rc = mysql_exec(
                f"INSERT IGNORE INTO {metrics_db}.migrations (id, description) "
                f"VALUES (%s, %s);",
                (m.id, m.description),
            )
            if rc != 0:
                # Non-fatal: the next `migrate` run will retry the auto-mark.
                log.warning(f"failed to auto-mark migration {m.id} as applied; "
                            f"will retry on next migrate run")

def applied_migration_ids(metrics_db: str) -> set[int]:
    return set(mysql_column(f"SELECT id FROM {metrics_db}.migrations ORDER BY id;"))

def _apply_pending_migrations(metrics_db: str, *, source: str = "migrate",
                               required_only: bool = False) -> None:
    """Apply every MIGRATIONS entry not yet recorded in the tracker
    table.  Shared by cmd_migrate (operator-driven) and _self_bootstrap
    (cron-driven on the first tick of a fresh install).  `source` only
    affects the log prefix so the two callers stay distinguishable in
    journalctl / manage.log.

    When `required_only` is True only migrations with required=True are
    applied — used by init/bootstrap so that long-running optional
    performance migrations (large-table InnoDB conversions, index builds)
    don't block the initial install.  Run `migrate --apply` afterwards to
    apply the remainder at a convenient time."""
    ensure_migrations_table(metrics_db)
    applied = applied_migration_ids(metrics_db)
    candidates = [m for m in MIGRATIONS if m.id not in applied]
    if required_only:
        candidates = [m for m in candidates if m.required]
    if not candidates:
        return
    log.info(f"[{source}] {len(candidates)} pending migration(s)"
             + (" (required only)" if required_only else ""))
    for m in candidates:
        sql = m.sql.format(metrics_db=metrics_db)
        log.info(f"[{source}] [{m.id}] {m.description}")
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
            raise SystemExit(1)


def cmd_migrate(args):
    _, _, _, metrics_db = db_credentials()
    ensure_migrations_table(metrics_db)
    applied = applied_migration_ids(metrics_db)

    log.info(f"{'ID':<4}  {'STATUS':<9}  {'TYPE':<11}  DESCRIPTION")
    log.info("-" * 80)
    for m in MIGRATIONS:
        status = "applied" if m.id in applied else "PENDING"
        kind = "required" if m.required else "performance"
        log.info(f"{m.id:<4}  {status:<9}  {kind:<11}  {m.description}")

    pending = [m for m in MIGRATIONS if m.id not in applied]
    if not pending:
        log.info("All migrations applied.")
        return

    req = sum(1 for m in pending if m.required)
    opt = len(pending) - req
    log.info(f"{len(pending)} pending migration(s): {req} required, {opt} performance.")

    if not args.apply:
        log.info("Run with --apply to execute them, or --apply --required-only for required only.")
        return

    log.debug(f"=== hzmetrics.py migrate --apply  @ {datetime.now()} ===")
    _apply_pending_migrations(metrics_db, source="migrate",
                              required_only=args.required_only)
    log.info(">>> done")


# ---------------------------------------------------------------------------
# setup-db  (create metrics database and all tables; idempotent)
# ---------------------------------------------------------------------------

METRICS_DB_DDL = [
    "CREATE DATABASE IF NOT EXISTS `{metrics_db}` DEFAULT CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`bot_useragents` (
  `useragent` tinytext NOT NULL DEFAULT '',
  PRIMARY KEY (`useragent`(255))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`classes` (
  `class` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(128) NOT NULL DEFAULT '',
  `valfmt` tinyint(4) NOT NULL DEFAULT 0,
  `size` tinyint(4) NOT NULL DEFAULT 0,
  PRIMARY KEY (`class`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`classvals` (
  `class` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 0,
  `rank` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(255) DEFAULT NULL,
  `value` bigint(20) NOT NULL DEFAULT 0,
  KEY `class` (`class`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`continents` (
  `continentSHORT` char(2) NOT NULL DEFAULT '',
  `continentLONG` varchar(45) NOT NULL DEFAULT '',
  UNIQUE KEY `continentSHORT` (`continentSHORT`,`continentLONG`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`countries` (
  `code` varchar(4) NOT NULL DEFAULT '',
  `name` varchar(128) NOT NULL DEFAULT '',
  PRIMARY KEY (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`country_continent` (
  `country` char(2) NOT NULL DEFAULT '',
  `continent` char(2) NOT NULL DEFAULT '',
  PRIMARY KEY (`country`,`continent`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`domainclass` (
  `domain` varchar(64) NOT NULL DEFAULT '',
  `class` tinyint(4) NOT NULL DEFAULT 0,
  `country` varchar(4) NOT NULL DEFAULT '',
  `state` varchar(4) NOT NULL DEFAULT '',
  `name` tinytext NOT NULL DEFAULT '',
  PRIMARY KEY (`domain`),
  KEY `class` (`class`),
  KEY `domain_class` (`domain`,`class`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`domainclasses` (
  `class` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(64) NOT NULL DEFAULT '',
  PRIMARY KEY (`class`),
  UNIQUE KEY `class_name` (`class`,`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`exclude_list` (
  `id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `filter` varchar(65) NOT NULL DEFAULT '',
  `type` varchar(65) NOT NULL DEFAULT 'domain',
  `notes` varchar(120) DEFAULT NULL,
  `date_added` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `filter_type` (`filter`,`type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`regionvals` (
  `region` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 0,
  `rank` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(255) DEFAULT NULL,
  `value` bigint(20) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_andmore` (
  `id` tinyint(4) NOT NULL DEFAULT 0,
  `label` varchar(255) NOT NULL DEFAULT '',
  `plot` int(1) DEFAULT 0,
  UNIQUE KEY `label` (`label`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_andmore_vals` (
  `rowid` tinyint(4) NOT NULL DEFAULT 0,
  `colid` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 1,
  `value` bigint(20) DEFAULT 0,
  `valfmt` tinyint(4) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_misc` (
  `id` tinyint(4) NOT NULL DEFAULT 0,
  `label` varchar(255) NOT NULL DEFAULT '',
  `plot` int(1) DEFAULT 0,
  UNIQUE KEY `label` (`label`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_misc_vals` (
  `rowid` tinyint(4) NOT NULL DEFAULT 0,
  `colid` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 1,
  `value` varchar(200) DEFAULT '',
  `valfmt` tinyint(4) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_simusage` (
  `id` tinyint(4) NOT NULL DEFAULT 0,
  `label` varchar(255) NOT NULL DEFAULT '',
  `plot` int(1) DEFAULT 0,
  UNIQUE KEY `label` (`label`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_simusage_vals` (
  `rowid` tinyint(4) NOT NULL DEFAULT 0,
  `colid` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 1,
  `value` bigint(20) DEFAULT 0,
  `valfmt` tinyint(4) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_user` (
  `id` tinyint(4) NOT NULL DEFAULT 0,
  `label` varchar(255) NOT NULL DEFAULT '',
  `plot` int(1) DEFAULT 0,
  UNIQUE KEY `label` (`label`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`summary_user_vals` (
  `rowid` tinyint(4) NOT NULL DEFAULT 0,
  `colid` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 1,
  `value` bigint(20) DEFAULT 0,
  `valfmt` tinyint(4) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`tops` (
  `top` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(128) NOT NULL DEFAULT '',
  `valfmt` tinyint(4) NOT NULL DEFAULT 0,
  `size` tinyint(4) NOT NULL DEFAULT 0,
  PRIMARY KEY (`top`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`totalvals` (
  `hub` tinyint(4) NOT NULL DEFAULT 0,
  `total` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 1,
  `value` bigint(20) NOT NULL DEFAULT 0,
  KEY `hub_total_datetime` (`hub`,`total`,`datetime`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`userlogin` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `user` varchar(255) NOT NULL DEFAULT '-',
  `uidNumber` bigint(20) DEFAULT 0,
  `ip` varchar(15) NOT NULL DEFAULT '',
  `action` varchar(40) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `userlogin` (`datetime`,`user`,`uidNumber`,`ip`,`action`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`userlogin_lite` (
  `id` bigint(20) NOT NULL DEFAULT 0,
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `user` varchar(255) NOT NULL DEFAULT '-',
  `uidNumber` bigint(20) DEFAULT 0,
  `ip` varchar(15) NOT NULL DEFAULT '',
  `action` varchar(40) NOT NULL DEFAULT '',
  KEY `uidNumber` (`uidNumber`),
  KEY `datetime_user` (`datetime`,`user`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

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
  KEY `dnload` (`dnload`),
  KEY `web_sessionid_dnload` (`sessionid`,`dnload`),
  KEY `web_host` (`host`(255)),
  KEY `web_dt_ip` (`datetime`,`ip`),
  KEY `web_dnload_dt` (`dnload`,`datetime`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`webhits` (
  `datetime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `hits` bigint(20) NOT NULL DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

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
  KEY `ip` (`ip`),
  KEY `ws_datetime_jobs_dur_country` (`datetime`,`jobs`,`duration`,`ipcountry`),
  KEY `ws_domain` (`domain`),
  KEY `ws_jobs_country_dur` (`jobs`,`ipcountry`,`duration`),
  KEY `ws_host` (`host`(255))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`pipeline_state` (
  `k` varchar(64) NOT NULL,
  `v` varchar(255) NOT NULL,
  `updated_at` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`k`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",

    """CREATE TABLE IF NOT EXISTS `{metrics_db}`.`imported_sources` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `filename` varchar(255) NOT NULL,
  `target_table` enum('web','userlogin','webhits') NOT NULL,
  `pk_start` bigint(20) DEFAULT NULL,
  `pk_end` bigint(20) DEFAULT NULL,
  `row_count` bigint(20) NOT NULL DEFAULT 0,
  `imported_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_filename_target` (`filename`,`target_table`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3""",
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


# ---------------------------------------------------------------------------
# init / doctor — operator-driven setup and diagnosis.
#
# `init` is the manual counterpart of the cron-time _self_bootstrap: it
# does the same idempotent dir + DB + migrate work but always runs (no
# apache-uid gate), so root or the operator can drive it during install
# or after a reorg.  Safe to re-invoke — every step is "create if
# missing".
#
# `doctor` is the diagnostic.  By default it only reports.  With --fix
# it tries to repair anything fixable from the same toolbox _self_
# bootstrap uses; anything that needs root (chown, parent dir create)
# is reported but not attempted.
# ---------------------------------------------------------------------------


def cmd_init(args):
    """Idempotent one-shot setup: dirs + database + migrate.  Same
    machinery cmd_run / cmd_tick invoke on the first cron tick, but
    runs regardless of the invoking user so an operator can drive the
    install steps before cron is registered."""
    if not SITE_EXPLICIT:
        log.error("[init] site name missing from /etc/hubzero.conf.")
        log.error("  Expected a line of the form `site = <hubname>` "
                  "(SITE prefixes every staged-log filename and DB convention).")
        return 2
    _bootstrap_dirs()
    _bootstrap_database()
    log.info("[init] done — pipeline is ready, register cron and "
             "drop access.cfg into HZMETRICS_HOME/conf/ if not already.")
    return 0


def cmd_doctor(args):
    """Diagnose install health.  Reports every issue first; with --fix,
    repeats the apache-gated _self_bootstrap repair steps.  Exits 0 if
    everything is healthy (or got fixed), 1 if any issue remains.

    Checks:
      - site name resolved from /etc/hubzero.conf
      - access.cfg present and parseable
      - every directory in _expected_dirs() exists AND is writable by
        the invoking user (catches a chown drift between operator
        installs)
      - DB reachable with the access.cfg credentials
      - metrics_db exists, has the baseline tables, and is migrated up
        to the latest known MIGRATIONS id"""
    fix = bool(getattr(args, "fix", False))
    problems: list[str] = []

    # --- 1. Site name ---------------------------------------------------
    if SITE_EXPLICIT:
        log.info(f"[doctor] OK    site name = {SITE}")
    else:
        problems.append(
            "site name missing from /etc/hubzero.conf "
            "(add a line `site = <hubname>` — not fixable from here)"
        )
        log.error("[doctor] FAIL  site name unresolved")

    # --- 2. access.cfg --------------------------------------------------
    try:
        cfg = db_config()
    except (FileNotFoundError, PermissionError, OSError) as e:
        problems.append(f"access.cfg unreadable: {e} (not fixable from here)")
        log.error(f"[doctor] FAIL  cannot read access.cfg: {e}")
        cfg = {}
    else:
        missing_keys = [k for k in ("db_host", "db_user", "db_pass", "metrics_db")
                        if not cfg.get(k)]
        if missing_keys:
            problems.append(
                f"access.cfg missing required key(s): {', '.join(missing_keys)} "
                "(operator must populate)"
            )
            log.error(f"[doctor] FAIL  access.cfg missing: {', '.join(missing_keys)}")
        else:
            log.info(f"[doctor] OK    access.cfg parsed "
                     f"(metrics_db={cfg.get('metrics_db')})")

    # --- 3. Directories -------------------------------------------------
    def _dir_state(d: Path) -> str:
        """Returns one of: "ok", "missing", "unwritable", "inaccessible".
        is_dir() / access() can raise PermissionError when a parent
        directory is 0750 root:apache and the invoking shell is neither
        — treat that as inaccessible so the doctor doesn't crash on a
        legitimate ownership-mismatch case."""
        try:
            if not d.is_dir():
                return "missing"
            return "ok" if os.access(d, os.W_OK) else "unwritable"
        except PermissionError:
            return "inaccessible"

    bad_dirs: list = []
    for d in _expected_dirs():
        state = _dir_state(d)
        if state == "ok":
            log.info(f"[doctor] OK    dir            {d}")
        else:
            bad_dirs.append((state, d))
            log.error(f"[doctor] FAIL  {state:<12}  {d}")
    if bad_dirs and fix:
        try:
            _bootstrap_dirs()
            bad_dirs = [
                (state, d) for (state, d) in bad_dirs
                if _dir_state(d) != "ok"
            ]
        except SystemExit:
            problems.append(
                "could not create one or more expected directories "
                "(likely a parent dir owned by root — see preceding error)"
            )
    if bad_dirs:
        for state, d in bad_dirs:
            problems.append(f"directory {d} is {state}")

    # --- 4. Database reachability + schema -----------------------------
    if cfg.get("metrics_db"):
        db_reachable = False
        db_present   = False
        pending: list = []
        try:
            db_present = (mysql_scalar(
                "SELECT SCHEMA_NAME FROM information_schema.schemata "
                "WHERE SCHEMA_NAME = %s",
                (cfg["metrics_db"],),
            ) is not None)
            db_reachable = True
        except Exception as e:  # pymysql.* or env
            problems.append(f"cannot connect to MySQL: {e}")
            log.error(f"[doctor] FAIL  MySQL unreachable: {e}")

        if db_reachable and not db_present:
            log.error(f"[doctor] FAIL  database `{cfg['metrics_db']}` missing")
            problems.append(f"database `{cfg['metrics_db']}` does not exist")
        elif db_reachable and db_present:
            log.info(f"[doctor] OK    database `{cfg['metrics_db']}` exists")
            try:
                ensure_migrations_table(cfg["metrics_db"])
                applied = applied_migration_ids(cfg["metrics_db"])
                pending = [m for m in MIGRATIONS if m.id not in applied]
            except Exception as e:
                problems.append(f"cannot read migrations table: {e}")
                log.error(f"[doctor] FAIL  migrations table unreadable: {e}")
            else:
                req_pending = [m for m in pending if m.required]
                opt_pending = [m for m in pending if not m.required]
                if req_pending:
                    log.error(f"[doctor] FAIL  {len(req_pending)} required migration(s) pending: "
                              f"{', '.join(str(m.id) for m in req_pending)}")
                    problems.append(
                        f"{len(req_pending)} unapplied required migration(s) "
                        f"({', '.join(str(m.id) for m in req_pending)})"
                    )
                elif pending:
                    log.info("[doctor] OK    migrations up to date (required)")
                else:
                    log.info("[doctor] OK    migrations up to date")
                if opt_pending:
                    log.info(f"[doctor] INFO  {len(opt_pending)} optional performance migration(s) pending: "
                             f"{', '.join(str(m.id) for m in opt_pending)} "
                             f"— run `migrate --apply` when convenient")

        # Fix path: full DB bootstrap (CREATE DATABASE + DDL + migrate).
        # Only attempt if we got past the connect probe — no point if
        # MySQL itself is unreachable.
        if fix and db_reachable and (not db_present or pending):
            _bootstrap_database()
            problems = [p for p in problems
                        if "database `" not in p and "unapplied required migration" not in p]

    # --- summary --------------------------------------------------------
    if problems:
        log.error(f"[doctor] {len(problems)} unresolved issue(s):")
        for p in problems:
            log.error(f"  - {p}")
        if not fix:
            log.error("  Re-run with --fix to attempt automatic repair.")
        return 1
    log.info("[doctor] all checks passed")
    return 0


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


def _source_files_matching(kind: str, date_filter: str | None) -> list[Path]:
    """Paths of source logs of the given kind across all source dirs.
    With date_filter=None, returns everything pending; with a YYYYMMDD
    string, restricts to that single day."""
    return [
        p for d, p in enumerate_log_sources(kind)
        if not date_filter or d == date_filter
    ]


# Heuristics for the staging disk-space pre-flight.  Apache access logs
# compress to roughly 8–12× on gzip; 15× is a conservative ceiling that
# catches the pathological "logrotate broke and one daily file has a
# year of traffic" case before we hit a disk-full error mid-stream.
_STAGING_COMPRESS_RATIO = 15
_STAGING_HEADROOM_MB    = 500   # keep at least this much free on /var


def _check_staging_space(files, staging_dir):
    """Pre-flight: estimate worst-case uncompressed size of `files` and
    refuse the fetch if that plus a fixed headroom would exceed free
    space on `staging_dir`'s filesystem.

    Catches logrotate-failure scenarios (a single "daily" file actually
    containing weeks/months of traffic) cleanly — operator sees a clear
    error instead of disk-full debris mid-import.

    Files contributed by `.gz` extensions get `_STAGING_COMPRESS_RATIO`
    applied; plain-text files are counted at their on-disk size."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(staging_dir).free
    estimate = 0
    for f in files:
        try:
            sz = f.stat().st_size
        except (FileNotFoundError, PermissionError):
            continue
        estimate += sz * _STAGING_COMPRESS_RATIO if f.suffix == ".gz" else sz
    needed = estimate + _STAGING_HEADROOM_MB * 1024 * 1024
    if needed > free:
        log.error(
            f"[fetch-logs] insufficient staging space on "
            f"{staging_dir.parent if str(staging_dir).endswith('/') else staging_dir}: "
            f"need ~{needed // (1024 * 1024)} MB "
            f"(compressed input × {_STAGING_COMPRESS_RATIO}× + "
            f"{_STAGING_HEADROOM_MB} MB headroom), "
            f"free {free // (1024 * 1024)} MB.  "
            f"Free space, split the file, or process the day manually."
        )
        raise SystemExit(1)


def do_fetch_logs(date_filter=None, *, dry_run=False):
    """Concatenate dated daily logs into the metrics staging files.

    Port of import/__fetch_apache_and_auth_log.sh.  Pulls source files
    from every known location (daily/, daily/YYYY/, daily.holding/) so
    the orchestrator can process backlog regardless of how a sysadmin
    organised the files.

    With date_filter=None we take all pending; with date_filter='YYYYMMDD'
    we keep only that single day.

    Pre-flight check: refuses to stage if the cumulative uncompressed
    size estimate would overflow /var/log/hubzero/metrics/.  Catches the
    "logrotate broke and one file has months of data" case before the
    streaming would otherwise fill the disk.
    """
    apache_files  = _source_files_matching("access", date_filter)
    cmsauth_files = _source_files_matching("auth",   date_filter)

    suffix = f"{date_filter}" if date_filter else "all"
    log.info(f"[fetch-logs] access {suffix}: {len(apache_files)} file(s)")
    log.info(f"[fetch-logs] auth   {suffix}: {len(cmsauth_files)} file(s)")

    if dry_run:
        for f in apache_files + cmsauth_files:
            log.info(f"  [dry-run] would zcat: {f}")
        return 0

    HZ_METRICS_STAGING.mkdir(parents=True, exist_ok=True)
    _check_staging_space(apache_files + cmsauth_files, HZ_METRICS_STAGING)

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


def _rmdir_if_empty(d: Path) -> None:
    """rmdir d only if it exists, is a directory, and is empty.  Used
    after archive to clean up daily/YYYY/ and daily.holding/ subdirs that
    the catchup loop just drained — keeps the filesystem tidy without
    risking removal of dirs that still hold files."""
    try:
        if d.is_dir():
            d.rmdir()
            log.info(f"  removed empty source dir: {d}")
    except OSError:
        # Not empty, or perm denied, or it disappeared — fine, leave it.
        pass


def do_archive_logs(date_filter=None, *, dry_run=False):
    """gzip each daily log in place and move it to imported/.

    Port of import/__archive_apache_and_auth_log.sh.  Handles the two
    primary metrics streams (access + auth) by walking every known source
    location (daily/, daily/YYYY/, daily.holding/) via enumerate_log_sources,
    plus the two ancillary streams (new-{site}-access*, cmsdebug*) which
    only ever live flat in daily/.  After a successful move, rmdir any
    daily/YYYY/ or daily.holding/ subdir that just became empty.
    """
    # Snapshot now so we can rmdir empties after the move; only dirs that
    # actually contained one of the files we're moving are candidates.
    candidate_parents: set[Path] = set()

    def archive(files: list[Path], dst_dir: Path, label: str) -> None:
        if not files:
            return
        log.info(f"[archive-logs] {label}: {len(files)} file(s)")
        if dry_run:
            for f in files:
                log.info(f"  [dry-run] would gzip+move: {f} -> {dst_dir}/")
            return
        dst_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            candidate_parents.add(f.parent)
            gz = f if f.suffix == ".gz" else _gzip_in_place(f)
            dst = _numbered_backup_dst(dst_dir / gz.name)
            shutil.move(str(gz), str(dst))
            log.info(f"  archived: {f.name} -> {dst}")

    # Primary streams: walked across all source dirs.
    archive(_source_files_matching("access", date_filter), HTTPD_IMPORTED, "access")
    archive(_source_files_matching("auth",   date_filter), HZ_IMPORTED,   "auth")

    # Ancillary streams: only ever live flat in daily/.  Kept on the old
    # direct-glob path because they have no year-subdir / holding variant.
    def pat(base: str) -> str:
        return f"{base}{date_filter}*" if date_filter else base

    def archive_flat(src_dir: Path, dst_dir: Path, glob: str, label: str) -> None:
        files = sorted(src_dir.glob(glob))
        archive(files, dst_dir, label)

    archive_flat(HTTPD_DAILY, HTTPD_IMPORTED, pat(f"new-{SITE}-access*log*"), "new-access")
    archive_flat(HZ_DAILY,    HZ_IMPORTED,    pat("cmsdebug*log*"),           "cmsdebug")

    # Cleanup: rmdir daily/YYYY/ and daily.holding/ subdirs we just drained.
    # Never touch HTTPD_DAILY / HZ_DAILY themselves — those are standard.
    for d in candidate_parents:
        if d == HTTPD_DAILY or d == HZ_DAILY:
            continue
        _rmdir_if_empty(d)

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
        do_identify_bots(str(STAGED_APACHE), dry_run=dry_run)
        do_import_apache(str(STAGED_APACHE), dry_run=dry_run)
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


def _record_imported_source(cur, filename: str, target_table: str,
                            pk_start: int | None, pk_end: int | None,
                            row_count: int) -> bool:
    """INSERT IGNORE into imported_sources, then UPDATE pk_range if we're
    the first to record this (filename, target) pair.  Returns True if
    this was a fresh insert (caller should do the data INSERT), False
    if a prior committed run already recorded it (caller should skip
    the data INSERT — typical post-commit-pre-move crash recovery)."""
    _, _, _, metrics_db = db_credentials()
    cur.execute(
        f"INSERT IGNORE INTO {metrics_db}.imported_sources "
        f"(filename, target_table, imported_at) VALUES (%s, %s, NOW())",
        (filename, target_table))
    if cur.rowcount == 0:
        return False
    src_id = cur.lastrowid
    cur.execute(
        f"UPDATE {metrics_db}.imported_sources "
        f"SET pk_start = %s, pk_end = %s, row_count = %s "
        f"WHERE id = %s",
        (pk_start, pk_end, row_count, src_id))
    return True


def _move_to_imported(src_path: Path, imported_dir: Path) -> None:
    """Move a source log file from daily/ to imported/.  Same-filesystem
    rename is atomic; cross-filesystem falls back to copy+unlink via
    shutil.move (rare but not catastrophic — if it fails after the
    transaction committed, the imported_sources row makes the next
    tick's retry skip the data INSERT and just retry the move).

    Hardlink edge case: if src and dst refer to the same inode (e.g., an
    operator hardlinked imported/X.gz into daily.holding/X.gz to drive a
    re-import without consuming disk), POSIX rename() is defined as a
    no-op and shutil.move follows suit — leaving src in place.  That
    would make the orchestrator re-pick the same file on every tick,
    looping forever.  Detect this and unlink src instead."""
    imported_dir.mkdir(parents=True, exist_ok=True)
    dst = imported_dir / src_path.name
    if dst.exists() and src_path.stat().st_ino == dst.stat().st_ino:
        src_path.unlink()
        log.info(f"  -> {dst} (src was hardlink to dst; unlinked src)")
        return
    shutil.move(str(src_path), str(dst))
    log.info(f"  -> {dst}")


def _import_apache_file_atomic(src_path: Path, dry_run: bool = False) -> int:
    """Atomic per-file apache-log import:
      1. Stream-decompress source → STAGED_APACHE
      2. Open conn, BEGIN
      3. INSERT IGNORE imported_sources(filename, 'web')
      4. If first-time: parse + INSERT into web (with inline per-day
         INSERT into webhits in the same loop); capture pk_start..pk_end;
         identify-bots.
      5. COMMIT — atomically commits the imported_sources row, web rows,
         and webhits rows
      6. Move source → imported/  (best-effort; failure is recoverable)

    Crash anywhere in 2-5 → InnoDB rolls back, file stays in daily/,
    next tick re-imports cleanly.  Crash between 5 and 6 → next tick
    sees imported_sources rowcount=0 → skips data, retries the move.

    Returns the number of rows inserted into `web` on this call (0 if
    the file was already committed by a prior run — crash-recovery
    path).  Callers use this to gate `rebuild_cascade_from`: no new
    base data means no downstream long-window invalidation.

    webhits no longer has its own imported_sources row: it commits in
    the same transaction as `web`, so `web_first` IS the signal.  If
    webhits ever drifts from `web`, `rebuild-webhits` regenerates it
    from the (filtered) `web` rows.
    """
    filename = src_path.name
    HZ_METRICS_STAGING.mkdir(parents=True, exist_ok=True)

    if dry_run:
        log.info(f"[import-apache-atomic] [dry-run] {filename}")
        return 0

    # Stage the file (overwrites any prior staging — idempotent).
    with open(STAGED_APACHE, "wb") as out:
        _stream_decompress(src_path, out)
    log.info(f"[import-apache-atomic] staged {filename} -> {STAGED_APACHE}")

    # do_import_apache + do_identify_bots both rely on the connection's
    # default schema for unqualified table refs (`exclude_list`,
    # `bot_useragents`, `INSERT INTO web …`, `INSERT INTO webhits …`).
    # Without database= here, MariaDB returns "(1046) No database
    # selected" on the first SELECT and the entire transaction rolls
    # back without importing the file or writing an imported_sources
    # marker.
    web_inserted = 0
    _, _, _, metrics_db = db_credentials()
    conn = _open_db(metrics_db)
    try:
        conn.begin()
        with conn.cursor() as cur:
            web_first = _record_imported_source(
                cur, filename, "web", None, None, 0)
            if web_first:
                _rc, pk_start, pk_end, n = do_import_apache(
                    str(STAGED_APACHE), conn=conn, dry_run=False)
                web_inserted = n or 0
                if pk_start is not None:
                    _, _, _, mdb = db_credentials()
                    cur.execute(
                        f"UPDATE {mdb}.imported_sources "
                        f"SET pk_start=%s, pk_end=%s, row_count=%s "
                        f"WHERE filename=%s AND target_table='web'",
                        (pk_start, pk_end, n, filename))
                do_identify_bots(str(STAGED_APACHE), conn=conn, dry_run=False)
            else:
                log.info(f"[import-apache-atomic] {filename}: web already "
                         f"committed by prior run; skipping data INSERT")
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception(f"[import-apache-atomic] rollback on {filename}")
        raise
    finally:
        conn.close()

    # Outside the transaction — best-effort move.  If this fails, the
    # imported_sources row makes the next attempt idempotent.
    try:
        _move_to_imported(src_path, HTTPD_IMPORTED)
    except OSError as e:
        log.warning(f"[import-apache-atomic] data committed for {filename} "
                    f"but move failed: {e}; next tick will retry the move")
    return web_inserted


def _import_auth_file_atomic(src_path: Path, dry_run: bool = False) -> int:
    """Atomic per-file cmsauth-log import.  Same shape as
    _import_apache_file_atomic but for userlogin.  Returns the number
    of rows inserted into `userlogin` on this call (0 on dry-run or
    crash-recovery skip)."""
    filename = src_path.name
    HZ_METRICS_STAGING.mkdir(parents=True, exist_ok=True)

    if dry_run:
        log.info(f"[import-auth-atomic] [dry-run] {filename}")
        return 0

    with open(STAGED_AUTH, "wb") as out:
        _stream_decompress(src_path, out)
    log.info(f"[import-auth-atomic] staged {filename} -> {STAGED_AUTH}")

    ul_inserted = 0
    # Match the same default-schema requirement as the apache helper —
    # do_import_auth's SELECTs and INSERTs are unqualified.
    _, _, _, metrics_db = db_credentials()
    conn = _open_db(metrics_db)
    try:
        conn.begin()
        with conn.cursor() as cur:
            ul_first = _record_imported_source(
                cur, filename, "userlogin", None, None, 0)
            if ul_first:
                _rc, pk_start, pk_end, n = do_import_auth(
                    str(STAGED_AUTH), conn=conn, dry_run=False)
                ul_inserted = n or 0
                if pk_start is not None:
                    _, _, _, mdb = db_credentials()
                    cur.execute(
                        f"UPDATE {mdb}.imported_sources "
                        f"SET pk_start=%s, pk_end=%s, row_count=%s "
                        f"WHERE filename=%s AND target_table='userlogin'",
                        (pk_start, pk_end, n, filename))
            else:
                log.info(f"[import-auth-atomic] {filename}: userlogin "
                         f"already committed by prior run; skipping")
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception(f"[import-auth-atomic] rollback on {filename}")
        raise
    finally:
        conn.close()

    try:
        _move_to_imported(src_path, HZ_IMPORTED)
    except OSError as e:
        log.warning(f"[import-auth-atomic] data committed for {filename} "
                    f"but move failed: {e}; next tick will retry the move")
    return ul_inserted


def do_import_day(date_str, dry_run=False) -> int:
    """Per-source-file atomic import for one day.  Each source file is
    its own commit unit: a partial import rolls back, file stays in
    daily/, next tick re-imports cleanly.  A successful import that
    crashes between COMMIT and the move-to-imported/ step is handled
    by the imported_sources UNIQUE constraint on retry.

    Returns the total number of base-table rows inserted across web
    and userlogin on this call — 0 if every source file was already
    committed by a prior run (crash-recovery / mark-dirty re-tick)."""
    with _timed_stage(f"import-day {date_str}"):
        access  = _source_files_matching("access", date_str)
        cmsauth = _source_files_matching("auth",   date_str)

        if dry_run:
            for f in access + cmsauth:
                log.info(f"    [dry-run] would import: {f}")
            if not access:
                log.info(f"    [dry-run] WARNING: no access log found for {date_str}")
            if not cmsauth:
                log.info(f"    [dry-run] WARNING: no cmsauth log found for {date_str}")
            return 0

        inserted = 0
        for src in access:
            inserted += _import_apache_file_atomic(src, dry_run=dry_run)
        for src in cmsauth:
            inserted += _import_auth_file_atomic(src, dry_run=dry_run)
        return inserted


def cmd_fetch_logs(args):
    return do_fetch_logs(args.date or None, dry_run=args.dry_run)


def cmd_archive_logs(args):
    return do_archive_logs(args.date or None, dry_run=args.dry_run)

@contextmanager
def _timed_stage(name: str):
    """Log a stage banner, run the body, then log the elapsed wallclock.

    Used at coarse pipeline boundaries (tool-metrics / usage-metrics /
    summary, the per-tick handlers, per-day import).  Makes manual ticks
    self-describing: an operator watching `tail -f manage.log` sees both
    which stage is running and how long it took, without needing an
    external profiler."""
    log.info("=== %s ===", name)
    t0 = time.monotonic()
    try:
        yield
    finally:
        dt = time.monotonic() - t0
        log.info("=== %s done in %.2fs ===", name, dt)


def _do_tool_metrics_stage(month_str, dry_run):
    """Run the per-month tool-metrics enrichment + stats chain in-process.
    Direct port of __process_tool_metrics.sh.

    Per-sub-stage timing wraps each heavy worker so the tick log lines
    up "stage X took Ys" — useful for spotting which step is dominating
    on big months.  sessionlog_metrics is normally tiny so most of
    these will be sub-second."""
    with _timed_stage("tool-metrics"):
        with _timed_stage("  import-hub-data"):
            do_import_hub_data(dry_run=dry_run)
        with _timed_stage("  resolve-dns sessionlog_metrics"):
            do_resolve_dns("metrics", "sessionlog_metrics", month_str,
                           dry_run=dry_run)
        with _timed_stage("  fill-domain sessionlog_metrics"):
            do_fill_domain("metrics", "sessionlog_metrics", month_str,
                           dry_run=dry_run)
        with _timed_stage("  fill-user-info sessionlog_metrics"):
            do_fill_user_info("metrics", "sessionlog_metrics", month_str,
                              dry_run=dry_run)
        with _timed_stage("  fill-ipcountry sessionlog_metrics"):
            do_fill_ipcountry("metrics", "sessionlog_metrics", month_str,
                              dry_run=dry_run)
        with _timed_stage("  gen-tool-stats"):
            do_gen_tool_stats(month_str,    dry_run=dry_run)
        with _timed_stage("  gen-tool-tops"):
            do_gen_tool_tops(month_str,     dry_run=dry_run)
        with _timed_stage("  gen-tool-toplists"):
            do_gen_tool_toplists(month_str, dry_run=dry_run)


def _do_usage_metrics_stage(month_str, dry_run, *, sessions=True):
    """Run the per-month web / toolstart / websessions enrichment chain
    in-process.  Direct port of __process_usage_metrics.sh.

    Per-sub-stage timing wraps each heavy worker (resolve-dns,
    fill-domain, fill-ipcountry, logfix-session, clean-bots) so the
    tick log shows exactly where the wallclock went — particularly
    important for big months (millions of rows) where one of these
    steps can dominate.

    `sessions` controls whether the session-bound sub-stages run.
    Pass `sessions=False` when called for the still-incomplete current
    month: logfix-session run mid-month creates a fresh-session boundary
    at every tick (rows already stamped with sessionid are skipped on
    the next pass, so a session that genuinely spans the tick boundary
    gets split).  Holding it back until month-close, when rebuild and
    catchup ticks run with `sessions=True`, eliminates that per-tick
    slicing — the only remaining boundary is the month-to-month one
    that the legacy methodology has always had.  Row-level enrichment
    (DNS, domain, country, clean-bots on web) is correct to run daily
    and still does — those steps don't depend on sessionid."""
    with _timed_stage("usage-metrics"):
        # Row-level enrichment — correct to run daily during the
        # current month; idempotent (only touches rows that haven't
        # been enriched yet via the various NULL-or-empty predicates).
        with _timed_stage("  import-hub-data"):
            do_import_hub_data(dry_run=dry_run)
        with _timed_stage("  middleware-wall"):
            do_middleware_wall(dry_run=dry_run)
        with _timed_stage("  middleware-cpu"):
            do_middleware_cpu( dry_run=dry_run)
        with _timed_stage("  resolve-dns web"):
            do_resolve_dns("metrics", "web",       month_str, dry_run=dry_run)
        with _timed_stage("  resolve-dns toolstart"):
            do_resolve_dns("metrics", "toolstart", month_str, dry_run=dry_run)
        with _timed_stage("  fill-domain web"):
            do_fill_domain("metrics", "web",       month_str, dry_run=dry_run)
        with _timed_stage("  fill-domain toolstart"):
            do_fill_domain("metrics", "toolstart", month_str, dry_run=dry_run)
        with _timed_stage("  clean-bots web"):
            do_clean_bots("web",         month_str, dry_run=dry_run)
        with _timed_stage("  fill-user-info toolstart"):
            do_fill_user_info("metrics", "toolstart",   month_str, dry_run=dry_run)
        with _timed_stage("  fill-ipcountry web"):
            do_fill_ipcountry("metrics", "web",         month_str, dry_run=dry_run)
        with _timed_stage("  fill-ipcountry toolstart"):
            do_fill_ipcountry("metrics", "toolstart",   month_str, dry_run=dry_run)

        # Session-level work — only sound to run when the month's
        # input set is stable.  Skipped during current-month daily
        # ticks (sessions=False); included in catchup, rebuild, and
        # month-close normal ticks where the month is complete.
        if sessions:
            with _timed_stage("  logfix-session"):
                do_logfix_session(month_str, dry_run=dry_run)
            with _timed_stage("  clean-bots websessions"):
                do_clean_bots("websessions", month_str, dry_run=dry_run)
            # websessions carries its own host / domain / ipcountry columns.
            # logfix-session copies host/domain from the constituent web
            # rows at coalesce time, but a session built before its web
            # rows were DNS-resolved (or skipped on a re-run because its
            # web rows were already sessionid-stamped) ends up with an
            # empty host — and an empty domain drops the session from the
            # org-class breakdown (summary_user_vals rowid 7/8 cols 8..11
            # join websessions.domain → domainclass with `dc.class > 0`).
            # Resolve + fill-domain websessions directly so its host/domain
            # are populated regardless of coalesce-time ordering, mirroring
            # the web / toolstart / sessionlog passes above.
            with _timed_stage("  resolve-dns websessions"):
                do_resolve_dns("metrics", "websessions", month_str, dry_run=dry_run)
            with _timed_stage("  fill-domain websessions"):
                do_fill_domain("metrics", "websessions", month_str, dry_run=dry_run)
            with _timed_stage("  fill-ipcountry websessions"):
                do_fill_ipcountry("metrics", "websessions", month_str, dry_run=dry_run)


def _do_summary_stage(month_str, dry_run, *, periods=None):
    """Run the per-month rolling-window summary stage in-process.
    Direct port of __process_usage_metrics_summary.sh.

    periods: iterable of period codes; None means all six (the legacy
    default).  Catchup-mode ticks pass (1,) so they only write this-month
    cells; the long-window periods (0/3/12/13/14) for backfilled months
    get a correctness rebuild later via the rebuild-mode sweep.

    andmore_usage is suppressed when periods restricts to (1,) — andmore
    iterates periods 1/12/14 against the hub DB; touching 12/14 on a
    backfilled month would write wrong rolling/all-time numbers that
    we'd have to redo anyway."""
    with _timed_stage("summary"):
        do_import_hub_data(dry_run=dry_run)
        do_summarize_month(month_str, periods=periods, dry_run=dry_run)
        catchup_only = periods is not None and set(periods) == {1}
        if not catchup_only:
            do_andmore_usage(month_str, dry_run=dry_run)


def do_analyze(month_str, dry_run=False, *, sessions=True):
    """Run the two enrichment stages — tool-metrics and usage-metrics — in
    sequence.  Wraps __process_tool_metrics.sh + __process_usage_metrics.sh
    from the legacy pipeline; called by cmd_analyze and by the catch-up
    loop in cmd_run.

    `sessions=False` skips the session-bound sub-stages (logfix-session
    and websessions-bound clean-bots / fill-ipcountry) — used by
    normal-mode for the still-incomplete current month, where running
    logfix-session daily slices sessions at the tick boundary.  See
    `_do_usage_metrics_stage` for details."""
    month_str = month_str or None
    _do_tool_metrics_stage(month_str, dry_run)
    _do_usage_metrics_stage(month_str, dry_run, sessions=sessions)


def do_summarize(month_str, dry_run=False, *, periods=None):
    """Run the per-month rolling-window summary stage.  Wraps
    __process_usage_metrics_summary.sh — called by cmd_summarize and by
    the catch-up loop in cmd_run after do_analyze completes.

    periods: iterable subset of period codes (default: all six).  Catchup
    passes (1,) so it stays cheap and skips long-window periods whose
    correctness depends on months that haven't been backfilled yet."""
    _do_summary_stage(month_str or None, dry_run, periods=periods)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args):
    """Print pipeline state: orchestrator mode + transition cursors from
    pipeline_state, counts and date spans of files awaiting import (across
    daily/, daily/YYYY/, daily.holding/), already-imported logs, and the
    current resolve-dns settings.  Read-only — logs to stderr + the
    configured HZMETRICS_LOG file, no DB writes, no exit code."""
    def summarize_files(files, label):
        count = len(files)
        if count == 0:
            log.info(f"  {label}: 0")
            return
        oldest, newest = files[0][0], files[-1][0]
        span = f"({oldest})" if oldest == newest else f"({oldest} .. {newest})"
        log.info(f"  {label}: {count}  {span}")

    def summarize_dir(directory, pattern, label):
        summarize_files(dated_files(directory, pattern), label)

    log.info("=== orchestrator state ===")
    try:
        state = read_state()
    except Exception as e:
        log.info(f"  (could not read pipeline_state: {e})")
        state = {}
    mode = state.get("mode", "normal")
    log.info(f"  mode             : {mode}")
    log.info(f"  last analyzed    : {state.get('analyzed', '(never)')}")
    if "catchup_started" in state:
        log.info(f"  catchup_started  : {state['catchup_started']}")
    try:
        dirty = sorted(get_dirty_months())
    except Exception:
        dirty = []
    if dirty:
        log.info(f"  dirty months     : {', '.join(dirty)}")
    if mode == "rebuild":
        cursor = state.get("rebuild_cursor", "(unset)")
        try:
            target = previous_month(date.today().strftime("%Y-%m"))
            if cursor != "(unset)" and cursor <= target:
                remaining = len(months_in_range(cursor, target))
                log.info(f"  rebuild_cursor   : {cursor}  ({remaining} month(s) "
                         f"remaining through {target})")
            else:
                log.info(f"  rebuild_cursor   : {cursor}  (past prev_month — "
                         f"next tick will transition to normal)")
        except Exception:
            log.info(f"  rebuild_cursor   : {cursor}")

    log.info("=== pending import (all source dirs) ===")
    summarize_files(enumerate_log_sources("access"), "httpd access")
    summarize_files(enumerate_log_sources("auth"),   "cmsauth     ")

    log.info("=== imported/ (already processed) ===")
    summarize_dir(HTTPD_IMPORTED, f"{SITE}-access*log*", "httpd  ")
    summarize_dir(HZ_IMPORTED,    "cmsauth*log*",        "hubzero")

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
    _require_complete_month(args.month, args.force)
    do_analyze(args.month, dry_run)
    do_summarize(args.month, dry_run)
    log.info(">>> done")


# ---------------------------------------------------------------------------
# summarize  (rolling-window aggregation; normally run once after catchup)
# ---------------------------------------------------------------------------

def cmd_summarize(args):
    dry_run = args.dry_run
    _require_complete_month(args.month, args.force)
    do_summarize(args.month, dry_run)
    log.info(">>> done")


# ---------------------------------------------------------------------------
# rebuild-summaries  (manual range resummarize — doesn't touch state["mode"])
# ---------------------------------------------------------------------------

def cmd_rebuild_summaries(args):
    """Resummarize an explicit range of months.  Useful for the
    post-catchup rebuild sweep when an operator wants to drive it
    manually (instead of letting cmd_run's rebuild mode do it tick by
    tick), and for one-offs after a data fix.

    Does NOT modify pipeline_state.mode — the catchup state machine in
    cmd_run keeps running independently.  An operator can therefore use
    this alongside an in-flight catchup, or after deliberately bypassing
    the state machine entirely."""
    dry_run = args.dry_run
    since   = args.since
    through = args.through or previous_month(date.today().strftime("%Y-%m"))

    if since > through:
        log.error(f"--since {since} is after --through {through}; nothing to do.")
        raise SystemExit(1)

    periods = None
    if args.periods:
        try:
            periods = tuple(int(p.strip()) for p in args.periods.split(","))
        except ValueError:
            log.error(f"--periods: expected comma-separated integers, got {args.periods!r}")
            raise SystemExit(1)
        bad = [p for p in periods if p not in SUMMARY_PERIODS_DEFAULT]
        if bad:
            log.error(f"--periods: each value must be one of "
                      f"{sorted(SUMMARY_PERIODS_DEFAULT)}, got {bad}")
            raise SystemExit(1)

    months = months_in_range(since, through)
    plabel = "all" if periods is None else ",".join(str(p) for p in periods)
    log.info(f"[rebuild-summaries] {len(months)} month(s) {since}..{through} "
             f"periods={plabel}{' [dry-run]' if dry_run else ''}")

    for m in months:
        log.info(f"=== {m} ===")
        do_summarize(m, dry_run, periods=periods)

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
    elif args.month:
        month_str = args.month
        days = pending_days_for_month(month_str)
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

    # `days` may be empty: --next can race with another importer that just
    # archived the only file, and --month can be invoked against a month
    # whose imports already completed.  In either case, skip the import
    # loop (check_order would IndexError on days[0]) and proceed to the
    # analyze/summarize tail on whatever's already in the DB.
    if days:
        log.info(f"{'[dry-run] would process' if dry_run else 'Processing'} {len(days)} day(s) for {month_str}")
        check_order(days[0], args.force)
        for date_str in days:
            log.info(f"--- {date_str} ---")
            do_import_day(date_str, dry_run)
    else:
        log.info(f"No new days pending for {month_str}; analyzing existing data.")

    if is_current_month(month_str) and not args.force:
        log.info(f">>> {month_str} is the current month — skipping analysis until it ends.")
        log.info(f"    Run: hzmetrics.py analyze --month {month_str}")
    else:
        log.info(f">>> {'[dry-run] would analyze' if dry_run else 'analyzing'} {month_str}")
        do_analyze(month_str, dry_run)
        log.info(f">>> {'[dry-run] would summarize' if dry_run else 'summarizing'} {month_str}")
        do_summarize(month_str, dry_run)

    log.info(">>> done")


# ---------------------------------------------------------------------------
# fill-geo
# ---------------------------------------------------------------------------

ACCESS_CFG = HZMETRICS_HOME / "conf" / "access.cfg"

def db_config() -> dict[str, str]:
    """Parse every $name = '…'; assignment in the access.cfg PHP file
    into a dict.  Defined variables typically include hub_dir, hub_db,
    metrics_db, db_host, db_user, db_pass, db_prefix.

    The cfg path defaults to {HZMETRICS_HOME}/conf/access.cfg.  Override
    via the HZMETRICS_ACCESS_CFG environment variable — used by the
    A/B test harness to point at a cfg that names the test DBs."""
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
    except pymysql.MySQLError:
        log.exception("[mysql] connect failed")
        return 1
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        return 0
    except pymysql.MySQLError:
        log.exception("[mysql] exec failed")
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

    # Build the WHERE clause with %s placeholders.  Note: this query also
    # contains the literal DATE_FORMAT(datetime, '%Y-%m'), whose '%' must be
    # doubled to '%%' once we pass params — pymysql treats %s/%(name)s as
    # placeholders and would otherwise mis-parse the format string.
    where = "dnload IS NULL"
    params: tuple = ()
    if start_month:
        where += " AND datetime >= %s"
        params = (f"{start_month}-01",)

    months = mysql_column(
        f"SELECT DISTINCT DATE_FORMAT(datetime,'%%Y-%%m') FROM {metrics_db}.web "
        f"WHERE {where} ORDER BY 1;",
        params,
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

        # Same %->%% caveat as the months query above: LIKE pattern's '%'
        # must be doubled when params=tuple is passed.
        regex = f"^/resources/.*\\.({ext_pattern})([?#]|$)"
        sql = (
            f"UPDATE {metrics_db}.web "
            f"SET dnload = IF("
            f"content LIKE '/resources/%%/download/%%' OR "
            f"content REGEXP %s, "
            f"1, 0) "
            f"WHERE datetime >= %s AND datetime < %s AND dnload IS NULL;"
        )

        if dry_run:
            log.info(f"{label}  [dry-run]")
        else:
            rc = mysql_exec(sql, (regex, m_start, m_end))
            if rc == 0:
                log.info(f"{label} done")
            else:
                log.error(f"{label} FAILED (rc={rc}); continuing with next month")

    if not dry_run:
        count = mysql_scalar(
            f"SELECT COUNT(*) FROM information_schema.statistics "
            f"WHERE table_schema='{metrics_db}' AND table_name='web' AND index_name='dnload';"
        )
        if count == 0:
            rc = mysql_exec(f"ALTER TABLE {metrics_db}.web ADD INDEX dnload (dnload);")
            if rc == 0:
                log.info(f"  Adding index on {metrics_db}.web(dnload) ... done")
            else:
                log.error(f"  Adding index on {metrics_db}.web(dnload) FAILED (rc={rc})")
        else:
            log.info(f"  Index on {metrics_db}.web(dnload) already exists.")


def cmd_backfill_dnload(args):
    dry_run = args.dry_run
    do_backfill_dnload(args.start, dry_run)
    log.info(">>> done")


# ---------------------------------------------------------------------------
# rebuild-webhits  (regenerate the `webhits` derived table from `web`)
# ---------------------------------------------------------------------------

def do_rebuild_webhits(month=None, *, dry_run=False):
    """Regenerate `webhits` for one month (or every month present in
    web) by re-aggregating from `web`.

    webhits is a derived table — one row per day with the count of
    kept `web` rows for that day.  Normally populated incrementally
    inline by do_import_apache (same loop, same transaction); this
    command exists for disaster recovery and for retroactively
    applying new filter rules to historical data:

      - filter logic changes (e.g. a new bot pattern is added to
        the chain in do_import_apache); old `web` rows have been
        deleted by a one-shot cleanup but webhits still reflects
        the pre-cleanup counts → rebuild from current `web`
      - operator ran `forget-import --table web` for some file;
        webhits rows for those days are now inflated → rebuild
        for the affected month
      - any cross-table drift between web and webhits detected by
        a future invariant check

    Per-month DELETE + INSERT.  webhits has no UNIQUE key so DELETE
    is unconditional within the month range.  Operates over months
    that actually exist in `web`; an empty month gets no webhits
    rows (consistent with the inline-populate behavior).
    """
    _, _, _, metrics_db = db_credentials()

    if month:
        # Single-month form.
        y, m = int(month[:4]), int(month[5:7])
        start = f"{month}-01"
        end = (f"{y + 1:04d}-01-01" if m == 12
               else f"{y:04d}-{m + 1:02d}-01")
        months = [(month, start, end)]
    else:
        # Enumerate every distinct YYYY-MM in `web`.  The `%%` doubling
        # is for pymysql param-substitution: even with an empty `()`
        # params tuple, pymysql treats `%` as significant and `%%`
        # becomes literal `%` in the SQL sent to MariaDB.  Passing None
        # / no-params would send `%%` literally and DATE_FORMAT would
        # return the string '%Y-%m' for every row (asked me how I know).
        rows = mysql_column(
            f"SELECT DISTINCT DATE_FORMAT(datetime,'%%Y-%%m') "
            f"FROM {metrics_db}.web ORDER BY 1;",
            ())
        months = []
        for m_str in rows:
            y, m = int(m_str[:4]), int(m_str[5:7])
            start = f"{m_str}-01"
            end = (f"{y + 1:04d}-01-01" if m == 12
                   else f"{y:04d}-{m + 1:02d}-01")
            months.append((m_str, start, end))

    if not months:
        log.info("  No months found in web table.")
        return

    log.info(f"  Will rebuild webhits for {len(months)} month(s)"
             f"{': ' + months[0][0] + ' .. ' + months[-1][0] if len(months) > 1 else ': ' + months[0][0]}")

    for m_str, start, end in months:
        if dry_run:
            existing = mysql_scalar(
                f"SELECT COUNT(*) FROM {metrics_db}.webhits "
                f"WHERE datetime >= %s AND datetime < %s;",
                (start, end))
            new_count = mysql_scalar(
                f"SELECT COUNT(DISTINCT DATE(datetime)) FROM {metrics_db}.web "
                f"WHERE datetime >= %s AND datetime < %s;",
                (start, end))
            log.info(f"  [dry-run] {m_str}: would replace "
                     f"{existing} webhits row(s) with {new_count} day(s)")
            continue

        mysql_exec(
            f"DELETE FROM {metrics_db}.webhits "
            f"WHERE datetime >= %s AND datetime < %s;",
            (start, end))
        mysql_exec(
            f"INSERT INTO {metrics_db}.webhits (datetime, hits) "
            f"SELECT DATE(datetime), COUNT(*) FROM {metrics_db}.web "
            f"WHERE datetime >= %s AND datetime < %s "
            f"GROUP BY DATE(datetime);",
            (start, end))
        # mysql_exec returns 0 on success (not rowcount), so count after.
        n = mysql_scalar(
            f"SELECT COUNT(*) FROM {metrics_db}.webhits "
            f"WHERE datetime >= %s AND datetime < %s;",
            (start, end)) or 0
        log.info(f"  {m_str}: rebuilt {n} day(s)")

    # webhits change → exactly one summary cell is affected:
    # summary_misc_vals[rowid=8] (the "Web server hits" dashboard
    # number, which reads SUM(hits) FROM webhits).  No other summary
    # cell depends on webhits — see _summary_build_misc_main and
    # docs/usage-tables.md.  So instead of triggering a full rebuild
    # cascade (which would also redo sessions, downloads, andmore, and
    # every other rowid — minutes per month, hours total), recompute
    # just that one cell for every (month, period) tuple whose window
    # touches the rebuilt months.  Typical cost: <1 s for the whole
    # range.
    if months:
        _refresh_misc_hits_for_months(months[0][0], dry_run)


def _refresh_misc_hits_for_months(oldest_touched: str, dry_run: bool) -> None:
    """Recompute summary_misc_vals[rowid=8] for every (month, period)
    cell whose window touches `oldest_touched` or later.  No other
    summary cell reads from `webhits`, so this is the complete fix
    for a webhits-only change — orders of magnitude cheaper than the
    rebuild cascade (which would also redo sessions, downloads, etc.)
    that fires for changes affecting `web` row content."""
    cfg = db_config()
    metrics_db = cfg.get("metrics_db", "")
    today_ym = date.today().strftime("%Y-%m")
    prev = previous_month(today_ym)
    if oldest_touched > prev:
        log.info(f"  no summary_misc_vals cells to refresh "
                 f"(oldest_touched {oldest_touched} > prev_month {prev})")
        return

    affected = months_in_range(oldest_touched, prev)
    periods = (PERIOD_CAL_YEAR, PERIOD_MONTH, PERIOD_QUARTER,
               PERIOD_ROLLING_12, PERIOD_FISCAL_YR, PERIOD_ALL_TIME)
    total = len(affected) * len(periods)

    if dry_run:
        log.info(f"  [dry-run] would refresh summary_misc_vals[rowid=8] "
                 f"for {len(affected)} month(s) x {len(periods)} period(s) "
                 f"= {total} cell(s)")
        return

    log.info(f"  refreshing summary_misc_vals[rowid=8] for "
             f"{len(affected)} month(s) x {len(periods)} period(s) "
             f"= {total} cell(s)")
    table = f"{metrics_db}.summary_misc_vals"
    conn = _open_db(metrics_db)
    try:
        with conn.cursor() as cur:
            for ym in affected:
                dthis = f"{ym}-00"
                for period in periods:
                    dstart, dstop = period_dates(ym, period)
                    cur.execute(
                        f"SELECT SUM(hits) FROM {metrics_db}.webhits "
                        f"WHERE datetime > %s AND datetime < %s",
                        (dstart, dstop))
                    r = cur.fetchone()
                    # Same NULL→'' coercion as _summary_build_misc_main:
                    # empty window means empty string, not 0.
                    v = (r[0] if r and r[0] is not None else '')
                    _summary_write_cell(cur, table, 8, 1, dthis, period, v, 1)
        conn.commit()
    finally:
        conn.close()
    log.info(f"  refreshed {total} cell(s)")


def cmd_rebuild_webhits(args):
    if not args.all and not args.month:
        log.error("rebuild-webhits: must specify --month YYYY-MM or --all")
        return 2
    if args.all and args.month:
        log.error("rebuild-webhits: --month and --all are mutually exclusive")
        return 2
    do_rebuild_webhits(args.month, dry_run=args.dry_run)
    log.info(">>> done")
    return 0


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

def _whoisonline_clear_stale(conn, hub_db, db_prefix):
    """Delete session_geo rows older than WHOISONLINE_IDLE_TIME seconds."""
    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM {hub_db}.{db_prefix}session_geo "
            f"WHERE (UNIX_TIMESTAMP() - time) > %s",
            (WHOISONLINE_IDLE_TIME,))


def _whoisonline_populate_from_session(conn, hub_db, db_prefix):
    """INSERT IGNORE fresh rows from jos_session — existing rows in
    session_geo are kept so their resolved host/geo data survives."""
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT IGNORE INTO {hub_db}.{db_prefix}session_geo "
            f"(ip, session_id, username, time, guest, userid) "
            f"SELECT ip, session_id, username, time, guest, userid "
            f"FROM {hub_db}.{db_prefix}session "
            f"WHERE (UNIX_TIMESTAMP() - time) < %s "
            f"GROUP BY ip, username",
            (WHOISONLINE_IDLE_TIME,))


def _whoisonline_propagate_known(conn, hub_db, db_prefix):
    """Copy (host, domain) from one already-resolved session_geo row to
    every other row carrying the same IP — avoids re-resolving an IP
    we've already mapped earlier in this session."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT ip, host, domain FROM {hub_db}.{db_prefix}session_geo "
            f"WHERE host <> '' AND host <> '(unknown)'")
        for ip, host, domain in cur.fetchall():
            cur.execute(
                f"UPDATE {hub_db}.{db_prefix}session_geo "
                f"SET host = %s, domain = %s WHERE ip = %s",
                (host, domain, ip))


def _whoisonline_resolve_dns(conn, hub_db, db_prefix):
    """Reverse-resolve any still-unresolved IPs via aiodns (batched —
    replaces the PHP's per-IP fork/exec to `host`) and write the
    resulting host + domain back to session_geo."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT ip FROM {hub_db}.{db_prefix}session_geo "
            f"WHERE (host = '' OR host IS NULL) AND ip <> ''")
        unresolved = [r[0] for r in cur.fetchall()]
    if not unresolved:
        return

    log.info(f"[whoisonline] resolving {len(unresolved)} IP(s) via aiodns")
    try:
        _r = _DnsResolver(DNS_NAMESERVER,
                          min(len(unresolved), DNS_CONCURRENCY),
                          DNS_TIMEOUT)
        try:
            pairs = _r.resolve(unresolved)
        finally:
            _r.close()
    except ImportError as e:
        # aiodns is a declared hard dependency in pyproject.toml; reaching
        # this branch means a broken / partial install.  Fall back to
        # leaving the IPs unresolved (host==ip) so the row still gets a
        # session_geo entry, but warn loudly so ops notice the host column
        # is wrong.
        log.warning(f"[whoisonline] aiodns unavailable ({e}); "
                    f"{len(unresolved)} session(s) will have "
                    f"host=ip (install via: sudo -H python3.11 -m pip install aiodns)")
        pairs = [(ip, ip) for ip in unresolved]

    with conn.cursor() as cur:
        for ip, host in pairs:
            host = host if host and host != '?' else ip
            domain = _whoisonline_get_domain(ip, host)
            cur.execute(
                f"UPDATE {hub_db}.{db_prefix}session_geo "
                f"SET host = %s, domain = %s WHERE ip = %s",
                (host, domain, ip))


def _whoisonline_fill_geo(conn, hub_db, db_prefix, metrics_db):
    """For session_geo rows missing ipLATITUDE, look up GeoIP data and
    write country / region / city / lat / lng / bot columns."""
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


def _whoisonline_write_xml(conn, hub_db, db_prefix, xml_file):
    """Render the <markers> XML consumed by the hub's Google Maps widget
    from the current session_geo state and write it to xml_file."""
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
        log.info("[whoisonline] missing hub_db / hub_dir in access.cfg")
        return 2

    map_dir = Path(hub_dir) / "app/site/stats/maps"
    if not map_dir.is_dir():
        map_dir = Path(hub_dir) / "site/stats/maps"
    xml_file = map_dir / "whoisonline.xml"
    if not map_dir.is_dir():
        log.info(f"[whoisonline] map dir missing: {map_dir}")
        return 2

    if dry_run:
        log.info(f"[whoisonline] dry-run: would update {hub_db}.{db_prefix}session_geo "
                 f"and write {xml_file}")
        return 0

    conn = _open_db()
    try:
        _whoisonline_clear_stale(conn, hub_db, db_prefix)
        _whoisonline_populate_from_session(conn, hub_db, db_prefix)
        _whoisonline_propagate_known(conn, hub_db, db_prefix)
        _whoisonline_resolve_dns(conn, hub_db, db_prefix)
        _whoisonline_fill_geo(conn, hub_db, db_prefix, metrics_db)
        _whoisonline_write_xml(conn, hub_db, db_prefix, xml_file)
        # PHP runs clear_stale_sessions twice — keep the same cleanup pass
        # at the end so a session that aged out mid-tick doesn't ship.
        _whoisonline_clear_stale(conn, hub_db, db_prefix)
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
    # Self-repair before any DB-touching work.  No-op for non-apache
    # users (dev shells, root, A/B harness); on apache it lazy-creates
    # the install tree + metrics DB + tables on a fresh hub so cron can
    # take a brand-new access.cfg straight to a working pipeline
    # without an operator-driven setup-db / migrate sequence.
    if not args.dry_run:
        _self_bootstrap()

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

# Built-in defaults.  Overridable by HZMETRICS_HOME/conf/hzmetrics.conf
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

HZMETRICS_CONF = HZMETRICS_HOME / "conf" / "hzmetrics.conf"


def _read_dns_config():
    """Resolve DNS-related settings from config file → env vars → defaults.

    Tolerant of an unreadable config: PermissionError / FileNotFoundError
    silently fall through to env vars and built-in defaults (so the script
    keeps working when invoked by a user without conf-dir access)."""
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
    hzmetrics.py commands don't pay the dep if they don't need it.

    Sets long server-side session timeouts on every connection:

      - wait_timeout / interactive_timeout: how long the server keeps an
        idle connection before killing it (default usually 300s).  Lifted
        to 24h so an ALTER TABLE that takes 30 min can run without the
        server killing the connection on us.
      - net_read_timeout / net_write_timeout: per-packet IO waits
        (defaults 30s and 60s).  Lifted to 24h for the same reason —
        observed "Lost connection during query" on a multi-minute MyISAM
        ALTER, which the longer wait fixes.

    Cost: 3 extra fast statements per connect.  Negligible vs. the
    failure mode of pymysql dropping mid-ALTER and leaving the migration
    half-applied."""
    import pymysql
    host, user, password, _ = db_credentials()
    # Connection charset matches the persistent schema — metrics tables
    # are utf8mb3_general_ci (as is the bulk of the hub DB we read).
    # Using utf8mb4 here makes every temp table inherit utf8mb4_general_ci
    # by default, which would force cross-collation JOINs against the
    # utf8mb3 tables and silently degrade query plans (the eq_ref-via-PK
    # path is rejected by the optimizer under mismatched collations).
    conn = pymysql.connect(
        host=host, user=user, password=password,
        database=database, autocommit=True, charset="utf8mb3",
    )
    with conn.cursor() as cur:
        cur.execute("SET SESSION wait_timeout = 86400")
        cur.execute("SET SESSION net_read_timeout = 86400")
        cur.execute("SET SESSION net_write_timeout = 86400")
    return conn

class _DnsResolver:
    """Owns one asyncio event loop + one aiodns resolver for the lifetime
    of a resolve-dns run, so repeated batch resolution reuses a single
    loop.

    The previous pattern — asyncio.run(_resolve_ips_async(...)) once per
    10K-IP batch — created and immediately closed a fresh event loop on
    every call.  A wait_for-cancelled c-ares query then fired its socket
    teardown callback against the already-closed loop, flooding stderr
    with 'RuntimeError: Event loop is closed' (hundreds of tracebacks on
    a 2 M-IP month).  A persistent loop lets that teardown run while the
    loop is still alive; close() cancels outstanding queries and pumps
    the loop once before tearing it down, in the right order.

    Usage:
        r = _DnsResolver(nameserver, concurrency, timeout)
        try:
            pairs = r.resolve(batch_of_ips)    # repeatable, one batch each
        finally:
            r.close()

    Constructing raises ImportError if aiodns is missing — callers that
    must degrade gracefully (whoisonline) wrap construction in try/except.
    """
    def __init__(self, nameserver, concurrency, timeout):
        import asyncio
        import aiodns            # ImportError propagates to the caller
        self._asyncio = asyncio
        self._aiodns = aiodns
        self._timeout = timeout
        self._concurrency = concurrency
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        if not nameserver or str(nameserver).strip().lower() == "system":
            self._resolver = aiodns.DNSResolver(timeout=timeout, loop=self._loop)
        else:
            self._resolver = aiodns.DNSResolver(
                nameservers=[nameserver], timeout=timeout, loop=self._loop)

    async def _resolve_async(self, ips):
        asyncio = self._asyncio
        aiodns = self._aiodns
        sem = asyncio.Semaphore(self._concurrency)
        async def one(ip):
            async with sem:
                try:
                    # Hard asyncio-level backstop around aiodns's own
                    # timeout: c-ares occasionally never completes a query
                    # (stuck UDP sockets that would hang gather forever on
                    # a 2 M-IP batch).  wait_for fires even when the c-ares
                    # timer doesn't, cancels the query, and lets gather
                    # finish.  +1s lets aiodns's cleaner timeout win when it
                    # works, falling back to the hard cancel otherwise.
                    r = await asyncio.wait_for(
                        self._resolver.gethostbyaddr(ip),
                        timeout=self._timeout + 1)
                    return ip, (r.name if r and r.name else "?")
                except (aiodns.error.DNSError, asyncio.TimeoutError):
                    return ip, "?"
        return await asyncio.gather(*(one(ip) for ip in ips))

    def resolve(self, ips):
        """Resolve a batch of IPs to [(ip, host_or_'?'), ...]."""
        return self._loop.run_until_complete(self._resolve_async(ips))

    def close(self):
        """Cancel outstanding queries and tear the loop down in order so
        pycares' socket cleanup runs on a live loop, not a closed one."""
        try:
            self._resolver.cancel()
        except Exception:
            pass
        try:
            # Pump the loop once so any pending c-ares teardown callbacks
            # run while the loop is still alive.
            self._loop.run_until_complete(self._asyncio.sleep(0))
        except Exception:
            pass
        self._loop.close()
        self._asyncio.set_event_loop(None)

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

    # Use one pymysql connection for the whole flow.  Temporary tables
    # are per-connection so they have to share this conn.
    #
    # Memory shape: a big month can produce >1 M distinct unresolved IPs.
    # Loading the full list into Python (the old fetchall pattern) costs
    # ~100 MB of object overhead and combined with InnoDB buffer pool
    # working set tripped the OOM killer on a tight host.  The streaming
    # variant below keeps Python's footprint bounded by `DNS_BATCH_SIZE`
    # — the working set sits on disk in `_dns_tmp` (InnoDB temp table)
    # and we fetch + resolve + write back one batch at a time.
    DNS_BATCH_SIZE = 10000

    # InnoDB holds row locks for every row inserted by a statement until
    # statement end; on a small innodb_buffer_pool the lock table caps
    # how many rows one INSERT can touch (e.g., a 128 MB pool overflows
    # at ~500 K row locks).  A single INSERT IGNORE … SELECT spanning
    # millions of unresolved IPs exceeds that and fails with
    # OperationalError(1206) "The total number of locks exceeds the
    # lock table size".  Splitting phase 1 into date chunks keeps each
    # statement's lock footprint bounded — the PRIMARY KEY on
    # _dns_tmp.ip de-dupes across chunks, so the final working set is
    # identical to the one-shot form.
    #
    # _DNS_CHUNK_DAYS=1 is also a *query-plan* fix: MariaDB's optimizer
    # picks the `ip` index for date ranges ≥7 days (table-scan order, no
    # date locality → ~22 M-row scans), but switches to the `datetime`
    # index for 1-day ranges (~150 K-row scans).  Daily chunks therefore
    # both fit the lock budget and run the right plan.
    _DNS_CHUNK_DAYS = 1
    conn = _open_db(db_name)
    try:
        with conn.cursor() as cur:
            # Phase 1: materialise the unresolved-IP working set into
            # _dns_tmp on disk via INSERT … SELECT — no Python list ever
            # holds the full IP set.  `host` column reserved for phase 2's
            # write-back; index lets us fetch "still null" batches fast.
            t_scan = time.monotonic()
            cur.execute(
                "CREATE TEMPORARY TABLE _dns_tmp ("
                "ip VARCHAR(45) NOT NULL PRIMARY KEY, "
                "host VARCHAR(255) NULL, "
                "KEY host_idx (host)) ENGINE=InnoDB")

            # Resolve chunk boundaries.  If either bound is unset we
            # probe the unresolved rows once to learn the actual range
            # — cheaper than a single unbounded INSERT and guarantees
            # we cover the same data as the original one-shot form.
            chunk_start, chunk_end = start_d, end_d
            if chunk_start is None or chunk_end is None:
                cur.execute(
                    f"SELECT MIN({d_col}), MAX({d_col}) FROM {table} "
                    f"WHERE (host IS NULL OR host = '')"
                )
                probe = cur.fetchone()
                if probe and probe[0] is not None:
                    if chunk_start is None:
                        chunk_start = probe[0].date()
                    if chunk_end is None:
                        chunk_end = probe[1].date() + timedelta(days=1)

            n_total = 0
            if chunk_start is not None and chunk_end is not None:
                cur_d = chunk_start
                chunks_total = max(1, (chunk_end - chunk_start).days // _DNS_CHUNK_DAYS + 1)
                ci = 0
                while cur_d < chunk_end:
                    nxt = min(cur_d + timedelta(days=_DNS_CHUNK_DAYS), chunk_end)
                    cur.execute(
                        f"INSERT IGNORE INTO _dns_tmp (ip) "
                        f"SELECT DISTINCT ip FROM {table} "
                        f"WHERE ip <> '' AND ip IS NOT NULL "
                        f"  AND {d_col} >= %s AND {d_col} < %s "
                        f"  AND (host IS NULL OR host = '')",
                        (f"{cur_d.isoformat()} 00:00:00",
                         f"{nxt.isoformat()} 00:00:00"),
                    )
                    n_total += cur.rowcount
                    ci += 1
                    cur_d = nxt
                    if chunks_total > 1 and (ci % 4 == 0 or cur_d >= chunk_end):
                        log.info(f"  scan: chunk {ci}/{chunks_total} "
                                 f"through {cur_d}, {n_total} distinct IPs so far")
            else:
                # No data at all (or table empty) — leave n_total = 0.
                pass

            scan_dt = time.monotonic() - t_scan
            log.info(f"[resolve-dns] {db_name}.{table} {scope_label}: "
                f"{n_total} unresolved IPs (ns={nameserver}, c={concurrency})  "
                f"scan={scan_dt:.1f}s")
            if n_total == 0 or dry_run:
                return 0

            # Phase 2: stream-resolve in batches.  Each iteration loads
            # at most DNS_BATCH_SIZE IPs into Python, runs aiodns, writes
            # results back into _dns_tmp.host.  Loop exits when there are
            # no more IPs with host IS NULL (everything resolved or
            # marked "?" for no-PTR).
            t_dns = time.monotonic()
            done = 0
            resolved_total = 0
            # One resolver + one event loop for every batch in this run
            # (see _DnsResolver) — avoids per-batch loop churn and the
            # 'Event loop is closed' teardown noise it produced.
            dns = _DnsResolver(nameserver, concurrency, timeout)
            try:
                while True:
                    cur.execute(
                        "SELECT ip FROM _dns_tmp WHERE host IS NULL LIMIT %s",
                        (DNS_BATCH_SIZE,),
                    )
                    batch = [r[0] for r in cur.fetchall()]
                    if not batch:
                        break
                    pairs = dns.resolve(batch)
                    # Persist results — "?" placeholder distinguishes "tried,
                    # no PTR" from "not yet attempted" (NULL) so the loop
                    # terminates correctly.
                    cur.executemany(
                        "UPDATE _dns_tmp SET host = %s WHERE ip = %s",
                        [(h, ip) for ip, h in pairs],
                    )
                    done += len(batch)
                    resolved_total += sum(1 for _, h in pairs if h != "?")
                    log.info(f"  resolved {done}/{n_total} so far "
                             f"(this batch: {sum(1 for _, h in pairs if h != '?')}"
                             f"/{len(batch)} got PTRs)")
            finally:
                dns.close()
            dns_dt = time.monotonic() - t_dns
            rate = done / dns_dt if dns_dt > 0 else 0
            log.info(f"[resolve-dns] resolved={resolved_total} no_ptr={done - resolved_total} "
                f"wall={dns_dt:.1f}s rate={rate:.0f} IPs/s")

            # Phase 3: write resolved hosts back to the target table via
            # JOIN-UPDATE.  The "?" placeholder is persisted on purpose
            # — it marks "we tried, no PTR available" and prevents the
            # next resolve-dns pass from retrying the same dead IPs
            # (the scan filter is `host IS NULL OR host = ''`, so a
            # row with host='?' falls out of the work set).  Matches
            # the legacy xlogfix_dns_v2.sh behavior; the pre-2026-05
            # variant of this port dropped the '?' writeback, which
            # caused every tick to re-resolve the same unresolvable
            # IPs and surfaced as a port_pipeline A/B divergence on
            # web.host / web.domain.  LOWER(d.host) normalises PTR
            # records on write so downstream fill-domain can JOIN on
            # t.host = d.host (indexed) instead of LOWER(t.host) =
            # d.host (function defeats the index).
            t_apply = time.monotonic()
            update_date_pred = _build_pred("t.")
            cur.execute(
                f"UPDATE {table} t INNER JOIN _dns_tmp d ON t.ip = d.ip "
                f"SET t.host = LOWER(d.host) "
                f"WHERE (t.host IS NULL OR t.host = '') {update_date_pred} "
                f"  AND d.host IS NOT NULL")
            updated = cur.rowcount
            apply_dt = time.monotonic() - t_apply
            log.info(f"[resolve-dns] applied: {updated} rows updated in {table}  "
                f"apply={apply_dt:.1f}s")
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

            # Two-step DELETE: SELECT id batches (consistent read, no
            # locks) → DELETE WHERE id IN (...) (locks only the N PKs).
            # A single DELETE … WHERE host LIKE %pat% over a full
            # week of a freshly-reimported month does a full scan of
            # the date range (LIKE with a leading wildcard has no
            # usable index) and InnoDB takes next-key locks on every
            # row examined, not just the matched ones — blowing the
            # lock table on a fresh-import 11.7 M-row month even at
            # 512 MB innodb_buffer_pool (OperationalError 1206).
            # The two-step form bounds locks to N matched rows per
            # DELETE statement; the SELECT side runs lock-free under
            # InnoDB's consistent-read snapshot.  Applied to both
            # filter kinds for symmetry — domain filters use an
            # indexed equality and rarely hit the limit, but a
            # popular bot domain on a big month could.
            _CHUNK = 50000
            def _delete_matching(where_pred, where_params):
                deleted_local = 0
                while True:
                    cur.execute(
                        f"SELECT id FROM {table} WHERE {where_pred} LIMIT {_CHUNK}",
                        where_params)
                    ids = [r[0] for r in cur.fetchall()]
                    if not ids:
                        break
                    placeholders = ",".join(["%s"] * len(ids))
                    cur.execute(
                        f"DELETE FROM {table} WHERE id IN ({placeholders})",
                        ids)
                    deleted_local += cur.rowcount
                    if len(ids) < _CHUNK:
                        break
                return deleted_local

            total_deleted = 0
            for c_start, c_end in chunks:
                for f in domain_filters:
                    total_deleted += _delete_matching(
                        "datetime > %s AND datetime <= %s AND domain = %s",
                        (c_start, c_end, f))
                for f in host_filters:
                    total_deleted += _delete_matching(
                        "datetime > %s AND datetime <= %s AND host LIKE %s",
                        (c_start, c_end, f))

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

def do_import_auth(input_file, *, batch_size=5000, dry_run=False, conn=None):
    """Parse a cmsauth-format file and INSERT IGNORE every recognized
    auth event (with action ∈ {login, simulation}) into metrics.userlogin.

    If `conn` is provided, the INSERTs run inside the caller's
    transaction (caller owns commit/rollback) and the function
    returns (rc, pk_start, pk_end, inserted) so the caller can record
    the PK range in imported_sources for crash recovery.

    Deliberately diverges from legacy `xlogimport_authlog.php` (master
    branch), which inserted every action type unfiltered and relied on a
    one-off DELETE via migration #4 to purge `detect` / `invalid` / `logout`
    rows that no analyze / summarize code path ever reads.  Without the
    insert-time filter, the next import-auth re-accumulates ~99.99% noise
    rows and migration #4's effect erodes immediately.  Skipping at parse
    time keeps userlogin small and avoids the periodic cleanup work.

    The change breaks byte-identical A/B parity for userlogin row counts;
    tests/ab/port_import_auth filters both legacy and new outputs to
    action ∈ {login, simulation} before diffing — same rows that the
    pipeline actually queries.

    input_file: path to the staged auth log (typically
    /var/log/hubzero/metrics/_hub_auth.log) or '-' for stdin.
    """
    cfg = db_config()
    metrics_db = cfg.get("metrics_db", "")
    if not metrics_db:
        msg = "[import-auth] missing metrics_db in access.cfg"
        log.info(msg)
        return (2, None, None, 0) if conn is not None else 2

    # Pull the IP exclusion list — use the caller's conn if we have
    # one (so we don't open a second connection alongside their
    # transaction), otherwise a short-lived one.
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute("SELECT filter FROM exclude_list WHERE type='ip'")
            ip_filters = [r[0] for r in cur.fetchall()]
    else:
        _fc = _open_db(metrics_db)
        try:
            with _fc.cursor() as cur:
                cur.execute("SELECT filter FROM exclude_list WHERE type='ip'")
                ip_filters = [r[0] for r in cur.fetchall()]
        finally:
            _fc.close()

    rows = []
    unrec = 0
    skipped_action = 0
    skipped_filter = 0
    total = 0
    # Track YYYY-MM seen in this file; >2 distinct months suggests
    # a logrotate failure (see import-apache's spillover check).
    months_seen: set = set()

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
            months_seen.add(dt[:7])
            if not user:
                user = "-"
            # Skip actions the pipeline never reads (detect / invalid / logout
            # are ~99.99% of the line volume on a typical hub).  Migration #4
            # exists to clean up rows accumulated before this filter landed.
            if action not in ("login", "simulation"):
                skipped_action += 1
                continue
            if user in ("hubstatus", "hubadmin"):
                skipped_filter += 1
                continue
            if _ip_excluded(ip, ip_filters):
                skipped_filter += 1
                continue
            rows.append((dt, uid, user, ip, action))

    log.info(f"[import-auth] parsed {total} line(s); "
        f"kept (action IN login/simulation) = {len(rows)}; "
        f"unrecognized = {unrec}; "
        f"filtered = {skipped_filter}; "
        f"other-action skipped = {skipped_action}")

    if dry_run or not rows:
        if conn is not None:
            return 0, None, None, 0
        return 0

    inserted = 0
    pk_start: int | None = None
    _owns_conn = (conn is None)
    if _owns_conn:
        conn = _open_db(metrics_db)
    try:
        with conn.cursor() as cur:
            sql = ("INSERT IGNORE INTO userlogin "
                   "(datetime, uidNumber, user, ip, action) "
                   "VALUES (%s, %s, %s, %s, %s)")
            for i in range(0, len(rows), batch_size):
                cur.executemany(sql, rows[i:i + batch_size])
                if pk_start is None and cur.lastrowid:
                    pk_start = cur.lastrowid
                inserted += cur.rowcount
    finally:
        if _owns_conn:
            conn.close()

    log.info(f"[import-auth] inserted {inserted} new row(s) into userlogin "
        f"(others were duplicates rejected by INSERT IGNORE)")

    # Spillover detection — mirrors do_import_apache.
    if len(months_seen) > 2:
        log.warning(
            f"[import-auth] spillover: rows from {len(months_seen)} distinct "
            f"months in one staged file: {sorted(months_seen)}. "
            f"Looks like a logrotate failure — original daily files may have "
            f"merged.  The catchup decision matrix will pick up the additional "
            f"months on subsequent ticks."
        )
    elif len(months_seen) == 2:
        log.info(f"[import-auth] rows span {sorted(months_seen)} "
                 f"(normal midnight/TZ-bleed)")

    pk_end = (pk_start + inserted - 1) if (pk_start is not None and inserted) else None
    if not _owns_conn:
        return 0, pk_start, pk_end, inserted
    return 0

def cmd_import_auth(args):
    return do_import_auth(args.input_file, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# fill-user-info  (assign countrycitizen, countryresident, orgtype on
#                  toolstart / sessionlog_metrics rows by joining to hub
#                  user profiles; ports xlogfix_user_info.php)
# ---------------------------------------------------------------------------

# Tables `fill-user-info` accepts as its <table> arg — must have a
# `user` column joinable against hub_db.<prefix>users.username.
FILL_USER_INFO_TABLES = ("toolstart", "sessionlog_metrics", "web", "websessions")

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
    # Added after 2025-06 analysis showed these dominating the table:
    "scrapy",      # Scrapy/2.11.2 — 43% of 2025-06 web rows on its own
    "prtg",        # PRTG Network Monitor probes (per-minute health checks)
    "pycurl",      # PycURL/* — typical health-check / curl-based monitor
    "yeti",        # Naver Yeti search-engine bot (+https://naver.me/spd)
]
# Whitelist overrides — remove these false positives after flagging.
BOT_UA_WHITELIST_LIKE = ["%searchtool%", "% feed/%"]

def _ua_is_bot(ua):
    lo = ua.lower()
    for f in BOT_UA_FILTERS:
        if f in lo:
            return True
    return False

def do_identify_bots(input_file, *, dry_run=False, conn=None):
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

    _owns_conn = (conn is None)
    if _owns_conn:
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
        if _owns_conn:
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

# do_import_webhits used to be a separate file-parser that re-read the
# staged apache log to populate `webhits`.  It was deleted when webhits
# became a derived table of `web`: do_import_apache now counts kept rows
# per day in the same loop and emits webhits rows in the same atomic
# transaction.  See rebuild-webhits for the regenerate-from-`web` path.


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


FILL_DOMAIN_TABLES = ("web", "websessions", "toolstart", "sessionlog_metrics")

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

            # Bulk update via temp table + JOIN.  Temp columns inherit
            # the connection's collation (utf8mb3_general_ci via
            # _open_db), which matches the persistent metrics tables —
            # so w.host = d.host is a same-collation comparison and the
            # optimizer can use d.host's PRIMARY KEY as eq_ref.
            # Cross-collation here would silently degrade the plan to
            # `_domain_tmp` ALL × web range (40 K × 700 K = 28 B probes
            # per chunk, observed at 3-4 h on 2025-05).
            cur.execute(
                "CREATE TEMPORARY TABLE _domain_tmp ("
                "host VARCHAR(255) NOT NULL PRIMARY KEY, "
                "domain VARCHAR(255)) ENGINE=Memory"
            )
            cur.executemany(
                "INSERT INTO _domain_tmp (host, domain) VALUES (%s, %s)",
                pairs,
            )
            # t.host = d.host (no LOWER): do_resolve_dns now writes host
            # values lowercase, and migration 36 backfilled existing rows
            # to lowercase, so this equality matches without case folding.
            # The function call removal lets the planner use the
            # web_host index (added by migration 37) — turning a per-host
            # full scan into an indexed lookup.
            #
            # Two strategies depending on table size:
            #
            # `web` (big — ~12 M rows / freshly-reimported month):
            #   chunk by PRIMARY KEY id range with FORCE INDEX(PRIMARY),
            #   so each statement does a sequential PK scan of one chunk
            #   and probes _domain_tmp (Memory hash) per row.  Locks are
            #   bounded to chunk size; PK-sequential scans are
            #   cache-friendly on a small buffer pool.  Prior approaches
            #   all foundered on the lock manager: a single whole-month
            #   JOIN-UPDATE overflowed (1206) or wedged for hours; per-day
            #   chunks didn't help because the planner drives the join off
            #   web_host and ignores the date predicate for locking;
            #   FORCE INDEX(datetime) bounded date scope but still wedged
            #   under lock-manager pressure on a freshly-imported month;
            #   per-host updates fell over on bot ISP hosts that own
            #   millions of rows each (static.vnpt.vn alone has 5.5 M).
            #
            # `toolstart` / `sessionlog_metrics` (small):
            #   single JOIN-UPDATE — the row count is tiny so lock-table
            #   capacity is not at risk and the simpler form is fine.
            if table == "web":
                # ~250 K id-range per chunk: each chunk's lock set is
                # ~500 K rows worst-case (PK scan plus probe), which fits
                # within the InnoDB lock budget at 512 MB buffer pool
                # with substantial headroom.
                _CHUNK_IDS = 250_000
                cur.execute(
                    f"SELECT MIN(id), MAX(id) FROM {table} WHERE 1=1{date_pred_sql}",
                    date_params,
                )
                row = cur.fetchone()
                id_lo, id_hi = (row if row else (None, None))
                base_update = (
                    f"UPDATE {table} w FORCE INDEX (PRIMARY) "
                    f"INNER JOIN _domain_tmp d ON w.host = d.host "
                    f"SET w.domain = d.domain "
                    f"WHERE w.id >= %s AND w.id < %s "
                    f"AND (w.domain = '' OR w.domain = '?' OR w.domain IS NULL) "
                    f"AND w.host <> '' AND w.host IS NOT NULL"
                )
                updated = 0
                if id_lo is not None and id_hi is not None:
                    cur_id = id_lo
                    total_chunks = max(1, (id_hi - id_lo) // _CHUNK_IDS + 1)
                    ci = 0
                    while cur_id <= id_hi:
                        nxt = cur_id + _CHUNK_IDS
                        cur.execute(base_update, (cur_id, nxt))
                        updated += cur.rowcount
                        ci += 1
                        cur_id = nxt
                        if ci % 10 == 0 or cur_id > id_hi:
                            log.info(f"  fill-domain: chunk {ci}/~{total_chunks} "
                                     f"(id<{cur_id}), {updated} row(s) updated so far")
            else:
                update_sql = (
                    f"UPDATE {table} t INNER JOIN _domain_tmp d ON t.host = d.host "
                    f"SET t.domain = d.domain "
                    f"WHERE (t.domain = '' OR t.domain = '?' OR t.domain IS NULL) "
                    f"AND t.host <> '' AND t.host IS NOT NULL"
                    f"{date_pred_sql.replace(d_col, 't.' + d_col)}"
                )
                cur.execute(update_sql, date_params)
                updated = cur.rowcount
            cur.execute("DROP TEMPORARY TABLE IF EXISTS _domain_tmp")

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
# /cron/tick (no trailing slash) was slipping the original /cron/tick/
# filter — added (?:/|$) to catch both forms.  Observed at 32 k hits/mo
# in the 2025-06 analysis.
_CRON_RE      = re.compile(r'^/cron/tick(?:[/?]|$)', re.IGNORECASE)
_SVN_RE       = re.compile(r'/projects/.+?/svn/\!svn/', re.IGNORECASE)
# Mailman archives — observed at ~33 % of 2025-06 web rows, ~97 % from
# Scrapy.  Mailing-list archive pages are a separate service that just
# happens to share the domain; they contribute no hub-usage signal.
_PIPERMAIL_RE = re.compile(r'^/pipermail/',        re.IGNORECASE)
_RESOURCES_RE = re.compile(r'^/resources/',        re.IGNORECASE)
# /register registration-form crawler probes.  2026-02..05 audit: 629 k
# hits in 3 months; top 2 UAs hit at 145 k and 100 k distinct IPs with
# ≈1 hit/IP — the same distributed-bot signature as the MSIE wave, just
# with current-Chrome UAs.  Empty-Referer gated rather than uncondit-
# ional: a real human reaching /register arrives via the login redirect
# or homepage with Referer set, so the gate preserves that ~1 % signal
# while still dropping the 99 % bot volume.  Matches the safety pattern
# of /login?return=, /resources/browse?, /citations/browse.
_REGISTER_RE  = re.compile(r'^/register(?:[/?]|$)', re.IGNORECASE)

# Crawler hits identifiable by URL pattern + empty Referer.  The 2025-05
# survey found 2.1 M rows in web for that month, of which:
#   /login?return=<base64>      773 k  (99.9 % empty Referer)
#   /resources/browse?<query>  1.20 M  (96-97 % empty Referer)
# Together ~93 % of the month.  Legitimate users hit /login?return= via
# a server redirect (so Referer is set) and reach /resources/browse via
# a search engine, internal nav, or another tag page (Referer set).
# Crawlers using browser-style User-Agents leave Referer empty.  These
# two regexes intentionally stay narrow — they only fire on the two
# patterns we measured, not on every empty-Referer hit.
#
# The `/?` in each regex makes the trailing slash optional: the CMS
# routes both `/login?return=…` and `/login/?return=…` to the same
# action (likewise `/resources/browse?…` and `/resources/browse/?…`),
# and crawlers hit both variants.  The 2023-12 audit found ~200 k
# slash-variant rows that the no-slash form had been missing.
_LOGIN_RETURN_RE = re.compile(r'^/login/?\?return=',         re.IGNORECASE)
_BROWSE_QUERY_RE = re.compile(r'^/resources/browse/?\?',     re.IGNORECASE)
# /citations/browse — same shape as /resources/browse?: paginated
# catalog walk + empty Referer.  2026-02..05 audit: 113 k hits, same
# distributed-bot UAs.  Broader regex (no `?` required) because real
# users reach the catalog landing page with a Referer set, so the
# empty-Referer gate alone separates bots from browsers.
_CITATIONS_BROWSE_RE = re.compile(r'^/citations/browse(?:[/?]|$)', re.IGNORECASE)

# /events/<year> archive walks.  2026-02..05 audit: a uniform ≈17-18 k
# hits per archive year going back to 2006, all with empty Referer.
# Real users browse near-current events; a flat hit-rate across 20
# years of past archives is a systematic crawler walking the index.
# Filter compares the URL's year against the apache log line's own
# datestamp year (NOT date.today()) — so a backfill of 2024 logs
# filters /events/<=2020, matching what a 2024-era operator would
# have considered "old."  Lookback bound by _EVENTS_ARCHIVE_LOOKBACK_YEARS.
_EVENTS_ARCHIVE_RE = re.compile(r'^/events/(\d{4})(?:[/?]|$)', re.IGNORECASE)
_EVENTS_ARCHIVE_LOOKBACK_YEARS = 3

# Pre-2016 Internet Explorer (Trident family).  Functionally extinct
# from the real-browser population after Microsoft EOL'd IE in 2022, but
# heavily spoofed by a long-running distributed bot family: 2024 the bot
# pounded `/resources/<id>/download/<URL>` (open-redirect probes), went
# quiet 2025-06 .. 2026-01, and returned 2026-02+ against a different
# URL set (citations/files/groups/outreach).  On the geodynamics hub,
# observed MSIE-Trident hits over four years: 0 (2022), 0 (2023),
# 69k (2024 bot bloom), 0 (2025), 190k+ (2026 bot wave) — i.e. zero
# legitimate traffic, all bot.  Filter is date-bound (>= 2022-01-01) so
# any future archival backfill of pre-EOL access logs isn't affected by
# the assumption that MSIE-Trident == bot.
_MSIE_TRIDENT_RE = re.compile(
    r"MSIE [789]\.0|MSIE 1[01]\.0|Trident/[4-7]\.0",
    re.IGNORECASE,
)
_MSIE_FILTER_FROM = "2022-01-01"

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
    if _PIPERMAIL_RE.match(url):       return True
    return False


def _is_referer_spam(url, referrer):
    """True iff the row is a crawler hit identifiable by URL pattern +
    empty Referer.  See _LOGIN_RETURN_RE / _BROWSE_QUERY_RE /
    _CITATIONS_BROWSE_RE / _REGISTER_RE for the patterns and the
    2025-05 / 2026-02 measurements that justified them."""
    if referrer and referrer != '-':
        return False
    return bool(
        _LOGIN_RETURN_RE.match(url)
        or _BROWSE_QUERY_RE.match(url)
        or _CITATIONS_BROWSE_RE.match(url)
        or _REGISTER_RE.match(url)
    )


def _is_archive_events_crawl(url, datestamp, referrer):
    """True iff the row is a crawler hit to /events/<old-year> with
    empty Referer.  Year cutoff is the log line's own datestamp year
    minus _EVENTS_ARCHIVE_LOOKBACK_YEARS — see _EVENTS_ARCHIVE_RE."""
    if referrer and referrer != '-':
        return False
    m = _EVENTS_ARCHIVE_RE.match(url)
    if not m:
        return False
    try:
        url_year = int(m.group(1))
        log_year = int(datestamp[:4])
    except (ValueError, TypeError):
        return False
    return url_year <= log_year - _EVENTS_ARCHIVE_LOOKBACK_YEARS


# _filter_apache_row was briefly extracted as a shared helper when
# do_import_webhits had its own parser; both are gone now (webhits
# became a derived table populated inline by do_import_apache and
# regeneratable via rebuild-webhits).  The filter chain inlines back
# into do_import_apache below.

def do_import_apache(input_file, *, batch_size=5000, dry_run=False, conn=None):
    """Parse an apache staged log file and INSERT eligible rows into
    metrics.web.  Faithful port of xlogimport_apache.php (the 1018cc2^
    snapshot — the column `dnload` did not exist yet in the legacy
    schema at that point).

    If `conn` is provided, the INSERTs run inside the caller's
    transaction (caller owns commit/rollback) and the function
    returns (return_code, pk_start, pk_end, inserted_count) so the
    caller can record the PK range in imported_sources for crash
    recovery.  If `conn` is None, the function opens its own short-
    lived connection with autocommit — backward-compatible path used
    by the standalone `import-apache` CLI for ad-hoc imports.

    Eligibility:
      - regex matches new or old apache log format
      - status=200, bytes>0, method ∈ {GET,POST}
      - IP / UA / URL not matched by exclude_list filters
      - useragent not present in metrics.bot_useragents (exact match)
      - URL not excluded by suffix/path rules, OR is under /resources/

    Does NOT set `web.dnload` inline.  The dnload column is populated
    separately by `backfill-dnload` (which fills historical months) and
    not at import time — matches the pre-1018cc2 legacy behavior.  An
    in-line dnload variant is sketched in commented-out code below the
    INSERT SQL if you ever want to flip back to the source-tree shape.
    """
    cfg = db_config()
    metrics_db = cfg.get("metrics_db", "")
    if not metrics_db:
        msg = "[import-apache] missing metrics_db in access.cfg"
        log.info(msg)
        return (2, None, None, 0) if conn is not None else 2

    # Load filter lists — use the caller's conn if we have one,
    # otherwise a short-lived one.
    def _load_filters(cur):
        cur.execute("SELECT filter FROM exclude_list WHERE type='ip'")
        ipf = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT filter FROM exclude_list WHERE type='useragent'")
        uaf = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT filter FROM exclude_list WHERE type='url'")
        urlf = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT useragent FROM bot_useragents")
        b = {r[0] for r in cur.fetchall()}
        return ipf, uaf, urlf, b

    if conn is not None:
        with conn.cursor() as cur:
            ip_filters, ua_filters, url_filters, bot_uas = _load_filters(cur)
    else:
        _fc = _open_db(metrics_db)
        try:
            with _fc.cursor() as cur:
                ip_filters, ua_filters, url_filters, bot_uas = _load_filters(cur)
        finally:
            _fc.close()

    log.info(f"[import-apache] loaded filters: "
        f"ip={len(ip_filters)} ua={len(ua_filters)} url={len(url_filters)} "
        f"bot_useragents={len(bot_uas)}")

    # Set web.dnload at insert time (post-1018cc2 form).  The earlier
    # pre-1018cc2 port left dnload NULL and required a separate
    # backfill-dnload pass — but production legacy never ran that pass
    # either, so dnload stayed NULL on every hub we've audited and the
    # downloaders / download-sessions cells in summary_misc_vals were
    # silently zero.  Setting at insert avoids the second pass and makes
    # the bug structurally impossible to reproduce.
    insert_sql = (
        "INSERT INTO web "
        "(datetime, content, ip, uidNumber, apache_pid, referrer, useragent, "
        "joomla_sessionid, site_cookie, auth_type, component_name, view_name, "
        "task_name, action_name, item_name, dnload) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
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
    skipped_ref = 0
    skipped_msie = 0
    skipped_events = 0
    dnload_set = 0  # counter for the dnload flag set per row
    # Per-day kept-row aggregate — emitted into `webhits` after the
    # main loop.  webhits is just COUNT(*) per day of `web` rows; doing
    # it inline avoids a second parse of the staged file and structurally
    # guarantees the two tables agree on what counts (replaces the
    # standalone do_import_webhits + _filter_apache_row split).
    daily_hits: dict = defaultdict(int)
    # Track distinct YYYY-MM seen in this file.  One staged file should
    # normally contain a single day's logs (so 1 month, or 2 across a
    # midnight TZ boundary).  >2 months in one file is almost always a
    # logrotate failure — log a warning so the operator can investigate.
    months_seen: set = set()

    _owns_conn = (conn is None)
    if _owns_conn:
        conn = _open_db(metrics_db)
    pk_start: int | None = None
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
                    months_seen.add(datestamp[:7])

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

                    # Filter chain — same order as legacy PHP, extended
                    # for the 2025-05 referer-spam and 2026-02 distributed-bot
                    # waves.  webhits is derived from the kept rows below
                    # (daily_hits counter), so this chain is the single
                    # source of truth for both tables.
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
                    # MSIE 7-11 / Trident — see _MSIE_TRIDENT_RE comment.
                    if (datestamp >= _MSIE_FILTER_FROM
                            and useragent and useragent != '-'
                            and _MSIE_TRIDENT_RE.search(useragent)):
                        skipped_msie += 1
                        continue
                    if _is_excluded_url(url) and not _RESOURCES_RE.match(url):
                        skipped_url += 1
                        continue
                    if _is_referer_spam(url, referrer):
                        skipped_ref += 1
                        continue
                    if _is_archive_events_crawl(url, datestamp, referrer):
                        skipped_events += 1
                        continue

                    dnload = 1 if _is_download_url(url) else 0
                    if dnload:
                        dnload_set += 1
                    rows_buf.append((
                        f"{datestamp} {timestamp}",
                        url, ip, uid, pid, referrer, useragent,
                        joomla_id, st_cookie, auth_type,
                        comp_name, view_name, task_name, actn_name, item_name,
                        dnload,
                    ))
                    daily_hits[datestamp] += 1

                    if len(rows_buf) >= batch_size and not dry_run:
                        cur.executemany(insert_sql, rows_buf)
                        if pk_start is None and cur.lastrowid:
                            pk_start = cur.lastrowid
                        inserted += cur.rowcount
                        rows_buf = []

            if rows_buf and not dry_run:
                cur.executemany(insert_sql, rows_buf)
                if pk_start is None and cur.lastrowid:
                    pk_start = cur.lastrowid
                inserted += cur.rowcount

            # webhits — one row per day with the kept-row count.  webhits
            # has no UNIQUE key per legacy semantics, so concurrent or
            # repeated imports of the same date append duplicate rows;
            # rebuild-webhits is the operator-facing reversal.
            if daily_hits and not dry_run:
                cur.executemany(
                    "INSERT INTO webhits (datetime, hits) VALUES (%s, %s)",
                    [(f"{d} 00:00:00", h) for d, h in sorted(daily_hits.items())])
    finally:
        if _owns_conn:
            conn.close()

    log.info(f"[import-apache] parsed {parsed}/{total} (unrecognized={unrec}); "
        f"eligible={len(rows_buf) if dry_run else inserted}; "
        f"skipped: status={skipped_status} filter={skipped_filter} "
        f"bot={skipped_bot} url={skipped_url} ref={skipped_ref} "
        f"msie={skipped_msie} events={skipped_events}; "
        f"dnload-flagged={dnload_set}; "
        f"webhits={sum(daily_hits.values())} across {len(daily_hits)} day(s)")

    # Spillover detection: a staged daily file containing rows from >2
    # distinct YYYY-MM is almost always a logrotate failure (1 month is
    # the norm, 2 is the TZ-bleed-across-midnight case).  The catchup
    # decision matrix will route the affected months correctly on the
    # next tick, but operators should know the file was mis-sized.
    if len(months_seen) > 2:
        log.warning(
            f"[import-apache] spillover: rows from {len(months_seen)} distinct "
            f"months in one staged file: {sorted(months_seen)}. "
            f"Looks like a logrotate failure — original daily files may have "
            f"merged.  The catchup decision matrix will pick up the additional "
            f"months on subsequent ticks."
        )
    elif len(months_seen) == 2:
        log.info(f"[import-apache] rows span {sorted(months_seen)} "
                 f"(normal midnight/TZ-bleed)")

    # Under single-writer flock, the inserted PKs are contiguous from
    # pk_start through pk_start + inserted - 1.  Return the range so
    # the caller can record it in imported_sources for crash recovery.
    pk_end = (pk_start + inserted - 1) if (pk_start is not None and inserted) else None
    if not _owns_conn:
        return 0, pk_start, pk_end, inserted
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

# Maximum IPs per multi-lookup request, per the upgraded ipinfo service
# contract (?n_ips=<csv>, ≤100 entries).  Single-IP lookups via the
# legacy ?n_ip= parameter still work and are used by whoisonline.
_IPCOUNTRY_BATCH_SIZE = 100

# Concurrency across batch requests.  Each batch is one HTTPS roundtrip;
# parallelism hides the per-request latency.  Conservative default
# because we're hitting a single shared hub server.
_IPCOUNTRY_HTTPS_CONCURRENCY = 10


def _ipgeo_https_batch(n_ips, *, url, hub_key, timeout):
    """Look up up to _IPCOUNTRY_BATCH_SIZE (100) IPs in one HTTPS round-trip
    via the n_ips= multi-IP endpoint.  Returns dict {n_ip: geo_dict} for
    every IP that resolved (per-ipset <status>_SUCCESS_); IPs that failed
    individually are absent from the dict.

    Pure HTTPS read — no DB I/O — safe to call from worker threads."""
    import urllib.request, xml.etree.ElementTree as ET
    if not n_ips:
        return {}
    if len(n_ips) > _IPCOUNTRY_BATCH_SIZE:
        raise ValueError(
            f"n_ips batch size {len(n_ips)} exceeds API limit "
            f"of {_IPCOUNTRY_BATCH_SIZE}")
    endpoints = [url] + [u for u in IPCOUNTRY_FALLBACKS if u != url]
    n_ips_csv = ",".join(str(n) for n in n_ips)
    root = None
    for ep in endpoints:
        full_url = f"{ep}/?&hub_key={hub_key}&n_ips={n_ips_csv}"
        try:
            with urllib.request.urlopen(full_url, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                root = ET.fromstring(text)
            break
        except (urllib.request.URLError, ET.ParseError, TimeoutError, OSError) as e:
            log.warning(f"ipinfo {ep} batch failed ({e}); trying next fallback")
            continue
    if root is None:
        return {}

    # Aggregate top-level status — only _INVALID_KEY_… is a fatal config
    # error we should surface.  _PARTIAL_ / _NO_IP_GEO_DATA_FOUND_ /
    # _SUCCESS_ are all "iterate the ipsets and trust each one's own status".
    top_status = (root.findtext('status') or '').strip()
    if top_status == '_INVALID_KEY_OR_KEY-HUB_HOSTNAME_MISMATCH_':
        log.warning("HUBzero.org IP-Geo location key invalid for this host. "
                    "Check the hub registration / hub_key setting.")
        return {}

    out: dict = {}
    for ipset in root.findall('ipset'):
        if (ipset.findtext('status') or '').strip() != '_SUCCESS_':
            continue
        try:
            n_ip = int((ipset.findtext('n_ip') or '').strip())
        except ValueError:
            continue
        out[n_ip] = {
            'n_ip':         n_ip,
            'countrySHORT': (ipset.findtext('countryCode') or '-').strip() or '-',
            'countryLONG':  (ipset.findtext('countryName') or '-').strip() or '-',
            'ipREGION':     (ipset.findtext('region')      or '-').strip() or '-',
            'ipCITY':       (ipset.findtext('city')        or '-').strip() or '-',
            'ipLATITUDE':   (ipset.findtext('lat')         or '-').strip() or '-',
            'ipLONGITUDE':  (ipset.findtext('long')        or '-').strip() or '-',
        }
    return out


def _ipgeo_lookup_batch(conn, hub_db, db_prefix, ips, *, url, hub_key,
                        timeout, ttl_days=90,
                        concurrency=_IPCOUNTRY_HTTPS_CONCURRENCY,
                        batch_size=_IPCOUNTRY_BATCH_SIZE):
    """Resolve a list of IPs to geo data using bulk cache + batched HTTPS.
    Returns dict {ip_str: geo_dict} for every IP that resolved successfully.

    Steps:
      1. Bulk SELECT of metrics_ipgeo_cache for all n_ips at once.
      2. For cache misses, split into batches of up to `batch_size` (100)
         and fire each batch as one HTTPS roundtrip — concurrent across
         batches via ThreadPoolExecutor.
      3. Bulk INSERT of new cache rows via ON DUPLICATE KEY UPDATE.

    Per-target-table UPDATEs stay with the caller — different tables
    need different filter clauses.
    """
    from concurrent.futures import ThreadPoolExecutor
    out: dict = {}
    if not ips:
        return out

    # Map ip-string → n_ip (int).  Skip invalid IPs entirely.
    ip_to_n = {}
    for ip in ips:
        n = _ip2long(ip)
        if n is not None:
            ip_to_n[ip] = n
    if not ip_to_n:
        return out

    # --- bulk cache lookup ---
    # NB: cache table's IP column is named `ip` and stores the int32-encoded
    # form (n_ip).  Same int values as ip_to_n.values(); only the column
    # name differs from the local variable convention.
    placeholders = ",".join(["%s"] * len(ip_to_n))
    cache_args = tuple(ip_to_n.values())
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT ip, countrySHORT, countryLONG, ipREGION, ipCITY, "
            f"ipLATITUDE, ipLONGITUDE FROM {hub_db}.{db_prefix}metrics_ipgeo_cache "
            f"WHERE ip IN ({placeholders}) "
            f"AND TO_DAYS(CURDATE()) - TO_DAYS(lookup_datetime) <= %s",
            cache_args + (ttl_days,),
        )
        cached = {row[0]: row for row in cur.fetchall()}

    # Reverse-map: n_ip back to string ip for the dict keys.
    n_to_ip = {n: ip for ip, n in ip_to_n.items()}

    for n_ip, row in cached.items():
        ip = n_to_ip[n_ip]
        out[ip] = {
            'n_ip':         n_ip,
            'countrySHORT': row[1], 'countryLONG': row[2],
            'ipREGION':     row[3], 'ipCITY':      row[4],
            'ipLATITUDE':   row[5], 'ipLONGITUDE': row[6],
        }

    miss_n_ips = [n for n in ip_to_n.values() if n not in cached]
    if not miss_n_ips:
        return out

    # --- batched HTTPS for cache misses ---
    chunks = [miss_n_ips[i:i + batch_size]
              for i in range(0, len(miss_n_ips), batch_size)]
    log.info(f"  ipgeo cache: {len(cached)} hit / {len(miss_n_ips)} miss → "
             f"{len(chunks)} HTTPS batch(es) of ≤{batch_size} "
             f"(concurrency={min(concurrency, len(chunks))})")

    def fetch(chunk):
        return _ipgeo_https_batch(chunk, url=url, hub_key=hub_key, timeout=timeout)

    new_rows = []
    workers = max(1, min(concurrency, len(chunks)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for batch_result in ex.map(fetch, chunks):
            for n_ip, geo in batch_result.items():
                ip = n_to_ip.get(n_ip)
                if ip is None:
                    continue
                out[ip] = geo
                new_rows.append((
                    geo['n_ip'], geo['countrySHORT'], geo['countryLONG'],
                    geo['ipREGION'], geo['ipCITY'],
                    geo['ipLATITUDE'], geo['ipLONGITUDE'],
                ))

    # --- bulk cache INSERT for new HTTPS results ---
    # Same caveat as the SELECT: the column is `ip` even though the
    # values are int32-encoded n_ips.
    # Chunked so a giant month (212k new IPs on websessions in 2025-05)
    # doesn't blow past max_allowed_packet — 1000 rows × 7 cols × ~100B
    # ≈ 700 KB SQL, well under any reasonable limit.
    if new_rows:
        insert_chunk = 1000
        with conn.cursor() as cur:
            for i in range(0, len(new_rows), insert_chunk):
                batch = new_rows[i:i + insert_chunk]
                values_clause = ", ".join(["(%s, %s, %s, %s, %s, %s, %s)"] * len(batch))
                params: list = [v for row in batch for v in row]
                cur.execute(
                    f"INSERT INTO {hub_db}.{db_prefix}metrics_ipgeo_cache "
                    f"(ip, countrySHORT, countryLONG, ipREGION, ipCITY, "
                    f"ipLATITUDE, ipLONGITUDE) VALUES {values_clause} "
                    f"ON DUPLICATE KEY UPDATE "
                    f"countrySHORT=VALUES(countrySHORT), "
                    f"countryLONG=VALUES(countryLONG), "
                    f"ipREGION=VALUES(ipREGION), ipCITY=VALUES(ipCITY), "
                    f"ipLATITUDE=VALUES(ipLATITUDE), ipLONGITUDE=VALUES(ipLONGITUDE)",
                    tuple(params),
                )

    return out


def do_fill_ipcountry(db_key, table, date_spec=None, *, all_dates=False,
                      url=IPCOUNTRY_URL, hub_key=IPCOUNTRY_HUB_KEY,
                      timeout=IPCOUNTRY_TIMEOUT, dry_run=False):
    """Port of xlogfix_ipcountry.php with concurrent HTTPS lookups.

    Per-IP / per-row, per-week-chunk SQL semantics preserved exactly.
    The HTTPS pass batches via ThreadPoolExecutor — was the dominant cost
    of usage-metrics on backfilled months (~30ms per IP, sequential).
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

    cfg = db_config()
    hub_db    = cfg.get('hub_db', '')
    db_prefix = cfg.get('db_prefix', 'jos_')

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

            # Resolve all IPs for this chunk in one shot: bulk cache lookup
            # + concurrent HTTPS for cache misses + bulk cache INSERT.
            ips = [ip for ip, _hits in rows]
            geos = _ipgeo_lookup_batch(
                conn, hub_db, db_prefix, ips,
                url=url, hub_key=hub_key, timeout=timeout,
            )

            for ip in ips:
                geo = geos.get(ip)
                if geo is None:
                    continue
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
        # Tie-break by country code so the rank assignment is deterministic
        # when two countries share a count (mirrored in legacy gen_tool_tops.php).
        f"GROUP BY country ORDER BY cnt DESC, country ASC LIMIT 10",
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
        # Tie-break by domain so the rank assignment is deterministic
        # (mirrored in legacy gen_tool_tops.php).
        f"GROUP BY dom ORDER BY cnt DESC, dom ASC LIMIT 10",
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
        # Tie-break by orgtype so the rank assignment is deterministic
        # when (e.g.) "Unknown" and "industry" both have the same count
        # (mirrored in legacy gen_tool_tops.php).
        f"GROUP BY u.orgtype ORDER BY cnt DESC, u.orgtype ASC",
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


# Above this many event_ids in one session, route the UPDATE through a
# temporary table joined on web.id (PRIMARY KEY) instead of expanding
# into a huge `WHERE id IN (...)` literal.  The IN-list form can push
# the optimizer into a wide range scan whose row-lock footprint
# exceeds InnoDB's lock table on a small innodb_buffer_pool (128 MB →
# ~500 K-lock ceiling) and fails with OperationalError(1206) "The
# total number of locks exceeds the lock table size".  Observed on
# 2020-12 week 3: a single session with tens of thousands of web
# events overflowed.  The JOIN form drives from the small temp table
# using web.id PK lookups, so the lock count equals len(event_ids).
# Small sessions stay on the original one-shot IN path so the cheap
# case is unchanged (one round-trip instead of three).
_EMIT_UPDATE_TMPTABLE_THRESHOLD = 1000
_EMIT_UPDATE_INSERT_BATCH       = 5000


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
        if not event_ids:
            return

        if len(event_ids) <= _EMIT_UPDATE_TMPTABLE_THRESHOLD:
            placeholders = ",".join(["%s"] * len(event_ids))
            cur.execute(
                f"UPDATE web SET sessionid = %s WHERE id IN ({placeholders})",
                [s_id, *event_ids],
            )
            return

        # Big-session path — see comment above.  CREATE IF NOT EXISTS
        # + DELETE makes the temp table reusable across calls on the
        # same connection (TEMPORARY TABLEs are per-connection); the
        # CREATE is a no-op after the first time.
        cur.execute(
            "CREATE TEMPORARY TABLE IF NOT EXISTS _logfix_emit_eids "
            "(id BIGINT UNSIGNED NOT NULL PRIMARY KEY) ENGINE=Memory"
        )
        cur.execute("DELETE FROM _logfix_emit_eids")
        for i in range(0, len(event_ids), _EMIT_UPDATE_INSERT_BATCH):
            chunk = event_ids[i:i + _EMIT_UPDATE_INSERT_BATCH]
            values = ",".join(["(%s)"] * len(chunk))
            cur.execute(
                f"INSERT INTO _logfix_emit_eids (id) VALUES {values}",
                chunk,
            )
        cur.execute(
            "UPDATE web w JOIN _logfix_emit_eids e ON w.id = e.id "
            "SET w.sessionid = %s",
            (s_id,),
        )


def do_logfix_session(month=None, *, dry_run=False):
    """Direct port of logfix_session.pl.

    Walks the 4 fixed week ranges (day 1-8, 9-16, 17-24, 25-1 of next month);
    in each, streams web rows ordered by (ip, host, datetime) and emits a
    websessions row whenever IP/host changes or there is a >1800s gap.

    Bug-for-bug quirks preserved for A/B parity — do NOT "fix" without
    coordinating with the legacy reference under tests/legacy/logfix_session.pl:

      * 'video' tracking is dead in the Perl — variables exist but never
        update, so the second timeout clause always fires when the gap
        condition does.  (Perl had two near-identical session-cut conditions
        guarded on `video`; with `video` permanently 0 the second one is
        equivalent to the first.)

      * s_webevents is reset to 0 on session start but never incremented;
        always 0 in INSERTs.  Looks like an event-counter that was abandoned
        mid-implementation in the original Perl; the column stays 0 in
        production data for the same reason.

      * The last in-flight session of week 3 is never flushed.  Each week is
        a separate Perl `while` over the week's rows, and Perl scopes the
        session-state vars at the loop body — so when week 3's loop ends
        with a session still open (e.g. a long-running session straddling
        the month/week boundary at day 25), that session's final segment is
        dropped instead of being emitted.  The new port mirrors this rather
        than emitting a partial trailing row, because doing so would diverge
        from legacy websessions counts and fail the A/B test.

      * INSERT IGNORE on websessions, so duplicate ids are silently dropped
        (legacy id collisions across re-runs of the same month).
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

    # Batch session emits inside explicit transactions so each set of
    # writes commits with one redo-log fsync rather than one per
    # statement.  Profile on a fresh 11.7 M-row month (2026-02) showed
    # logfix-session at 7.6 h with autocommit-per-emit; the cost was
    # dominated by ~20 M individual fsyncs, not by the work itself.
    #
    # Idempotency / safety: a session is only emitted AFTER its end is
    # detected, so the in-flight session state lives in Python only —
    # nothing partial ever reaches the DB.  The transaction boundary
    # therefore falls cleanly between fully-emitted sessions:
    #   - mid-batch crash: InnoDB rolls back the uncommitted transaction;
    #     those sessions never existed; on re-run the SELECT filter
    #     `(sessionid = '0' OR sessionid IS NULL)` re-processes their
    #     web rows from scratch.
    #   - INSERT IGNORE on websessions is belt-and-suspenders against
    #     any s_id collision across re-runs (s_id starts from current
    #     MAX(id) at each run, so committed sessions always sit below
    #     re-run s_ids — collisions only happen if a re-run starts
    #     while a crashed transaction's uncommitted rows haven't yet
    #     been visible-rolled-back, which InnoDB precludes).
    #   - month_has_orphaned_stamps() (already used by catchup) still
    #     catches drift if any future code path violates this invariant.
    _LOGFIX_TXN_BATCH = 500
    conn_write.autocommit(False)

    total_sessions = 0
    total_events   = 0
    total_jobs     = 0
    sessions_in_txn = 0

    try:
        with conn_write.cursor() as cur:
            cur.execute("SELECT MAX(id) FROM websessions")
            row = cur.fetchone()
            s_id = int(row[0]) if row and row[0] else 0
        # The MAX(id) probe above opened an implicit transaction;
        # commit it so we start the batch loop on a clean transaction
        # boundary (avoids a stale read snapshot if anything else
        # touched websessions between probe and emit).
        conn_write.commit()

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
                        # Commit at the session boundary, never mid-session,
                        # so an interrupted re-run never sees a half-stamped
                        # session.  See the batch-safety comment above.
                        sessions_in_txn += 1
                        if sessions_in_txn >= _LOGFIX_TXN_BATCH:
                            conn_write.commit()
                            sessions_in_txn = 0

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

        # Commit any tail batch that didn't reach the batch threshold.
        if sessions_in_txn > 0:
            conn_write.commit()
            sessions_in_txn = 0

        log.info(f"[logfix-session] total: {total_sessions} session(s), "
            f"{total_events} web event(s), {total_jobs} toolstart job(s) linked")
        return 0
    except BaseException:
        # On any exception (including KeyboardInterrupt / SystemExit),
        # explicitly rollback so the partial in-flight batch is dropped
        # cleanly — re-run starts from the last committed batch.
        try:
            conn_write.rollback()
        except Exception:
            pass
        raise
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

# ---------------------------------------------------------------------------
# cmd_run: three-mode orchestrator (normal | catchup | rebuild)
#
# State, transitions, and per-month routing
# -----------------------------------------
# Each tick reads `pipeline_state` to get the current mode and dispatches
# to the matching handler.  Mode transitions are computed at the start of
# every tick from filesystem + DB state, not stored across ticks — so
# the orchestrator self-corrects after manual intervention or external
# changes (e.g. someone drops a new log into daily/2027/ mid-rebuild).
#
#   normal:  default.  Today's pending logs get imported, prev month gets
#            summarized when its last day arrives.
#            Transition → catchup when any month strictly before today_str
#            has either a pending source log or DB rows + incomplete summary.
#
#   catchup: process one backlog month per tick.  Applies the decision
#            matrix from Phase C (month_has_source / month_has_data /
#            is_month_fully_summarized) to pick import / wipe+reimport /
#            resummarize-only / skip.  Summarize uses periods=(1,) so the
#            expensive long-window (0/3/12/13/14) work is deferred to
#            rebuild.  Records earliest backfilled month in
#            state["catchup_started"].
#            Transition → rebuild when no more backlog months need touching.
#
#   rebuild: walk forward from state["rebuild_cursor"] (initially set to
#            state["catchup_started"]) through prev_month(today_str),
#            re-summarizing one month per tick with all six periods.  This
#            corrects the period 12/13/14 cells in every month at-or-after
#            the earliest backfill — those cells were computed when 2022 /
#            2023 weren't yet in `web`, so their windows are now stale.
#            Transition → normal when cursor passes prev_month.
# ---------------------------------------------------------------------------

_CATCHUP_PERIODS: tuple = (1,)  # period=1 only: this-month cells, self-contained


def _import_month(month_str: str, dry_run: bool) -> int:
    """Import every pending day in `month_str` via do_import_day.  Days
    that aren't pending (because they're already in imported/ or simply
    don't exist) are silently skipped — do_import_day itself is a no-op
    when its source files aren't found.

    Returns total base-table rows inserted across the whole month.
    Crash-recovery re-ticks (where every source file is already
    committed in `imported_sources`) return 0 — the caller uses that
    signal to skip lowering `rebuild_cascade_from` and the cascade
    walk that follows."""
    days = pending_days_for_month(month_str)
    log.info(f"[import] {month_str}: {len(days)} day(s) pending")
    inserted = 0
    for date_str in days:
        log.info(f"--- {date_str} ---")
        inserted += do_import_day(date_str, dry_run)
    return inserted


def _do_normal_tick(today_str: str, prev: str, today_date: str,
                    state: dict, dry_run: bool) -> None:
    """Steady-state behaviour: import any pending logs for today's month,
    analyze the current month once per day, summarize the previous month
    when its last day has arrived (or when we're >5 days into the new
    month and the last day still hasn't shown up — a tolerant fallback
    that catches logrotate drops)."""
    current_pending = pending_days_for_month(today_str)
    if current_pending:
        log.info(f"[normal] importing {len(current_pending)} pending day(s) for {today_str}")
        for date_str in current_pending:
            log.info(f"--- {date_str} ---")
            do_import_day(date_str, dry_run)

    analyzed_today = state.get("analyzed") == today_date
    if not analyzed_today:
        log.info(f"[normal] analyzing current month {today_str} (sessions deferred to month-close)")
        # sessions=False: skip logfix-session + websessions-bound steps
        # for the in-progress current month.  Daily logfix-session would
        # stamp sessionid on rows imported so far; the next tick's
        # logfix-session would then start fresh on the new rows, slicing
        # any session that genuinely spanned the tick boundary.  The
        # month-close branch below runs do_analyze (sessions=True) on
        # prev once the month is fully imported, which is the only run
        # of logfix-session that month sees.
        do_analyze(today_str, dry_run, sessions=False)
        if not dry_run:
            update_state(analyzed=today_date)

    if not is_month_summarized(prev):
        if is_month_complete(prev):
            log.info(f"[normal] {prev} complete — analyzing and summarizing")
            do_analyze(prev, dry_run)
            do_summarize(prev, dry_run)
        else:
            log.info(f"[normal] {prev} not yet complete (no next-month data, "
                     f"last-day file not in imported/) — deferring")


def _backlog_months(today_str: str) -> list[str]:
    """Months strictly before today_str that need orchestrator attention:
    either still have pending source logs, OR have base-table data but
    aren't fully summarized, OR are marked dirty by the operator, OR
    have orphaned sessionid stamps pointing at deleted websessions
    rows.  Sorted oldest-first.

    The dirty + orphaned-stamps checks let the orchestrator self-heal
    after bulk web mutations (cleanup scripts, manual DELETEs) that
    invalidate derived tables — the operator marks the affected
    months with `mark-dirty` (or the consistency check finds the
    drift on its own) and the next ticks redrive them."""
    months = set()
    months_with_source: set[str] = set()  # pending source files override the fully-summ filter
    # Auth-only months count too — see _enumerate_all_pending_days.
    for d in _enumerate_all_pending_days():
        m = f"{d[:4]}-{d[4:6]}"
        if m < today_str:
            months.add(m)
            months_with_source.add(m)
    # Also include months whose data is in the DB but summary is incomplete —
    # the 2024 access months + 2025-07 fit this shape.
    _, _, _, metrics_db = db_credentials()
    rows = mysql_query(
        f"SELECT DISTINCT DATE_FORMAT(datetime, '%%Y-%%m') AS ym "
        f"FROM {metrics_db}.web WHERE datetime < %s "
        f"  AND DATE_FORMAT(datetime, '%%Y-%%m') NOT IN "
        f"      (SELECT DATE_FORMAT(datetime, '%%Y-%%m') FROM {metrics_db}.summary_user_vals "
        f"       WHERE period = 1)",
        (today_str + "-01",),
    )
    for (ym,) in rows:
        if ym and ym < today_str:
            months.add(ym)
    # Operator-flagged dirty months get re-considered even if they
    # were already fully summarized — the operator set the flag
    # specifically to override that signal.
    dirty = get_dirty_months()
    months.update(m for m in dirty if m < today_str)
    # Months with pending source files always need attention (even if
    # they look fully summarized — the new files may contain rows the
    # summary doesn't reflect, as happens in the apache-log gzip-bug
    # recovery flow).  Dirty + orphaned-stamps also override fully-summ.
    return sorted(m for m in months
                  if m in months_with_source
                  or m in dirty
                  or month_has_orphaned_stamps(m)
                  or not is_month_fully_summarized(m))


def _record_rebuild_cascade_from(month: str, state: dict, dry_run: bool) -> None:
    """Lower `rebuild_cascade_from` to `month` if it's earlier than the
    stored value (or unset).

    `rebuild_cascade_from` is the oldest month catchup IMPORTED new base
    data into during the current catchup cycle.  Only the import branches
    call this — resummarize-only months change no base rows and so
    invalidate no downstream long-window cells.  At the catchup→rebuild
    handoff this becomes the rebuild walk's starting cursor: the walk must
    cover [cascade_from, prev] because new data at `cascade_from` stales
    the all-time (period-14) and rolling (period-12/13) windows of every
    later month.  If catchup imports nothing, the key stays unset and the
    walk is skipped entirely — the completeness sweep alone fixes any
    months catchup only resummarized."""
    prior = state.get("rebuild_cascade_from")
    if prior and prior <= month:
        return
    state["rebuild_cascade_from"] = month  # local reflect for this tick
    if not dry_run:
        update_state(rebuild_cascade_from=month)


def _do_catchup_tick(today_str: str, state: dict, dry_run: bool) -> bool:
    """Process one backlog month per tick, applying the Phase C decision
    matrix.  Returns True if we transitioned out of catchup (caller should
    update state["mode"]).

    Decision matrix (source / data / summary state → action):
      ✓ ✗ –            : import + analyze + summarize-period-1
      ✓ ✓ none/partial : import (additive) + reset-derived + analyze + summarize-period-1
      ✗ ✓ none/partial : (re)summarize-period-1 only — data is in DB,
                          source is gone (2024 access months / 2025-07)
      ✗ ✗ –            : skip (true gap)
      any ✓ full       : skip (already done)

    Provenance principle: base tables (web/userlogin/webhits) are never
    deleted by datetime — only via `forget-import` against an explicit
    `imported_sources` PK range.  A filename is a sortable identifier,
    not a data dictionary; the `datetime` of rows it imported is its own
    business.  The `source ✓ data ✓` branch therefore trusts existing
    rows (their provenance is either recorded in `imported_sources` or
    is pre-migration-#44 and thus unrecoverable — either way, deleting
    them by inferred date range is wrong) and just adds the pending
    files on top.  `_reset_month_for_resummarize` clears all derived
    state — `web.sessionid`, `websessions`, the month's `summary_*_vals`
    cells, AND the month's `webhits` rows — so `logfix-session`
    recomputes sessions from the combined (existing + newly imported)
    base rows, and `do_rebuild_webhits` (called at the end of
    `do_analyze`) regenerates `webhits` from current `web`.
    """
    backlog = _backlog_months(today_str)
    if not backlog:
        log.info(f"[catchup] no backlog months remaining — transition to rebuild")
        return True

    target = backlog[0]
    remaining = len(backlog)
    log.info(f"[catchup] {remaining} backlog month(s) — processing {target}")

    # Record the earliest backfill date so rebuild knows where to start.
    # rebuild's forward walk begins here, so catchup_started must track
    # the OLDEST month catchup has touched: if a later catchup run picks
    # up source files for a month earlier than the stored value (e.g. a
    # pre-2022 auth backlog that only became visible after the discovery
    # layer learned to enumerate auth logs), lower it so the rebuild
    # cascade reaches back far enough to refresh every later month whose
    # long-window cells that older data invalidates.
    prior = state.get("catchup_started")
    if prior is None or target < prior:
        if not dry_run:
            update_state(catchup_started=target)
        state["catchup_started"] = target  # local reflect for this tick

    has_source  = month_has_source(target)
    has_data    = month_has_data(target)
    fully_summ  = is_month_fully_summarized(target) if (has_source or has_data) else False
    is_dirty    = target in get_dirty_months()
    orphans     = month_has_orphaned_stamps(target) if has_data else False
    needs_reset = is_dirty or orphans  # post-bulk-edit invalidation

    if fully_summ and not needs_reset and not has_source:
        log.info(f"[catchup] {target} already fully summarized — skipping")
        return False  # let next tick advance past it

    if has_source and has_data:
        log.info(f"[catchup] {target}: source ✓ data ✓ — "
                 f"importing pending files + resetting derived state")
        inserted = _import_month(target, dry_run)
        # Reset derived state AFTER import so logfix-session in
        # do_analyze recomputes websessions over the combined (existing
        # + newly imported) base rows.  Base tables are untouched.
        _reset_month_for_resummarize(target, dry_run=dry_run)
        do_analyze(target, dry_run)
        do_summarize(target, dry_run, periods=_CATCHUP_PERIODS)
        # Only lower the cascade marker if NEW base data landed.  A
        # crash-recovery re-tick where every file in `imported_sources`
        # already committed inserts 0 — re-summarizing without new
        # base rows invalidates nothing downstream.
        if inserted > 0:
            _record_rebuild_cascade_from(target, state, dry_run)
    elif has_source:
        log.info(f"[catchup] {target}: source ✓ data ✗ — fresh import")
        inserted = _import_month(target, dry_run)
        do_analyze(target, dry_run)
        do_summarize(target, dry_run, periods=_CATCHUP_PERIODS)
        if inserted > 0:
            _record_rebuild_cascade_from(target, state, dry_run)
    elif has_data:
        if needs_reset:
            why = []
            if is_dirty:  why.append("dirty marker")
            if orphans:   why.append("orphaned sessionid stamps")
            log.info(f"[catchup] {target}: source ✗ data ✓ — derived state stale "
                     f"({', '.join(why)}); resetting + resummarize")
            _reset_month_for_resummarize(target, dry_run=dry_run)
        else:
            log.info(f"[catchup] {target}: source ✗ data ✓ — DB-only, resummarize")
        do_analyze(target, dry_run)
        do_summarize(target, dry_run, periods=_CATCHUP_PERIODS)
    else:
        log.warning(f"[catchup] {target}: source ✗ data ✗ — true gap, skipping")
        # Skip — but the backlog probe will keep returning this month
        # if it's a placeholder.  Currently the probe excludes such
        # months (no source / no data → not detected), so we won't loop.

    # Clear the dirty marker once we've handled the month.  The
    # orphan check is self-clearing — once logfix-session rebuilds
    # websessions, the stamps line up again on the next pass.
    if is_dirty and not dry_run:
        clear_dirty_month(target)

    return False  # still in catchup after this tick


def _do_rebuild_tick(today_str: str, prev: str, state: dict, dry_run: bool) -> bool:
    """Advance the rebuild phase by one unit of work per tick.  Two phases,
    in order:

      Phase 1 — forward cursor walk.  Re-summarize one month per tick from
      rebuild_cursor through prev_month with all six periods.  This is the
      invalidation cascade: when catchup backfilled an older month, every
      later month's long-window (12 / 13 / 14) cells were computed off a
      window that didn't yet include that data, so they're stale and must
      be recomputed.  The walk runs forward from the earliest backfilled
      month (catchup_started), never re-touching months before it.

      Phase 2 — completeness sweep.  Once the walk is done (cursor past
      prev_month), resummarize any month that has summary rows but is
      missing period codes — months the walk never reached because they
      sit below the cursor (e.g. a pre-catchup_started auth backlog that
      a catchup pass only ever period-1-summarized).  One month per tick,
      oldest first.

    Note: the walk currently resummarizes every month in [cursor, prev]
    unconditionally — it does NOT skip months that are already complete
    and drift-free.  Within the cascade that's correct (a backfill staled
    them), but it is broader than strictly necessary when catchup only
    touched a few historically-incomplete months and added no new data.
    A future optimisation could fast-forward the cursor over intact
    months; that is not implemented here.

    Returns True only when the walk is done AND no incomplete months
    remain (caller transitions back to normal)."""
    cursor = state.get("rebuild_cursor") or state.get("catchup_started")

    # --- Phase 1: forward cursor walk -------------------------------------
    # Re-summarize one month per tick from the cursor through prev_month.
    # This is the invalidation cascade: when catchup backfilled an older
    # month, every later month's long-window (12 / 13 / 14) cells were
    # computed off a window that didn't yet include that data, so they're
    # stale and must be recomputed.  The walk runs forward from the
    # earliest backfilled month (catchup_started), never re-touching
    # months before it.
    if cursor and cursor <= prev:
        log.info(f"[rebuild] resummarizing {cursor} (all 6 periods)")
        do_analyze(cursor, dry_run)
        do_summarize(cursor, dry_run)  # default = all periods
        next_cursor = next_month(cursor)
        if not dry_run:
            update_state(rebuild_cursor=next_cursor)
        log.info(f"[rebuild] cursor advanced: {cursor} → {next_cursor}")
        return False

    # --- Phase 2: completeness sweep --------------------------------------
    # The forward walk is done (cursor past prev_month, or absent).  Before
    # declaring rebuild complete, look for any month that has summary rows
    # but is missing periods — months the walk never reached because they
    # sit below the cursor.  The canonical case: a catchup pass imported +
    # period-1-summarized months older than catchup_started (the pre-2022
    # cmsauth backlog), so they have period=1 but none of the long-window
    # periods.  Rebuilding such a month fills its own missing periods and
    # has NO downstream effect (it doesn't change any other month's
    # windows), so this is targeted, minimal work — and because rebuild
    # writes all periods, each swept month becomes complete and is never
    # surfaced again (no re-process loop).
    incomplete = period_incomplete_months(today_str)
    if incomplete:
        target = incomplete[0]
        log.info(f"[rebuild] completeness sweep — {len(incomplete)} incomplete "
                 f"month(s); resummarizing {target} (all 6 periods)")
        do_analyze(target, dry_run)
        do_summarize(target, dry_run)
        return False  # stay in rebuild; re-check on the next tick

    log.info(f"[rebuild] cursor past prev_month and no incomplete months — done")
    return True


def cmd_run(args):
    dry_run = args.dry_run

    # Self-repair before lock acquire (the lock file lives under
    # HZMETRICS_HOME/state which bootstrap is responsible for creating).
    # Idempotent + apache-gated; see _self_bootstrap.  Skipped when
    # invoked from cmd_tick (already ran there) and during dry-run.
    if not dry_run:
        _self_bootstrap()
        if not acquire_lock():
            log.info(f"[run] still running — skipping.")
            return

    try:
        today_str  = date.today().strftime("%Y-%m")
        today_date = date.today().isoformat()
        prev       = previous_month(today_str)

        state = read_state()
        mode  = state.get("mode", "normal")

        if not dry_run:
            log.debug(f"=== hzmetrics.py run @ {datetime.now()} mode={mode} ===")

        if mode == "normal":
            # Enter catchup if any month before today needs work.
            if _backlog_months(today_str):
                log.info(f"[run] backlog detected — switching mode normal → catchup")
                if not dry_run:
                    update_state(mode="catchup")
                state["mode"] = "catchup"
                mode = "catchup"

        with _timed_stage(f"tick mode={mode}"):
            if mode == "catchup":
                done = _do_catchup_tick(today_str, state, dry_run)
                if done:
                    # The rebuild walk starts at the oldest month catchup
                    # actually IMPORTED new base data into — only that
                    # staled later months' long-window cells.  If catchup
                    # imported nothing (it only resummarized incomplete
                    # months), there is no invalidation cascade: set the
                    # cursor past prev_month so the walk is skipped and the
                    # completeness sweep alone runs.
                    cascade_from = state.get("rebuild_cascade_from")
                    cursor = cascade_from if cascade_from else next_month(today_str)
                    if cascade_from:
                        log.info(f"[run] catchup complete — rebuild cascade from "
                                 f"{cursor} (oldest month with new imported data)")
                    else:
                        log.info(f"[run] catchup complete — no new data imported; "
                                 f"rebuild walk skipped, completeness sweep only")
                    if not dry_run:
                        update_state(mode="rebuild", rebuild_cursor=cursor)
                    # Don't run rebuild this tick — give the next tick a fresh start.
            elif mode == "rebuild":
                done = _do_rebuild_tick(today_str, prev, state, dry_run)
                if done:
                    log.info(f"[run] rebuild complete — switching to normal")
                    if not dry_run:
                        # Clear the cascade marker so the next catchup cycle
                        # starts fresh (empty = "no import yet this cycle").
                        update_state(mode="normal", rebuild_cascade_from="")
                    # Also run a normal tick this iteration since rebuild is cheap once done.
                    _do_normal_tick(today_str, prev, today_date, state, dry_run)
            else:
                _do_normal_tick(today_str, prev, today_date, state, dry_run)

        log.info(">>> done")

    finally:
        if not dry_run:
            release_lock()


_YYYY_MM_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')

def cmd_mark_dirty(args):
    """Flag one or more YYYY-MM months as needing rework.  Use after a
    bulk DELETE/UPDATE on `web` (cleanup script, manual surgery) so the
    next catchup tick redrives those months end-to-end: reset
    web.sessionid, wipe websessions + summary, re-run logfix-session +
    summarize.  Months are auto-cleared after the orchestrator processes
    them."""
    bad = [m for m in args.months if not _YYYY_MM_RE.match(m)]
    if bad:
        log.info(f"[mark-dirty] not in YYYY-MM form: {' '.join(bad)}")
        return 2
    add_dirty_months(args.months)
    log.info(f"[mark-dirty] flagged: {' '.join(sorted(args.months))}")
    log.info(f"[mark-dirty] current dirty set: "
             f"{', '.join(sorted(get_dirty_months())) or '(empty)'}")
    return 0


def cmd_clear_dirty(args):
    """Remove months from the dirty set without processing them.  Mostly
    a backstop for the case where mark-dirty was used by mistake — the
    catchup state machine clears the marker automatically once a month
    finishes a reset+resummarize pass."""
    if args.all:
        update_state(dirty_months="")
        log.info("[clear-dirty] cleared all dirty months")
        return 0
    for m in args.months:
        clear_dirty_month(m)
    log.info(f"[clear-dirty] remaining dirty set: "
             f"{', '.join(sorted(get_dirty_months())) or '(empty)'}")
    return 0


def cmd_rebuild_from(args):
    """Re-enter rebuild mode starting at the given YYYY-MM.  Use after a
    code change or data fix (e.g., dnload backfill, a new summary
    formula) that requires every month at or after a cutoff to be
    resummarized end-to-end.

    Atomically sets `pipeline_state.mode = 'rebuild'` and
    `pipeline_state.rebuild_cursor = <month>` in one UPDATE.  The next
    tick will see mode=rebuild and call `_do_rebuild_tick`, which walks
    the cursor forward through prev_month with all 6 periods per month.

    Operationally equivalent to manually editing pipeline_state, but
    validates the argument, refuses to point the cursor past today,
    and logs the before→after transition for audit."""
    month = args.month
    if not _YYYY_MM_RE.match(month):
        log.info(f"[rebuild-from] not in YYYY-MM form: {month!r}")
        return 2
    today_ym = date.today().strftime("%Y-%m")
    if month > today_ym:
        log.info(f"[rebuild-from] refusing to rebuild from future month "
                 f"{month} (today is {today_ym})")
        return 2
    state = read_state()
    prev_mode   = state.get("mode", "normal")
    prev_cursor = state.get("rebuild_cursor", "(unset)")
    catchup_started = state.get("catchup_started")
    if catchup_started and month < catchup_started:
        # Not strictly wrong — the operator may want to rebuild further
        # back than catchup_started — but warn since the long-window
        # periods (12, 14) at month < catchup_started were built off
        # the full historical web data and don't need a refresh in the
        # usual workflow.
        log.warning(f"[rebuild-from] cursor {month} predates "
                    f"catchup_started={catchup_started} — proceeding, but "
                    f"this is unusual; verify intent")
    update_state(mode="rebuild", rebuild_cursor=month)
    log.info(f"[rebuild-from] mode {prev_mode} → rebuild, "
             f"rebuild_cursor {prev_cursor} → {month}")
    return 0


def cmd_forget_import(args):
    """Remove an imported_sources record AND the data rows it tracked.
    After this, re-feeding the file (move from imported/ back to
    daily/) triggers a fresh import on the next tick.

    Operator use case: a previously imported file's data turned out to
    be wrong (corrupt log, schema mismatch, bad day) and the operator
    wants to redo from scratch.  Without forget-import, the
    imported_sources UNIQUE constraint would skip the re-INSERT.

    For 'web' / 'userlogin' targets, pk_start/pk_end identify the
    inserted rows exactly — indexed PK-range DELETE.  For 'webhits'
    (no AUTO_INCREMENT PK), data rows aren't auto-removed; this
    command logs a warning and leaves them in place — operator
    handles them manually if needed.
    """
    _, _, _, metrics_db = db_credentials()
    rows = mysql_query(
        f"SELECT id, target_table, pk_start, pk_end, row_count "
        f"FROM {metrics_db}.imported_sources WHERE filename = %s "
        f"ORDER BY target_table",
        (args.filename,))
    if not rows:
        log.info(f"[forget-import] no record found for {args.filename}")
        return 1
    if args.table and args.table != "all":
        rows = [r for r in rows if r[1] == args.table]
        if not rows:
            log.info(f"[forget-import] no '{args.table}' record for "
                     f"{args.filename}")
            return 1
    for src_id, table, pk_start, pk_end, row_count in rows:
        if pk_start is not None and pk_end is not None:
            if args.dry_run:
                log.info(f"[forget-import] [dry-run] would DELETE FROM "
                         f"{table} id IN [{pk_start}, {pk_end}] "
                         f"(~{row_count} row(s))")
            else:
                mysql_exec(
                    f"DELETE FROM {metrics_db}.{table} "
                    f"WHERE id BETWEEN %s AND %s",
                    (pk_start, pk_end))
                log.info(f"[forget-import] removed {row_count} row(s) from "
                         f"{table} (id {pk_start}..{pk_end})")
        else:
            log.warning(f"[forget-import] {table} record for {args.filename} "
                        f"has no PK range (table likely has no AUTO_INCREMENT "
                        f"— webhits).  Data rows NOT auto-removed; operator "
                        f"must clean {table} manually if desired.")
        if not args.dry_run:
            mysql_exec(
                f"DELETE FROM {metrics_db}.imported_sources WHERE id = %s",
                (src_id,))
    return 0


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

    p_md = sub.add_parser("mark-dirty",
        help="Flag YYYY-MM months as needing rework after a bulk web mutation")
    p_md.set_defaults(func=cmd_mark_dirty)
    p_md.add_argument("months", nargs="+", metavar="YYYY-MM",
        help="One or more months to flag")

    p_cd = sub.add_parser("clear-dirty",
        help="Remove months from the dirty set without processing them")
    p_cd.set_defaults(func=cmd_clear_dirty)
    p_cd.add_argument("months", nargs="*", metavar="YYYY-MM",
        help="Months to remove (omit with --all)")
    p_cd.add_argument("--all", action="store_true",
        help="Clear every dirty marker")

    p_rf = sub.add_parser("rebuild-from",
        help="Re-enter rebuild mode starting at the given YYYY-MM")
    p_rf.set_defaults(func=cmd_rebuild_from)
    p_rf.add_argument("month", metavar="YYYY-MM",
        help="Cursor to start rebuilding from (validated; refuses future months)")

    p_fi = sub.add_parser("forget-import",
        help="Remove imported_sources record + data rows for a file (enables re-import)")
    p_fi.set_defaults(func=cmd_forget_import)
    p_fi.add_argument("filename", metavar="FILENAME",
        help="Source filename as recorded in imported_sources")
    p_fi.add_argument("--table", choices=["web", "userlogin", "webhits", "all"],
        default="all",
        help="Target table to forget (default: all targets for this filename)")
    p_fi.add_argument("--dry-run", action="store_true",
        help="Show what would be deleted without doing it")

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

    p_rebuild = sub.add_parser("rebuild-summaries",
        help="Resummarize a range of months (manual override; doesn't touch orchestrator mode)")
    p_rebuild.set_defaults(func=cmd_rebuild_summaries)
    p_rebuild.add_argument("--since",   metavar="YYYY-MM", type=_arg_yyyymm, required=True,
        help="Earliest month to resummarize (inclusive)")
    p_rebuild.add_argument("--through", metavar="YYYY-MM", type=_arg_yyyymm,
        help="Latest month (inclusive); defaults to previous calendar month")
    p_rebuild.add_argument("--periods", metavar="CSV",
        help="Comma-separated period codes (subset of 0,1,3,12,13,14); default: all")
    p_rebuild.add_argument("--dry-run", action="store_true",
        help="Show what would be done without doing it")

    p_setup = sub.add_parser("setup-db", help="Create metrics database and all tables (idempotent)")
    p_setup.set_defaults(func=cmd_setup_db)
    p_setup.add_argument("--dry-run", action="store_true", help="Show statements without executing")

    p_migrate = sub.add_parser("migrate", help="Show or apply schema migrations")
    p_migrate.set_defaults(func=cmd_migrate)
    p_migrate.add_argument("--apply", action="store_true", help="Apply pending migrations")
    p_migrate.add_argument("--required-only", action="store_true",
                           help="With --apply: skip optional performance migrations")

    p_init = sub.add_parser(
        "init",
        help="One-shot install bootstrap: create expected dirs, "
             "create the metrics database, run setup-db DDL, apply "
             "migrations.  Idempotent — safe to re-run.")
    p_init.set_defaults(func=cmd_init)

    p_doctor = sub.add_parser(
        "doctor",
        help="Diagnose install health.  Reports every dir / DB / "
             "migration issue first; --fix attempts to repair what's "
             "safely repairable.")
    p_doctor.set_defaults(func=cmd_doctor)
    p_doctor.add_argument(
        "--fix", action="store_true",
        help="Attempt to repair (run the same dir + DB + migrate "
             "bootstrap _self_bootstrap uses on cron startup)")

    p_dnload = sub.add_parser("backfill-dnload", help="Populate web.dnload flag for historical rows")
    p_dnload.set_defaults(func=cmd_backfill_dnload)
    p_dnload.add_argument("--start", metavar="YYYY-MM", type=_arg_yyyymm,
        help="Only process months >= this (default: all)")
    p_dnload.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")

    p_rwh = sub.add_parser("rebuild-webhits",
        help="Regenerate the webhits derived table from web (disaster recovery / "
             "retroactive filter rule application)")
    p_rwh.set_defaults(func=cmd_rebuild_webhits)
    grp_rwh = p_rwh.add_mutually_exclusive_group()
    grp_rwh.add_argument("--month", metavar="YYYY-MM", type=_arg_yyyymm,
        help="Rebuild a specific month")
    grp_rwh.add_argument("--all", action="store_true",
        help="Rebuild every month present in web")
    p_rwh.add_argument("--dry-run", action="store_true",
        help="Show what would be deleted/inserted without doing it")

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
    p_fd.add_argument("table", choices=list(FILL_DOMAIN_TABLES),
        help="Target table (web | websessions | toolstart | sessionlog_metrics)")
    p_fd.add_argument("date_spec", nargs="?", default=None, metavar="DATE_OR_RANGE",
        help="YYYY | YYYY-MM | YYYY-MM-DD or '<start>..<end>' (default: current month)")
    p_fd.add_argument("--all", action="store_true",
        help="No date filter (use with care on large tables)")
    p_fd.add_argument("--dry-run", action="store_true",
        help="Show derivations; don't UPDATE")

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
    p_ui.add_argument("table", choices=list(FILL_USER_INFO_TABLES),
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
    p_dns.add_argument("table", type=_arg_sql_identifier,
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
    # Propagate the handler's return code as the process exit status so
    # cron / CI see a real failure (do_* helpers return 1 on mysql_exec
    # failure, 2 on config errors, etc.).  None or 0 means success.
    rc = args.func(args)
    if rc:
        raise SystemExit(rc)

if __name__ == "__main__":
    main()
