#!/bin/bash 
# 
# @package      hubzero-metrics
# @file         _process_tool_metrics.sh
# @author       Swaroop Shivarajapura <swaroop@purdue.edu>
# @copyright    Copyright (c) 2011-2013 HUBzero Foundation, LLC.
# @license      http://www.gnu.org/licenses/lgpl-3.0.html LGPLv3
#
# Copyright (c) 2011-2013 HUBzero Foundation, LLC.
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

SCRIPT=`readlink -f $0`
SCRIPTPATH=`dirname $SCRIPT`

$SCRIPTPATH/xlogfix_prep
$SCRIPTPATH/xlogimport_tool_and_reg_user_data
$SCRIPTPATH/xlogfix_dns metrics sessionlog_metrics
$SCRIPTPATH/xlogfix_domain metrics sessionlog_metrics
$SCRIPTPATH/xlogfix_user_info metrics sessionlog_metrics
$SCRIPTPATH/xlogfix_ipcountry metrics sessionlog_metrics
$SCRIPTPATH/gen_tool_stats $1
$SCRIPTPATH/gen_tool_tops $1
$SCRIPTPATH/gen_tool_plots $1
$SCRIPTPATH/gen_tool_toplists $1
