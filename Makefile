# @package      hubzero-metrics
# @file         Makefile
# @author       Nicholas J. Kisseberth <nkissebe@purdue.edu>
# @copyright    Copyright (c) 2012 HUBzero Foundation, LLC.
# @license      http://www.gnu.org/licenses/lgpl-3.0.html LGPLv3
#
# Copyright (c) 2012 HUBzero Foundation, LLC.
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

BASESCRIPTS=gen_tool_plots 				__process_usage_metrics_summary.sh  xlogfix_domain \
			xlogfix_prep 				gen_tool_stats 						_setup_permissions_temp \
			xlogfix_ipcountry 			xlogfix_summary 					gen_tool_toplists \
			logfix_session				xlogfix_andmore_usage				xlogfix_middleware_cpu \
			xlogfix_user_info			gen_tool_tops			   			__process_tool_metrics.sh \
			xlogfix_clean				xlogfix_middleware_wall  			xlogfix_whoisonline \
   			__process_usage_metrics.sh	xlogfix_dns			   				xlogfix_plot \
   			xlogimport_tool_and_reg_user_data

INCLUDES=	db_connect.php	func_andmore.php	func_misc.php hub_parameters.php	xlogplotgraph

IMPORT=	_archive_apache_and_auth_log.sh			__fetch_apache_and_auth_log.sh		__import_apache_and_auth_log.sh \
		xlogfix_identify_bots					xlogimport_apache					xlogimport_authlog \
		xlogimport_webhits


default all build:
	@true

install:
	for f in $(BASESCRIPTS); do install --mode 0755 -D $$f $(USRSHAREDIR)/hubzero-metrics/$$f ; done
	for f in $(INCLUDES); do install --mode 0755 -D $$f $(USRSHAREDIR)/hubzero-metrics/includes/$$f ; done
	for f in $(IMPORT); do install --mode 0755 -D $$f $(USRSHAREDIR)/hubzero-metrics/import/$$f ; done


configure:
	@true
	
clean:
	rm -f build-stamp configure-stamp
