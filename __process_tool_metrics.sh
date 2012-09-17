#!/bin/bash 
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
