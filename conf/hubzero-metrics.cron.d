# Metrics cron jobs
# Format is:
# min hour day-of-month month day-of-week user command...
#
*/5 * * * * apache  export PHPRC=/opt/remi/php56/root/etc/php-cli.ini;  /opt/hubzero/bin/metrics/scripts/xlogfix_whoisonline > /var/log/metrics/xlogfix.log
10 0 * * *  apache  export PHPRC=/opt/remi/php56/root/etc/php-cli.ini;  /opt/hubzero/bin/metrics/scripts/import/__fetch_apache_and_auth_log.sh
15 0 * * *  apache  export PHPRC=/opt/remi/php56/root/etc/php-cli.ini;  /opt/hubzero/bin/metrics/scripts/import/__import_apache_and_auth_log.sh
30 0 * * *  apache  export PHPRC=/opt/remi/php56/root/etc/php-cli.ini;  /opt/hubzero/bin/metrics/scripts/import/__archive_apache_and_auth_log.sh
40 0 * * *  apache  export PHPRC=/opt/remi/php56/root/etc/php-cli.ini;  /opt/hubzero/bin/metrics/scripts/__process_tool_metrics.sh
50 0 * * *  apache  export PHPRC=/opt/remi/php56/root/etc/php-cli.ini;  /opt/hubzero/bin/metrics/scripts/__process_usage_metrics.sh
50 1 1 * *  apache  export PHPRC=/opt/remi/php56/root/etc/php-cli.ini;  /opt/hubzero/bin/metrics/scripts/__process_usage_metrics_summary.sh
