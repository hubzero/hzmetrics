# @package      hubzero-metrics
# @file         Makefile
# @copyright    Copyright (c) 2010-2020 The Regents of the University of California.
# @license      http://opensource.org/licenses/MIT MIT
#
# Copyright (c) 2010-2020 The Regents of the University of California.
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
# HUBzero is a registered trademark of The Regents of the University of California.
#

ETC=$(DESTDIR)/etc
VAR_LOG=$(DESTDIR)/var/log
INSTALLDIR=$(DESTDIR)/opt/hubzero/bin

all:
	@true

install:
	install --owner apache --group apache --mode 0750 -D metrics/gen_tool_plots $(INSTALLDIR)/metrics/gen_tool_plots
	install --owner apache --group apache --mode 0750 -D metrics/gen_tool_stats $(INSTALLDIR)/metrics/gen_tool_stats
	install --owner apache --group apache --mode 0750 -D metrics/gen_tool_toplists $(INSTALLDIR)/metrics/gen_tool_toplists
	install --owner apache --group apache --mode 0750 -D metrics/gen_tool_tops $(INSTALLDIR)/metrics/gen_tool_tops
	install --owner apache --group apache --mode 0750 -D metrics/logfix_session $(INSTALLDIR)/metrics/logfix_session
	install --owner apache --group apache --mode 0750 -D metrics/__process_tool_metrics.sh $(INSTALLDIR)/metrics/__process_tool_metrics.sh
	install --owner apache --group apache --mode 0750 -D metrics/__process_usage_metrics.sh $(INSTALLDIR)/metrics/__process_usage_metrics.sh
	install --owner apache --group apache --mode 0750 -D metrics/__process_usage_metrics_summary.sh $(INSTALLDIR)/metrics/__process_usage_metrics_summary.sh
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_andmore_usage $(INSTALLDIR)/metrics/xlogfix_andmore_usage
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_clean $(INSTALLDIR)/metrics/xlogfix_clean
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_dns $(INSTALLDIR)/metrics/xlogfix_dns
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_dns_v2 $(INSTALLDIR)/metrics/xlogfix_dns_v2
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_dns_worker $(INSTALLDIR)/metrics/xlogfix_dns_worker
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_domain $(INSTALLDIR)/metrics/xlogfix_domain
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_ipcountry $(INSTALLDIR)/metrics/xlogfix_ipcountry
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_middleware_cpu $(INSTALLDIR)/metrics/xlogfix_middleware_cpu
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_middleware_wall $(INSTALLDIR)/metrics/xlogfix_middleware_wall
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_plot $(INSTALLDIR)/metrics/xlogfix_plot
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_prep $(INSTALLDIR)/metrics/xlogfix_prep
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_summary $(INSTALLDIR)/metrics/xlogfix_summary
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_user_info $(INSTALLDIR)/metrics/xlogfix_user_info
	install --owner apache --group apache --mode 0750 -D metrics/xlogfix_whoisonline $(INSTALLDIR)/metrics/xlogfix_whoisonline
	install --owner apache --group apache --mode 0750 -D metrics/xlogimport_tool_and_reg_user_data $(INSTALLDIR)/metrics/xlogimport_tool_and_reg_user_data

	install --owner apache --group apache --mode 0750 -D metrics/includes/db_connect.php $(INSTALLDIR)/metrics/includes/db_connect.php
	install --owner apache --group apache --mode 0750 -D metrics/includes/func_andmore.php $(INSTALLDIR)/metrics/includes/func_andmore.php
	install --owner apache --group apache --mode 0750 -D metrics/includes/func_misc.php $(INSTALLDIR)/metrics/includes/func_misc.php
	install --owner apache --group apache --mode 0750 -D metrics/includes/hub_parameters.php $(INSTALLDIR)/metrics/includes/hub_parameters.php
	install --owner apache --group apache --mode 0750 -D metrics/includes/xlogplotgraph $(INSTALLDIR)/metrics/includes/xlogplotgraph

	install --owner apache --group apache --mode 0750 -D metrics/import/__archive_apache_and_auth_log.sh $(INSTALLDIR)/metrics/import/__archive_apache_and_auth_log.sh
	install --owner apache --group apache --mode 0750 -D metrics/import/__fetch_apache_and_auth_log.sh $(INSTALLDIR)/metrics/import/__fetch_apache_and_auth_log.sh
	install --owner apache --group apache --mode 0750 -D metrics/import/__import_apache_and_auth_log.sh $(INSTALLDIR)/metrics/import/__import_apache_and_auth_log.sh
	install --owner apache --group apache --mode 0750 -D metrics/import/xlogfix_identify_bots $(INSTALLDIR)/metrics/import/xlogfix_identify_bots
	install --owner apache --group apache --mode 0750 -D metrics/import/xlogimport_apache $(INSTALLDIR)/metrics/import/xlogimport_apache
	install --owner apache --group apache --mode 0750 -D metrics/import/xlogimport_authlog $(INSTALLDIR)/metrics/import/xlogimport_authlog
	install --owner apache --group apache --mode 0750 -D metrics/import/xlogimport_webhits $(INSTALLDIR)/metrics/import/xlogimport_webhits

	install --owner apache --group apache --mode 0740 -D var/log/metrics/xlogfix.log $(VAR_LOG)/metrics/xlogfix.log

	install --mode 0640 -D conf/hubzero-metrics.cron.d $(ETC)/cron.d/metrics

uninstall:
	@true

postinst:
	@true

clean:
	@true

.PHONY: all install uninstall postinst clean
