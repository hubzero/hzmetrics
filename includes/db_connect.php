<?php

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
