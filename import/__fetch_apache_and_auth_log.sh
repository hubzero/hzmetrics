#!/bin/bash 
#
# @package      hubzero-metrics
# @file         _fetch_apache_and_auth_log.sh
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
CMSLOGDIR=/var/log/hubzero
CMSLOGPREFIX=
METRICSLOGDIR=/var/log/hubzero/metrics

if [ ! -d $METRICSLOGDIR ]
then
  mkdir -p $METRICSLOGDIR
fi

if [ -f /etc/hubzero.conf ]
then
  site=$(grep site= /etc/hubzero.conf | sed 's/site= //')
else
  site=hub
fi

if [ -d "/etc/httpd" ]; then
  APACHELOGDIR=/var/log/httpd
fi

if [ -d "/etc/apache2" ]; then
  APACHELOGDIR=/var/log/apache2
fi


# -----------------------------------------------------------------------------------------------
# Fetching apache and CMS logs
# -----------------------------------------------------------------------------------------------
files=$(ls ${APACHELOGDIR}/daily/"$site"-access*log* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	cat ${APACHELOGDIR}/daily/$site-access*log* > $METRICSLOGDIR/_hub_apache.log
fi

files=$(ls ${CMSLOGDIR}/daily/${CMSLOGPREFIX}cmsauth*log* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	cat ${CMSLOGDIR}/daily/${CMSLOGPREFIX}cmsauth*log* > $METRICSLOGDIR/_hub_auth.log
fi
