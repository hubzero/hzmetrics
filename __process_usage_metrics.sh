#!/bin/bash
SCRIPT=`readlink -f $0`
SCRIPTPATH=`dirname $SCRIPT`

echoerr() { echo "$@" 1>&2; }

$SCRIPTPATH/xlogfix_prep
$SCRIPTPATH/xlogimport_tool_and_reg_user_data
$SCRIPTPATH/xlogfix_middleware_wall
$SCRIPTPATH/xlogfix_middleware_cpu
$SCRIPTPATH/xlogfix_dns_v2 metrics web
$SCRIPTPATH/xlogfix_dns_v2 metrics toolstart
$SCRIPTPATH/xlogfix_domain metrics web
$SCRIPTPATH/xlogfix_domain metrics toolstart
$SCRIPTPATH/logfix_session
$SCRIPTPATH/xlogfix_clean web
$SCRIPTPATH/xlogfix_clean websessions
$SCRIPTPATH/xlogfix_user_info metrics toolstart
$SCRIPTPATH/xlogfix_ipcountry metrics web
$SCRIPTPATH/xlogfix_ipcountry metrics websessions
$SCRIPTPATH/xlogfix_ipcountry metrics toolstart

