#!/usr/bin/php
<?php
# @package      hubzero-metrics
# @file         xlogfix_user_info.php
# @copyright    Copyright (c) 2016-2020 The Regents of the University of California.
# @license      http://opensource.org/licenses/MIT MIT
# @trademark    HUBzero is a registered trademark of The Regents of the University of California.
# =========================================================================
# This Script assigns countryresident, countrycitizen and orgtype to each 
# user in toolstart table
#
# USAGE: ./xlogfix_user_info.php <database-prefix> <table-name>
#

error_reporting(E_ALL & ~E_NOTICE);
@ini_set('display_errors','1');

if(!defined('__DIR__')) {
    $fPos = strrpos(__FILE__, "/");
    define("__DIR__", substr(__FILE__, 0, $fPos) . "/");
}

require_once(__DIR__."/includes/hub_parameters.php");
require_once(__DIR__."/includes/db_connect.php");
require_once(__DIR__."/includes/func_misc.php");

$db_hub = db_connect('db_hub');

if (!$_SERVER['argv'][1] || !$_SERVER['argv'][2])
{
    $msg = 'Usage: ' . $_SERVER['argv'][0] . '<database>.<table>'."\n";
    clean_exit($msg);
}

if ($_SERVER['argv'][1] == "hub") {
    $table = $hub_db.'.'.$_SERVER['argv'][2];
} else if ($_SERVER['argv'][1] == "metrics") {
    $table = $metrics_db.'.'.$_SERVER['argv'][2];
} else {
    $msg = 'Invalid database type'."\n";
    clean_exit($msg);
}

$debug = 0;
$fix_params = array('countrycitizen','countryresident','orgtype');

foreach ($fix_params as $param) {
    update_tables($db_hub, $param, $table);
}

db_close($db_hub);

function update_tables($db_hub, $param, $table) {

    global $metrics_db, $db_prefix, $debug; 
    
    if ($debug)
        print "\n".'Finding all '.$table.' records lacking '.$param.'....'."\n";

    if ($param == "countrycitizen") {
        $r_param = "countryorigin";
    } else {
        $r_param = $param;
    }
    $user_list = '';
    $queryDate = new DateTime();
    $queryDate->modify('-7 days');

    // column for datetime has different names in toolstart vs sessionlog_metrics table:
    // toolstart.datetime column:
    $datetime_col = '`datetime`';
    // sessionlog_metrics.start column:
    if (explode(".", $table)[1] == 'sessionlog_metrics') $datetime_col = '`start`';

    $sql = 'SELECT DISTINCT user FROM '.$table.' WHERE '.$datetime_col.'> "'.$queryDate->format('Y-m-d').'" AND ('.$param.' = "" OR '.$param.' IS NULL)';
    $result = mysqli_query($db_hub, $sql);
    if($result) {
        if(mysqli_num_rows($result) > 0) {
            while($row = mysqli_fetch_assoc($result)) {
                $user_list .= '"'.$row['user'].'",';
            }
        }
    } else {
        $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
        clean_exit($msg);
    }
    $user_list = rtrim($user_list,",");
    if ($user_list) {
        $sql = 'SELECT username, countryresident, countryorigin, orgtype FROM '.$metrics_db.'.'.$db_prefix.'xprofiles_metrics WHERE username IN ('.$user_list.')'; 
        $result = mysqli_query($db_hub, $sql);
        if($result) {
            if(mysqli_num_rows($result) > 0) {
                while($row = mysqli_fetch_assoc($result)) {
                    if ($row[$r_param]) {
                        $value = strtoupper($row[$r_param]);
                        $sql_updt = 'UPDATE '.$table.' SET '.$param.' = '.dbquote($value).' WHERE ('.$param.' = "" OR '.$param.' IS NULL) AND user = '.dbquote($row['username']);
                        db_exec($db_hub, $sql_updt);
                    }
                }
            }
        } else {
            $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
            clean_exit($msg);
        }
    }
}

?>
