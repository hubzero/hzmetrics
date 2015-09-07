#!/bin/bash 
#
# @package      hubzero-metrics
# @file         _import_apache_and_auth_log.sh
# @author       Swaroop Shivarajapura Samek <swaroop@purdue.edu>
# @copyright    Copyright (c) 2011-2015 HUBzero Foundation, LLC.
# @license      http://opensource.org/licenses/MIT MIT
#
# Copyright (c) 2011-2015 HUBzero Foundation, LLC.
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
# HUBzero is a registered trademark of HUBzero Foundation, LLC.
#

SCRIPT=`readlink -f $0`
SCRIPTPATH=`dirname $SCRIPT`

# -----------------------------------------------------------------------------------------------
# Importing apache logs
# -----------------------------------------------------------------------------------------------
if [ -f $SCRIPTPATH/_hub_apache.log ]
then
	$SCRIPTPATH/xlogimport_webhits $SCRIPTPATH/_hub_apache.log >> $SCRIPTPATH/_apache_webhits.err
	$SCRIPTPATH/xlogfix_identify_bots $SCRIPTPATH/_hub_apache.log >> $SCRIPTPATH/_apache_bots.err
	$SCRIPTPATH/xlogimport_apache $SCRIPTPATH/_hub_apache.log >> $SCRIPTPATH/_apache_import.err
	mv $SCRIPTPATH/_hub_apache.log $SCRIPTPATH/_prev_hub_apache.log
fi

# -----------------------------------------------------------------------------------------------
# Importing CMS logs
# -----------------------------------------------------------------------------------------------
if [ -f $SCRIPTPATH/_hub_auth.log ]
then
	$SCRIPTPATH/xlogimport_authlog $SCRIPTPATH/_hub_auth.log >> $SCRIPTPATH/_cmsauth_import.err
	mv $SCRIPTPATH/_hub_auth.log $SCRIPTPATH/_prev_hub_auth.log
fi
