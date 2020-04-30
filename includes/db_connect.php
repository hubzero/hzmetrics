<?php
# @package      hubzero-metrics
# @file         db_connect.php
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
function db_connect($dblink) {

	switch($dblink) {

		case 'db_hub':
			$db = mysqli_connect($GLOBALS['db_host'], $GLOBALS['db_user'], $GLOBALS['db_pass'], trim($GLOBALS['hub_db'],'`'));
			if (!$db) {
				die('Database Connection Error: ' . mysqli_connect_error() . "\n\n");
			}
			break;

		case 'db_net':
			$db = mysqli_connect($GLOBALS['db_net_host'], $GLOBALS['db_net_user'], $GLOBALS['db_net_pass'], $GLOBALS['net_db']);
			if (!$db) {
				die('Database Connection Error: ' . mysqli_connect_error() . "\n\n");
			}
			break;

		default:
			print 'Unrecognized database link '.$dblink."\n";
    		die;
	}

	return $db;

}

function db_close($db) {

	mysqli_close($db);

}

?>
