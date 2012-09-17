#!/bin/bash 
SCRIPT=`readlink -f $0`
SCRIPTPATH=`dirname $SCRIPT`

$SCRIPTPATH/xlogfix_prep
$SCRIPTPATH/xlogimport_tool_and_reg_user_data
$SCRIPTPATH/xlogfix_summary $1
$SCRIPTPATH/xlogfix_plot $1
$SCRIPTPATH/xlogfix_andmore_usage $1
