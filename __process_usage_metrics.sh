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

$SCRIPTPATH/xlogfix_prep.php
$SCRIPTPATH/xlogimport_tool_and_reg_user_data.php
$SCRIPTPATH/xlogfix_middleware_wall.pl
$SCRIPTPATH/xlogfix_middleware_cpu.pl
$SCRIPTPATH/xlogfix_dns_v2.sh metrics web $1
$SCRIPTPATH/xlogfix_dns_v2.sh metrics toolstart $1
$SCRIPTPATH/xlogfix_domain.php metrics web
$SCRIPTPATH/xlogfix_domain.php metrics toolstart
$SCRIPTPATH/logfix_session.pl
$SCRIPTPATH/xlogfix_clean.php web
$SCRIPTPATH/xlogfix_clean.php websessions
$SCRIPTPATH/xlogfix_user_info.php metrics toolstart $1
$SCRIPTPATH/xlogfix_ipcountry.php metrics web $1
$SCRIPTPATH/xlogfix_ipcountry.php metrics websessions $1
$SCRIPTPATH/xlogfix_ipcountry.php metrics toolstart $1

