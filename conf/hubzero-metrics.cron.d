# HUBzero metrics pipeline (drop-in /etc/cron.d/ flavor — runs as apache).
# Format: min hour dom month dow user command
# Every 5 min: updates whoisonline map; at :30 past each hour also runs the
# metrics pipeline under flock.  Reads the unified config from
# /opt/hubzero/metrics/conf/hzmetrics.conf by default.  For multi-tenant
# hosts, add one line per tenant with `-c /etc/hzmetrics/<hub>.conf`.
#
# Cron mails command output by default.  The pipeline routes its own logs to
# syslog LOG_LOCAL0 + /var/log/hubzero/metrics/manage.log + stderr (INFO+),
# so cron mail would just duplicate noise.  MAILTO="" silences the cron-side
# mailer.  Set MAILTO=operator@example.com (without the quotes) if you'd
# rather have failures land in inboxes instead of the file/syslog stream.
MAILTO=""
# Explicit PATH so the script's 3.10+ self-relaunch can find python3.11
# even when it lives outside cron's default /usr/bin:/bin (e.g.
# /usr/local/bin from a pip install, or /opt/rh/.../bin from RHEL SCL).
PATH=/usr/local/bin:/usr/bin:/bin:/opt/rh/python3.11/root/usr/bin
*/5 * * * * apache  python3 /opt/hubzero/metrics/bin/hzmetrics.py tick
