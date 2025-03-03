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

$SCRIPTPATH/xlogfix_prep.php
$SCRIPTPATH/xlogimport_tool_and_reg_user_data.php
$SCRIPTPATH/xlogfix_dns_v2.sh metrics sessionlog_metrics $1
$SCRIPTPATH/xlogfix_domain.php metrics sessionlog_metrics $1
$SCRIPTPATH/xlogfix_user_info.php metrics sessionlog_metrics $1
$SCRIPTPATH/xlogfix_ipcountry.php metrics sessionlog_metrics $1
$SCRIPTPATH/gen_tool_stats.php $1
$SCRIPTPATH/gen_tool_tops.php $1
$SCRIPTPATH/gen_tool_toplists.php $1
