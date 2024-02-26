# Metrics cron jobs
# Format is:
# min hour day-of-month month day-of-week user command...
#
*/5 * * * * apache  /opt/hubzero/bin/metrics/xlogfix_whoisonline.php > /var/log/metrics/xlogfix.log
10 0 * * *  apache  /opt/hubzero/bin/metrics/import/__fetch_apache_and_auth_log.sh
15 0 * * *  apache  /opt/hubzero/bin/metrics/import/__import_apache_and_auth_log.sh
30 0 * * *  apache  /opt/hubzero/bin/metrics/import/__archive_apache_and_auth_log.sh
40 0 * * *  apache  /opt/hubzero/bin/metrics/__process_tool_metrics.sh
50 0 * * *  apache  /opt/hubzero/bin/metrics/__process_usage_metrics.sh
50 1 1 * *  apache  /opt/hubzero/bin/metrics/__process_usage_metrics_summary.sh
