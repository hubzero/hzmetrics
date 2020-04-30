#!/bin/bash 
# 
# @package      hubzero-metrics
# @file         _process_tool_metrics.sh
# @copyright    Copyright (c) 2011-2015 The Regents of the University of California.
# @license      http://opensource.org/licenses/MIT MIT
#
# Copyright (c) 2011-2015 The Regents of the University of California.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
# HUBzero is a registered trademark of The Regents of the University of California.
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
