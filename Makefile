# @package      hubzero-metrics
# @file         Makefile
# @author       Nicholas J. Kisseberth <nkissebe@purdue.edu>
# @copyright    Copyright (c) 2010-2018 HUBzero Foundation, LLC.
# @license      http://opensource.org/licenses/MIT MIT
#
# Copyright (c) 2010-2018 HUBzero Foundation, LLC.
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

ETC=$(DESTDIR)/etc
USRSHARE=$(DESTDIR)/usr/share

all:
	@true

install:
	install --mode 0755 -D metrics/gen_tool_plots $(USRSHARE)/hubzero-metrics/scripts/gen_tool_plots
	install --mode 0755 -D metrics/gen_tool_stats $(USRSHARE)/hubzero-metrics/scripts/gen_tool_stats
	install --mode 0755 -D metrics/gen_tool_toplists $(USRSHARE)/hubzero-metrics/scripts/gen_tool_toplists
	install --mode 0755 -D metrics/gen_tool_tops $(USRSHARE)/hubzero-metrics/scripts/gen_tool_tops
	install --mode 0755 -D metrics/logfix_session $(USRSHARE)/hubzero-metrics/scripts/logfix_session
	install --mode 0755 -D metrics/__process_tool_metrics.sh $(USRSHARE)/hubzero-metrics/scripts/__process_tool_metrics.sh
	install --mode 0755 -D metrics/__process_usage_metrics.sh $(USRSHARE)/hubzero-metrics/scripts/__process_usage_metrics.sh
	install --mode 0755 -D metrics/__process_usage_metrics_summary.sh $(USRSHARE)/hubzero-metrics/scripts/__process_usage_metrics_summary.sh
	install --mode 0755 -D metrics/xlogfix_andmore_usage $(USRSHARE)/hubzero-metrics/scripts/xlogfix_andmore_usage
	install --mode 0755 -D metrics/xlogfix_clean $(USRSHARE)/hubzero-metrics/scripts/xlogfix_clean
	install --mode 0755 -D metrics/xlogfix_dns $(USRSHARE)/hubzero-metrics/scripts/xlogfix_dns
	install --mode 0755 -D metrics/xlogfix_domain $(USRSHARE)/hubzero-metrics/scripts/xlogfix_domain
	install --mode 0755 -D metrics/xlogfix_ipcountry $(USRSHARE)/hubzero-metrics/scripts/xlogfix_ipcountry
	install --mode 0755 -D metrics/xlogfix_middleware_cpu $(USRSHARE)/hubzero-metrics/scripts/xlogfix_middleware_cpu
	install --mode 0755 -D metrics/xlogfix_middleware_wall $(USRSHARE)/hubzero-metrics/scripts/xlogfix_middleware_wall
	install --mode 0755 -D metrics/xlogfix_plot $(USRSHARE)/hubzero-metrics/scripts/xlogfix_plot
	install --mode 0755 -D metrics/xlogfix_prep $(USRSHARE)/hubzero-metrics/scripts/xlogfix_prep
	install --mode 0755 -D metrics/xlogfix_summary $(USRSHARE)/hubzero-metrics/scripts/xlogfix_summary
	install --mode 0755 -D metrics/xlogfix_user_info $(USRSHARE)/hubzero-metrics/scripts/xlogfix_user_info
	install --mode 0755 -D metrics/xlogfix_whoisonline $(USRSHARE)/hubzero-metrics/scripts/xlogfix_whoisonline
	install --mode 0755 -D metrics/xlogimport_tool_and_reg_user_data $(USRSHARE)/hubzero-metrics/scripts/xlogimport_tool_and_reg_user_data

	install --mode 0644 -D metrics/includes/db_connect.php $(USRSHARE)/hubzero-metrics/scripts/includes/db_connect.php
	install --mode 0644 -D metrics/includes/func_andmore.php $(USRSHARE)/hubzero-metrics/scripts/includes/func_andmore.php
	install --mode 0644 -D metrics/includes/func_misc.php $(USRSHARE)/hubzero-metrics/scripts/includes/func_misc.php
	install --mode 0644 -D metrics/includes/hub_parameters.php $(USRSHARE)/hubzero-metrics/scripts/includes/hub_parameters.php
	install --mode 0755 -D metrics/includes/xlogplotgraph $(USRSHARE)/hubzero-metrics/scripts/includes/xlogplotgraph

	install --mode 0755 -D metrics/import/__archive_apache_and_auth_log.sh $(USRSHARE)/hubzero-metrics/scripts/import/__archive_apache_and_auth_log.sh
	install --mode 0755 -D metrics/import/__fetch_apache_and_auth_log.sh $(USRSHARE)/hubzero-metrics/scripts/import/__fetch_apache_and_auth_log.sh
	install --mode 0755 -D metrics/import/__import_apache_and_auth_log.sh $(USRSHARE)/hubzero-metrics/scripts/import/__import_apache_and_auth_log.sh
	install --mode 0755 -D metrics/import/xlogfix_identify_bots $(USRSHARE)/hubzero-metrics/scripts/import/xlogfix_identify_bots
	install --mode 0755 -D metrics/import/xlogimport_apache $(USRSHARE)/hubzero-metrics/scripts/import/xlogimport_apache
	install --mode 0755 -D metrics/import/xlogimport_authlog $(USRSHARE)/hubzero-metrics/scripts/import/xlogimport_authlog
	install --mode 0755 -D metrics/import/xlogimport_webhits $(USRSHARE)/hubzero-metrics/scripts/import/xlogimport_webhits

	install --mode 0644 -D conf/hubzero-metrics.cron.d $(USRSHARE)/hubzero-metrics/conf/hubzero-metrics.cron.d

uninstall:
	@true

postinst:
	@true

clean:
	@true

.PHONY: all install uninstall postinst clean
