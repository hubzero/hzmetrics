#!/bin/bash
#
# @package      hubzero-metrics
# @file         _archive_apache_and_auth_log.sh
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

if [ -f /etc/hubzero.conf ]
then
  site=$(grep site= /etc/hubzero.conf | sed 's/site=//')
else
  site=hub
fi

# -----------------------------------------------------------------------------------------------
# Archiving apache and CMS logs
# -----------------------------------------------------------------------------------------------
files=$(ls /var/log/apache2/daily/$site-access*log* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	gzip --quiet /var/log/apache2/daily/$site-access*log*
	mv --backup=numbered /var/log/apache2/daily/$site-access*log* /var/log/apache2/imported/
fi

files=$(ls /var/log/apache2/daily/new-$site-access*log* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	gzip --quiet /var/log/apache2/daily/new-$site-access*log*
	mv --backup=numbered /var/log/apache2/daily/new-$site-access*log* /var/log/apache2/imported/
fi

files=$(ls ${CMSLOGDIR}/daily/${CMSLOGPREFIX}cmsauth*log* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	gzip --quiet ${CMSLOGDIR}/daily/${CMSLOGPREFIX}cmsauth*log*
	mv --backup=numbered ${CMSLOGDIR}/daily/${CMSLOGPREFIX}cmsauth*log* ${CMSLOGDIR}/imported/
fi

files=$(ls ${CMSLOGDIR}/daily/${CMSLOGPREFIX}cmsdebug*log* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	gzip --quiet ${CMSLOGDIR}/daily/${CMSLOGPREFIX}cmsdebug*log*
	mv --backup=numbered ${CMSLOGDIR}/daily/${CMSLOGPREFIX}cmsdebug*log* ${CMSLOGDIR}/imported/
fi
