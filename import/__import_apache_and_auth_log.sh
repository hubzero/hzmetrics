#!/bin/bash 
#
# @package      hubzero-metrics
# @file         _import_apache_and_auth_log.sh
# @author       Swaroop Shivarajapura <swaroop@purdue.edu>
# @copyright    Copyright (c) 2011-2013 HUBzero Foundation, LLC.
# @license      http://www.gnu.org/licenses/lgpl-3.0.html LGPLv3
#
# Copyright (c) 2011-2013 HUBzero Foundation, LLC.
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
