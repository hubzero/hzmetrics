# HUBzero metrics pipeline (drop-in /etc/cron.d/ flavor — runs as apache).
# Format: min hour dom month dow user command
# Every 5 min: updates whoisonline map; at :30 past each hour also runs the
# metrics pipeline under flock.  The script reads $HZMETRICS_HOME/conf/access.cfg
# by default; override with HZMETRICS_ACCESS_CFG if the cfg lives elsewhere
# (e.g. operators migrating from the pre-2026 /etc/hubzero-metrics/ layout).
#
# Cron mails command output by default.  The pipeline routes its own logs to
# syslog LOG_LOCAL0 + /var/log/hubzero/metrics/manage.log + stderr (INFO+),
# so cron mail would just duplicate noise.  MAILTO="" silences the cron-side
# mailer.  Set MAILTO=operator@example.com (without the quotes) if you'd
# rather have failures land in inboxes instead of the file/syslog stream.
MAILTO=""
*/5 * * * * apache  python3 /opt/hubzero/metrics/bin/hzmetrics.py tick
