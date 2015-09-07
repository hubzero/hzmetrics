# @package      hubzero-metrics
# @file         crontab_metrics
# @author       Swaroop Shivarajapura Samek <swaroop@purdue.edu>
# @copyright    Copyright (c) 2011-2015 HUBzero Foundation, LLC.
# @license      http://opensource.org/licenses/MIT MIT
#
# Copyright (c) 2011-2015 HUBzero Foundation, LLC.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
# HUBzero is a registered trademark of HUBzero Foundation, LLC.
#
# Metrics cron jobs
# Format is:
# min hour day-of-month month day-of-week user command...
#
# Use only on hosted HUBs
#MAILTO=hubmetrics@hubzero.org

*/5 * * * *	www-data	/opt/hubzero/bin/metrics/xlogfix_whoisonline
# 5 0 * * *   root		/opt/hubzero/bin/metrics/_setup_permissions_temp
10 0 * * *	www-data	/opt/hubzero/bin/metrics/import/__fetch_apache_and_auth_log.sh
15 0 * * *	www-data	/opt/hubzero/bin/metrics/import/__import_apache_and_auth_log.sh
30 0 * * *	www-data	/opt/hubzero/bin/metrics/import/__archive_apache_and_auth_log.sh
40 0 * * *	www-data	/opt/hubzero/bin/metrics/__process_tool_metrics.sh
50 0 * * *	www-data	/opt/hubzero/bin/metrics/__process_usage_metrics.sh
50 1 1 * *	www-data	/opt/hubzero/bin/metrics/__process_usage_metrics_summary.sh
