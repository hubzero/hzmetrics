#!/bin/bash 
#
# @package      hubzero-metrics
# @file         _archive_apache_and_auth_log.sh
# @author       Swaroop Shivarajapura <swaroop@purdue.edu>
# @copyright    Copyright (c) 2011-2012 HUBzero Foundation, LLC.
# @license      http://www.gnu.org/licenses/lgpl-3.0.html LGPLv3
#
# Copyright (c) 2011-2012 HUBzero Foundation, LLC.
#
# This file is part of: The HUBzero(R) Platform for Scientific Collaboration
#
# The HUBzero(R) Platform for Scientific Collaboration (HUBzero) is free
# software: you can redistribute it and/or modify it under the terms of
# the GNU Lesser General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# HUBzero is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# HUBzero is a registered trademark of HUBzero Foundation, LLC.
#

SCRIPT=`readlink -f $0`
SCRIPTPATH=`dirname $SCRIPT`

if [ -f /etc/hubzero.conf ]
then
  site=$(grep site= /etc/hubzero.conf | sed 's/site=//')
else
  site=hub
fi

# -----------------------------------------------------------------------------------------------
# Archiving apache and CMS logs
# -----------------------------------------------------------------------------------------------
files=$(ls /var/log/apache2/daily/$site-access.log-* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	mv --backup=numbered /var/log/apache2/daily/$site-access.log-* /var/log/apache2/imported/
fi

files=$(ls /var/log/hubzero/daily/cmsauth.log-* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	mv --backup=numbered /var/log/hubzero/daily/cmsauth.log-* /var/log/hubzero/imported/
fi
