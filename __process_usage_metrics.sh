#!/bin/bash
# @package      hubzero-metrics
# @file         __process_usage_metrics.sh
# @copyright    Copyright (c) 2016-2023 The Regents of the University of California.
# @license      http://opensource.org/licenses/MIT MIT
# @trademark    HUBzero is a registered trademark of The Regents of the University of California.
#
# =========================================================================
# This script uses the hub's database and logs to populate usage figures in the metrics database.
#
# USAGE: ./__process_usage_metrics.sh [YYYY-MM]

SCRIPT=`readlink -f $0`
SCRIPTPATH=`dirname $SCRIPT`

echoerr() { echo "$@" 1>&2; }

$SCRIPTPATH/xlogfix_prep
$SCRIPTPATH/xlogimport_tool_and_reg_user_data
$SCRIPTPATH/xlogfix_middleware_wall
$SCRIPTPATH/xlogfix_middleware_cpu
$SCRIPTPATH/xlogfix_dns_v2 metrics web $1
$SCRIPTPATH/xlogfix_dns_v2 metrics toolstart $1
$SCRIPTPATH/xlogfix_domain metrics web
$SCRIPTPATH/xlogfix_domain metrics toolstart
$SCRIPTPATH/logfix_session
$SCRIPTPATH/xlogfix_clean web
$SCRIPTPATH/xlogfix_clean websessions
$SCRIPTPATH/xlogfix_user_info metrics toolstart
$SCRIPTPATH/xlogfix_ipcountry metrics web
$SCRIPTPATH/xlogfix_ipcountry metrics websessions
$SCRIPTPATH/xlogfix_ipcountry metrics toolstart

