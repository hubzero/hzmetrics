*/15 * * * *	www-data	/usr/share/hubzero-metrics/scripts/xlogfix_whoisonline
10 0 * * *	www-data	/usr/share/hubzero-metrics/scripts/import/__fetch_apache_and_auth_log.sh
15 0 * * *	www-data	/usr/share/hubzero-metrics/scripts/import/__import_apache_and_auth_log.sh
30 0 * * *	www-data	/usr/share/hubzero-metrics/scripts/import/__archive_apache_and_auth_log.sh
40 0 * * *	www-data	/usr/share/hubzero-metrics/scripts/__process_tool_metrics.sh
50 0 * * *	www-data	/usr/share/hubzero-metrics/scripts/__process_usage_metrics.sh
50 1 1 * *	www-data	/usr/share/hubzero-metrics/scripts/__process_usage_metrics_summary.sh
