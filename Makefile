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
	install --mode 0750 -D metrics/gen_tool_plots.pl $(INSTALLDIR)/metrics/gen_tool_plots.pl
	install --mode 0750 -D metrics/gen_tool_stats.php $(INSTALLDIR)/metrics/gen_tool_stats.php
	install --mode 0750 -D metrics/gen_tool_toplists.php $(INSTALLDIR)/metrics/gen_tool_toplists.php
	install --mode 0750 -D metrics/gen_tool_tops.php $(INSTALLDIR)/metrics/gen_tool_tops.php
	install --mode 0750 -D metrics/logfix_session.pl $(INSTALLDIR)/metrics/logfix_session.pl
	install --mode 0750 -D metrics/__process_tool_metrics.sh $(INSTALLDIR)/metrics/__process_tool_metrics.sh
	install --mode 0750 -D metrics/__process_usage_metrics.sh $(INSTALLDIR)/metrics/__process_usage_metrics.sh
	install --mode 0750 -D metrics/__process_usage_metrics_summary.sh $(INSTALLDIR)/metrics/__process_usage_metrics_summary.sh
	install --mode 0750 -D metrics/xlogfix_andmore_usage.php $(INSTALLDIR)/metrics/xlogfix_andmore_usage.php
	install --mode 0750 -D metrics/xlogfix_clean.php $(INSTALLDIR)/metrics/xlogfix_clean.php
	install --mode 0750 -D metrics/xlogfix_dns_v2.sh $(INSTALLDIR)/metrics/xlogfix_dns_v2.sh
	install --mode 0750 -D metrics/xlogfix_dns_worker.php $(INSTALLDIR)/metrics/xlogfix_dns_worker.php
	install --mode 0750 -D metrics/xlogfix_domain.php $(INSTALLDIR)/metrics/xlogfix_domain.php
	install --mode 0750 -D metrics/xlogfix_ipcountry.php $(INSTALLDIR)/metrics/xlogfix_ipcountry.php
	install --mode 0750 -D metrics/xlogfix_middleware_cpu.pl $(INSTALLDIR)/metrics/xlogfix_middleware_cpu.pl
	install --mode 0750 -D metrics/xlogfix_middleware_wall.pl $(INSTALLDIR)/metrics/xlogfix_middleware_wall.pl
	install --mode 0750 -D metrics/xlogfix_plot.pl $(INSTALLDIR)/metrics/xlogfix_plot.pl
	install --mode 0750 -D metrics/xlogfix_prep.php $(INSTALLDIR)/metrics/xlogfix_prep.php
	install --mode 0750 -D metrics/xlogfix_summary.php $(INSTALLDIR)/metrics/xlogfix_summary.php
	install --mode 0750 -D metrics/xlogfix_user_info.php $(INSTALLDIR)/metrics/xlogfix_user_info.php
	install --mode 0750 -D metrics/xlogfix_whoisonline.php $(INSTALLDIR)/metrics/xlogfix_whoisonline.php
	install --mode 0750 -D metrics/xlogimport_tool_and_reg_user_data.php $(INSTALLDIR)/metrics/xlogimport_tool_and_reg_user_data.php

	install --mode 0640 -D metrics/includes/db_connect.php $(INSTALLDIR)/metrics/includes/db_connect.php
	install --mode 0640 -D metrics/includes/func_andmore.php $(INSTALLDIR)/metrics/includes/func_andmore.php
	install --mode 0640 -D metrics/includes/func_misc.php $(INSTALLDIR)/metrics/includes/func_misc.php
	install --mode 0640 -D metrics/includes/hub_parameters.php $(INSTALLDIR)/metrics/includes/hub_parameters.php
	install --mode 0750 -D metrics/includes/xlogplotgraph.pl $(INSTALLDIR)/metrics/includes/xlogplotgraph.pl

	install --mode 0750 -D metrics/import/__archive_apache_and_auth_log.sh $(INSTALLDIR)/metrics/import/__archive_apache_and_auth_log.sh
	install --mode 0750 -D metrics/import/__fetch_apache_and_auth_log.sh $(INSTALLDIR)/metrics/import/__fetch_apache_and_auth_log.sh
	install --mode 0750 -D metrics/import/__import_apache_and_auth_log.sh $(INSTALLDIR)/metrics/import/__import_apache_and_auth_log.sh
	install --mode 0750 -D metrics/import/xlogfix_identify_bots.php $(INSTALLDIR)/metrics/import/xlogfix_identify_bots.php
	install --mode 0750 -D metrics/import/xlogimport_apache.php $(INSTALLDIR)/metrics/import/xlogimport_apache.php
	install --mode 0750 -D metrics/import/xlogimport_authlog.php $(INSTALLDIR)/metrics/import/xlogimport_authlog.php
	install --mode 0750 -D metrics/import/xlogimport_webhits.php $(INSTALLDIR)/metrics/import/xlogimport_webhits.php

	install --mode 0740 -D var/log/metrics/xlogfix.log $(VAR_LOG)/metrics/xlogfix.log

	install --mode 0640 -D conf/hubzero-metrics.cron.d $(ETC)/cron.d/metrics

uninstall:
	@true

postinst:
	@true

clean:
	@true

.PHONY: all install uninstall postinst clean
