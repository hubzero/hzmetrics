#!/bin/bash
# Capture a real production-data slice into tests/ab/port_realdata/snapshot/.
# Output files are NOT committed (snapshot/ is gitignored — contains real
# usernames and email addresses).  Re-run this on any host with read access
# to the prod+ foo_metrics databases.
#
# Defaults to March 2025 (87k web rows; ~290 meaningful userlogin rows).
# Override the window with MONTH_START / MONTH_END env vars.

set -euo pipefail
SCRIPTPATH=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT="$SCRIPTPATH/snapshot"
mkdir -p "$OUT"

WIN_START="${MONTH_START:-2025-03-01}"
WIN_END="${MONTH_END:-2025-04-01}"
HUB_DB=
METRICS_DB=foo_metrics

echo "Capturing slice $WIN_START .. $WIN_END from $HUB_DB / $METRICS_DB"

dump() {
    local db="$1" table="$2" where="$3" out="$4"
    sudo mysqldump --no-create-info --no-tablespaces --where="$where" \
        "$db" "$table" 2>/dev/null | gzip > "$OUT/$out"
    echo "  $(printf '%9d' "$(wc -c < "$OUT/$out")")  $out"
}

dump_full() {
    local db="$1" table="$2"
    sudo mysqldump --no-create-info --no-tablespaces \
        "$db" "$table" 2>/dev/null | gzip > "$OUT/${table}.sql.gz"
    echo "  $(printf '%9d' "$(wc -c < "$OUT/${table}.sql.gz")")  ${table}.sql.gz"
}

# ── Metrics-side: month-bounded slices ─────────────────────────────
dump "$METRICS_DB" web         "datetime BETWEEN '$WIN_START' AND '$WIN_END'" web.sql.gz
dump "$METRICS_DB" websessions "datetime BETWEEN '$WIN_START' AND '$WIN_END'" websessions.sql.gz
dump "$METRICS_DB" toolstart   "datetime BETWEEN '$WIN_START' AND '$WIN_END'" toolstart.sql.gz
dump "$METRICS_DB" webhits     "datetime BETWEEN '$WIN_START' AND '$WIN_END'" webhits.sql.gz

# userlogin: keep all meaningful action rows + a sample of bot-probe noise
dump "$METRICS_DB" userlogin \
    "datetime BETWEEN '$WIN_START' AND '$WIN_END' AND action IN ('login','simulation','logout','invalid')" \
    userlogin_meaningful.sql.gz
sudo mysql -BN -e "
    SELECT CONCAT('INSERT INTO userlogin (datetime,uidNumber,user,ip,action) VALUES (',
                  QUOTE(datetime),',',uidNumber,',',QUOTE(user),',',QUOTE(ip),',',QUOTE(action),');')
    FROM $METRICS_DB.userlogin
    WHERE datetime BETWEEN '$WIN_START' AND '$WIN_END' AND action = 'detect'
    LIMIT 1000;
" 2>/dev/null | gzip > "$OUT/userlogin_detect_sample.sql.gz"
echo "  $(printf '%9d' "$(wc -c < "$OUT/userlogin_detect_sample.sql.gz")")  userlogin_detect_sample.sql.gz"

# ── Hub-side: time-bounded slices ──────────────────────────────────
dump "$HUB_DB" sessionlog "start BETWEEN '$WIN_START' AND '$WIN_END'" sessionlog.sql.gz
sudo mysql -BN -e "
    SELECT CONCAT('INSERT INTO joblog VALUES (',
                  j.sessnum,',',j.job,',',j.superjob,',',QUOTE(j.event),',',
                  QUOTE(j.start),',',j.walltime,',',j.cputime,',',j.ncpus,',',
                  j.status,',',QUOTE(j.venue),',',j.zone_id,',',QUOTE(j.end),');')
    FROM $HUB_DB.joblog j
    JOIN $HUB_DB.sessionlog s ON s.sessnum = j.sessnum
    WHERE s.start BETWEEN '$WIN_START' AND '$WIN_END'
" 2>/dev/null | gzip > "$OUT/joblog.sql.gz"
echo "  $(printf '%9d' "$(wc -c < "$OUT/joblog.sql.gz")")  joblog.sql.gz"

# ── Hub-side: full snapshots of slow-changing reference tables ────
for t in jos_resources jos_resource_assoc jos_xprofiles jos_users \
         jos_user_profiles jos_tool_version jos_tool_version_alias; do
    dump_full "$HUB_DB" "$t"
done

echo
echo "Snapshot ready in $OUT  ($(du -sh "$OUT" | cut -f1) total)"
