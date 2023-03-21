#!/bin/bash 
# @package      hubzero-metrics
# @file         __process_tool_metrics.sh
# @copyright    Copyright (c) 2016-2023 The Regents of the University of California.
# @license      http://opensource.org/licenses/MIT MIT
# @trademark    HUBzero is a registered trademark of The Regents of the University of California.
#
# =========================================================================
# This script uses the hub's database and logs to populate tool usage figures in the metrics database.
#
# USAGE: ./__process_tool_metrics.sh [YYYY-MM]

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
