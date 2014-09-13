<?php
# @package      hubzero-metrics
# @file         db_connect.php
# @author       Swaroop Shivarajapura <swaroop@purdue.edu>
# @copyright    Copyright (c) 2011-2014 HUBzero Foundation, LLC.
# @license      http://www.gnu.org/licenses/lgpl-3.0.html LGPLv3
#
# Copyright (c) 2011-2014 HUBzero Foundation, LLC.
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
function db_connect($dblink) {

	switch($dblink) {

		case 'db_hub':
			$db = mysql_connect($GLOBALS['db_host'], $GLOBALS['db_user'], $GLOBALS['db_pass'], true);
			if (!$db) {
				die('Database Connection Error: '.mysql_error().n.n);
			}
			break;

		case 'db_net':
			$db = mysql_connect($GLOBALS['db_net_host'], $GLOBALS['db_net_user'], $GLOBALS['db_net_pass'], true);
			if (!$db) {
				die('Database Connection Error: '.mysql_error().n.n);
			}
			break;

		default:
			print 'Unrecognized database link '.$dblink.n;
    		die;
	}

	return $db;

}

function db_close($db) {

	mysql_close($db);

}

?>
