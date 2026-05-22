#!/bin/bash
# Regression test for the SQL-side download-extension regex.
#
# Background: do_backfill_dnload built its UPDATE via f-string with
# literal '^/resources/.*\\.(EXT)([?#]|$)' in single quotes.  Python
# turned `\\.` into `\.`; MariaDB's default string mode treats `\.` as
# the escape sequence "backslash-dot" and collapses it to plain `.`.
# The regex actually evaluated as `^/resources/.*.(EXT)([?#]|$)` — `.`
# matches any character, so URLs like `/resources/browse?tag=…,webinar`
# matched on the trailing `ar` (any-char then "r", which is the listed
# single-letter R-script EXT).  Commit db5d8ba (2026-05-18) silently
# fixed this when it moved the regex to a `%s` parameter; the commit
# message framed it as SQL-injection cleanup and never mentioned the
# semantic side-effect.  These rows pin the corrected behavior so a
# future revert-to-f-string would fail loudly.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AB="$(cd "$DIR/.." && pwd)"
. "$AB/conftest.sh"

OUT="$DIR/_out"; mkdir -p "$OUT"
LOG="$OUT/run.log"
: > "$LOG"

reset_test_dbs > /dev/null

# Seed: 4 real downloads + 4 historically false-positive shapes.
mysql_test "$METRICS_DB" <<'SQL' >> "$LOG" 2>&1
INSERT INTO web (datetime, content, ip, dnload) VALUES
  -- true positives: literal-dot ext OR /download/ path
  ('2025-03-10 10:00:01', '/resources/1234/foo.zip',                 '10.0.0.1', NULL),
  ('2025-03-10 10:00:02', '/resources/1234/sample.pdf?v=2',          '10.0.0.2', NULL),
  ('2025-03-10 10:00:03', '/resources/1234/download/anything',       '10.0.0.3', NULL),
  ('2025-03-10 10:00:04', '/resources/single-letter-r.r',            '10.0.0.4', NULL),
  -- false positives under the OLD buggy regex (no literal dot, but
  -- end in a single listed-EXT char or sequence of "any-char + ext")
  -- ',webinar' ends in 'r' — listed EXT.  Old regex matched .r$.
  ('2025-03-10 10:00:05', '/resources/browse?tag=a,webinar',         '10.0.0.5', NULL),
  -- bare 'browse?tag=...,nc' ends in 'nc' (also a listed EXT).
  ('2025-03-10 10:00:06', '/resources/browse?tag=foo,bar,nc',        '10.0.0.6', NULL),
  -- 'Xpy' under buggy regex: '.py$' matched any-char + 'py'.
  ('2025-03-10 10:00:07', '/resources/browseXpy',                    '10.0.0.7', NULL),
  -- 'doNOTtouch' — controls; nothing on the EXT list ends here.
  ('2025-03-10 10:00:08', '/resources/browse?tag=nothing-special',   '10.0.0.8', NULL);
SQL

run_new backfill-dnload --start 2025-03 >> "$LOG" 2>&1

# Capture the resulting (content, dnload) pairs in deterministic order.
ACTUAL=$(mysql_test "$METRICS_DB" -BN -e "
    SELECT content, IFNULL(dnload, 'NULL') FROM web
    WHERE datetime >= '2025-03-01' AND datetime < '2025-04-01'
    ORDER BY datetime;
")

# Reference: true-positive rows should be 1, false-positive shapes 0.
EXPECTED=$(cat <<'EOF'
/resources/1234/foo.zip	1
/resources/1234/sample.pdf?v=2	1
/resources/1234/download/anything	1
/resources/single-letter-r.r	1
/resources/browse?tag=a,webinar	0
/resources/browse?tag=foo,bar,nc	0
/resources/browseXpy	0
/resources/browse?tag=nothing-special	0
EOF
)

if [ "$ACTUAL" = "$EXPECTED" ]; then
    echo "PASS"
    exit 0
fi

echo "FAIL  backfill-dnload regex mis-classified rows"
echo "  expected:"
echo "$EXPECTED" | sed 's/^/    /'
echo "  actual:"
echo "$ACTUAL"   | sed 's/^/    /'
echo "  diff:"
diff <(echo "$EXPECTED") <(echo "$ACTUAL") | sed 's/^/    /'
exit 1
