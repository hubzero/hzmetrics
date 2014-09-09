# @package      hubzero-metrics
# @file         Makefile
# @author       Nicholas J. Kisseberth <nkissebe@purdue.edu>
# @copyright    Copyright (c) 2012-2014 HUBzero Foundation, LLC.
# @license      http://www.gnu.org/licenses/lgpl-3.0.html LGPLv3
#
# Copyright (c) 2012-2014 HUBzero Foundation, LLC.
#
# This file is part of: The HUBzero(R) Platform for Scientific Collaboration
#
# The HUBzero(R) Platform for Scientific Collaboration (HUBzero) is free
# software: you can redistribute it and/or modify it under the terms of
# the GNU Lesser General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# HUBzero is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# HUBzero is a registered trademark of HUBzero Foundation, LLC.
#

ETC=$(DESTDIR)/etc
USRSHARE=$(DESTDIR)/usr/share

all:
	@true

install:
	install --mode 0755 -D metrics/gen_tool_plots $(USRSHARE)/hubzero-metrics/scripts/gen_tool_plots
	install --mode 0755 -D metrics/__process_usage_metrics_summary.sh $(USRSHARE)/hubzero-metrics/scripts/__process_usage_metrics_summary.sh
	install --mode 0755 -D metrics/xlogfix_domain $(USRSHARE)/hubzero-metrics/scripts/xlogfix_domain
	install --mode 0755 -D metrics/xlogfix_prep $(USRSHARE)/hubzero-metrics/scripts/xlogfix_prep
	install --mode 0755 -D metrics/gen_tool_stats $(USRSHARE)/hubzero-metrics/scripts/gen_tool_stats
	install --mode 0755 -D metrics/_setup_permissions_temp $(USRSHARE)/hubzero-metrics/scripts/_setup_permissions_temp
	install --mode 0755 -D metrics/xlogfix_ipcountry $(USRSHARE)/hubzero-metrics/scripts/xlogfix_ipcountry
	install --mode 0755 -D metrics/xlogfix_summary $(USRSHARE)/hubzero-metrics/scripts/xlogfix_summary
	install --mode 0755 -D metrics/gen_tool_toplists $(USRSHARE)/hubzero-metrics/scripts/gen_tool_toplists
	install --mode 0755 -D metrics/logfix_session $(USRSHARE)/hubzero-metrics/scripts/logfix_session
	install --mode 0755 -D metrics/xlogfix_andmore_usage $(USRSHARE)/hubzero-metrics/scripts/xlogfix_andmore_usage
	install --mode 0755 -D metrics/xlogfix_middleware_cpu $(USRSHARE)/hubzero-metrics/scripts/xlogfix_middleware_cpu
	install --mode 0755 -D metrics/xlogfix_user_info $(USRSHARE)/hubzero-metrics/scripts/xlogfix_user_info
	install --mode 0755 -D metrics/gen_tool_tops $(USRSHARE)/hubzero-metrics/scripts/gen_tool_tops
	install --mode 0755 -D metrics/__process_tool_metrics.sh $(USRSHARE)/hubzero-metrics/scripts/__process_tool_metrics.sh
	install --mode 0755 -D metrics/xlogfix_clean $(USRSHARE)/hubzero-metrics/scripts/xlogfix_clean
	install --mode 0755 -D metrics/xlogfix_middleware_wall $(USRSHARE)/hubzero-metrics/scripts/xlogfix_middleware_wall
	install --mode 0755 -D metrics/xlogfix_whoisonline $(USRSHARE)/hubzero-metrics/scripts/xlogfix_whoisonline
	install --mode 0755 -D metrics/__process_usage_metrics.sh $(USRSHARE)/hubzero-metrics/scripts/__process_usage_metrics.sh
	install --mode 0755 -D metrics/xlogfix_dns $(USRSHARE)/hubzero-metrics/scripts/xlogfix_dns
	install --mode 0755 -D metrics/xlogfix_plot $(USRSHARE)/hubzero-metrics/scripts/xlogfix_plot
	install --mode 0755 -D metrics/xlogimport_tool_and_reg_user_data $(USRSHARE)/hubzero-metrics/scripts/xlogimport_tool_and_reg_user_data
	install --mode 0755 -D metrics/includes/db_connect.php $(USRSHARE)/hubzero-metrics/scripts/includes/db_connect.php
	install --mode 0755 -D metrics/includes/func_andmore.php $(USRSHARE)/hubzero-metrics/scripts/includes/func_andmore.php
	install --mode 0755 -D metrics/includes/func_misc.php $(USRSHARE)/hubzero-metrics/scripts/includes/func_misc.php
	install --mode 0755 -D metrics/includes/hub_parameters.php $(USRSHARE)/hubzero-metrics/scripts/includes/hub_parameters.php
	install --mode 0755 -D metrics/includes/xlogplotgraph $(USRSHARE)/hubzero-metrics/scripts/includes/xlogplotgraph
	install --mode 0755 -D metrics/import/__archive_apache_and_auth_log.sh $(USRSHARE)/hubzero-metrics/scripts/import/__archive_apache_and_auth_log.sh
	install --mode 0755 -D metrics/import/__fetch_apache_and_auth_log.sh $(USRSHARE)/hubzero-metrics/scripts/import/__fetch_apache_and_auth_log.sh
	install --mode 0755 -D metrics/import/__import_apache_and_auth_log.sh $(USRSHARE)/hubzero-metrics/scripts/import/__import_apache_and_auth_log.sh
	install --mode 0755 -D metrics/import/xlogfix_identify_bots $(USRSHARE)/hubzero-metrics/scripts/import/xlogfix_identify_bots
	install --mode 0755 -D metrics/import/xlogimport_apache $(USRSHARE)/hubzero-metrics/scripts/import/xlogimport_apache
	install --mode 0755 -D metrics/import/xlogimport_authlog $(USRSHARE)/hubzero-metrics/scripts/import/xlogimport_authlog
	install --mode 0755 -D metrics/import/xlogimport_webhits $(USRSHARE)/hubzero-metrics/scripts/import/xlogimport_webhits
	install --mode 0644 -D metrics/_install/hub_files/db_create_metrics_tables.sql $(USRSHARE)/hubzero-metrics/hubzero_metrics.sql
	mkdir -p $(ETC)/cron.d
	mkdir -p $(ETC)/cron.d
	mkdir -p $(ETC)/cron.d
	sed -e "s#/opt/hubzero/bin/metrics#/usr/share/hubzero-metrics/scripts#" metrics/_install/hub_files/crontab_metrics   > $(ETC)/cron.d/crontab_metrics

postinst:
	@true

clean:
	@true

.PHONY: all install postinst clean
