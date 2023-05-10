#!/bin/bash 
# @package      hubzero-metrics
# @file         __process_usage_metrics_summary.sh
# @copyright    Copyright (c) 2016-2023 The Regents of the University of California.
# @license      http://opensource.org/licenses/MIT MIT
# @trademark    HUBzero is a registered trademark of The Regents of the University of California.
#
# =========================================================================
# This script summarizes usage figures for the month and populates them in the metrics database.
#
# USAGE: ./__process_usage_metrics_summary.sh [YYYY-MM]

SCRIPT=`readlink -f $0`
SCRIPTPATH=`dirname $SCRIPT`

$SCRIPTPATH/xlogfix_prep.php
$SCRIPTPATH/xlogimport_tool_and_reg_user_data.php
$SCRIPTPATH/xlogfix_summary.php $1
$SCRIPTPATH/xlogfix_plot.pl $1
$SCRIPTPATH/xlogfix_andmore_usage.php $1
