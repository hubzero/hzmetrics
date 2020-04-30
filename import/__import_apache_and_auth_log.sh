#!/bin/bash 
#
# @package      hubzero-metrics
# @file         _import_apache_and_auth_log.sh
# @author       Swaroop Shivarajapura Samek <swaroop@purdue.edu>
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
METRICSLOGDIR=/var/log/hubzero/metrics

if [ ! -d $METRICSLOGDIR ]
then
  mkdir -p $METRICSLOGDIR
fi

# -----------------------------------------------------------------------------------------------
# Importing apache logs
# -----------------------------------------------------------------------------------------------
if [ -f $METRICSLOGDIR/_hub_apache.log ]
then
	$SCRIPTPATH/xlogimport_webhits $METRICSLOGDIR/_hub_apache.log >> $METRICSLOGDIR/_apache_webhits.err
	$SCRIPTPATH/xlogfix_identify_bots $METRICSLOGDIR/_hub_apache.log >> $METRICSLOGDIR/_apache_bots.err
	$SCRIPTPATH/xlogimport_apache $METRICSLOGDIR/_hub_apache.log >> $METRICSLOGDIR/_apache_import.err
	mv $METRICSLOGDIR/_hub_apache.log $METRICSLOGDIR/_prev_hub_apache.log
fi

# -----------------------------------------------------------------------------------------------
# Importing CMS logs
# -----------------------------------------------------------------------------------------------
if [ -f $METRICSLOGDIR/_hub_auth.log ]
then
	$SCRIPTPATH/xlogimport_authlog $METRICSLOGDIR/_hub_auth.log >> $METRICSLOGDIR/_cmsauth_import.err
	mv $METRICSLOGDIR/_hub_auth.log $METRICSLOGDIR/_prev_hub_auth.log
fi
