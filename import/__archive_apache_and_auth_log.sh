#!/bin/bash
#
# @package      hubzero-metrics
# @file         _archive_apache_and_auth_log.sh
# @copyright    Copyright (c) 2011-2020 The Regents of the University of California.
# @license      http://opensource.org/licenses/MIT MIT
#
# Copyright (c) 2011-2020 The Regents of the University of California.
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
CMSLOGDIR=/var/log/hubzero
CMSLOGPREFIX=

if [ -f /etc/hubzero.conf ]
then
  site=$(grep -E "site\s*=" /etc/hubzero.conf | sed 's/site[ ]*=[ ]*//')
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
# Archiving apache and CMS logs
# -----------------------------------------------------------------------------------------------
files=$(ls ${APACHELOGDIR}/daily/$site-access*log* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	gzip --quiet ${APACHELOGDIR}/daily/$site-access*log*
	mv --backup=numbered ${APACHELOGDIR}/daily/$site-access*log* ${APACHELOGDIR}/imported/
fi

files=$(ls ${APACHELOGDIR}/daily/new-$site-access*log* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	gzip --quiet ${APACHELOGDIR}/daily/new-$site-access*log*
	mv --backup=numbered ${APACHELOGDIR}/daily/new-$site-access*log* ${APACHELOGDIR}/imported/
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
