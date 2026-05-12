#!/bin/sh
# Logrotate postrotate hook for hzmetrics pipeline.
#
# Add this to the postrotate/endscript block of whichever logrotate config
# manages the Apache access logs (e.g. /etc/logrotate.d/httpd or a site-specific
# config).  It triggers hzmetrics immediately after log rotation completes,
# which is more reliable than relying on cron timing — especially for end-of-month
# processing where the last day's log must arrive before the summary can run.
#
# Example logrotate stanza:
#
#   /var/log/httpd/-access.log {
#       daily
#       rotate 365
#       dateext
#       ...
#       postrotate
#           /opt/hubzero/bin/metrics/import/__fetch_apache_and_auth_log.sh
#           /opt/hubzero/bin/hzmetrics-postrotate.sh
#       endscript
#   }
#
# If logrotate runs as root, the script uses sudo to run hzmetrics as apache.
# If logrotate already runs as apache, remove the sudo wrapper.
#
# The pipeline is run in the background (&) so logrotate does not wait for it.
# The hzmetrics lock file prevents overlapping runs if the cron fires concurrently.

sudo -u apache python3 /opt/hubzero/bin/hzmetrics.py run &
