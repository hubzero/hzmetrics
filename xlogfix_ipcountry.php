#!/usr/bin/php
<?php
# @package      hubzero-metrics
# @file         xlogfix_ipcountry.php
# @copyright    Copyright (c) 2016-2020 The Regents of the University of California.
# @license      http://opensource.org/licenses/MIT MIT
# @trademark    HUBzero is a registered trademark of The Regents of the University of California.

# ------------------------------------------------------------------------- 
# This script assigns assigns a country given an IP address and stores this
# information in sessionlog_metrics, web, toolstart, or websessions tables.
#
# If a YYYY-MM date argument is passed, this computation is run for each week
# in the specified month; if not, for each week in the month up to the current day.
#
# USAGE: ./xlogfix_ipcountry.php <database> <table> [<YYYY-MM>]
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
$debug = 0;

if (!$_SERVER['argv'][1] || !$_SERVER['argv'][2])
{
    $msg = "Usage: " . $_SERVER['argv'][0] . " <database> <table> [YYYY-MM]\n";
    clean_exit($msg);
} else {
    $database = $_SERVER['argv'][1];
    $table = $_SERVER['argv'][2];
}

if ($database == 'hub') {
    $database = $hub_db;
} else if ($database == 'metrics') {
    $database = $metrics_db;
} else {
    $msg = 'Invalid database type'."\n";
    clean_exit($msg);
}

// column for datetime has different names in sessionlog_metrics vs other tables:
// toolstart, web, websessions use `datetime`:
$datetime_col = '`datetime`';
// sessionlog_metrics.start column:
if ($table == 'sessionlog_metrics') $datetime_col = '`start`';

// determine the month and end-date to use
// if no date passed, use current date:
if ($_SERVER['argc'] < 4) {
    // year and month
    $yearMonth = date('Y').'-'.date('m');
    // end date
    $day = date('d');
} else {
    // use YYYY-MM argument passed to script
    $yearMonth = $_SERVER['argv'][3];
    $day = NULL;
}
if ($debug) print("Effective computation month is: ".$yearMonth."\n");
if ($debug) print("Effective computation day is: ".$day."\n");


// run the table updates for each week in the effective month:

$calculationDates = findWeeks($yearMonth, $day);

foreach ($calculationDates as $ddate) {
    if ($debug) print_r($ddate);
    update_tables($db_hub, $database, $table, $datetime_col, $ddate['start'], $ddate['end']); 
}
db_close($db_hub);

# ------------------------

function update_tables($db_hub, $database, $table, $datetime_col, $dateStart, $dateEnd) {

    global $metrics_db, $db_prefix, $hub_db, $debug;

    // Identify ip addresses with no associated ipcountry. Limit query to the current effective week.
    // IP is returned in dotted quad format
    $sql = 'SELECT DISTINCT(ip) AS n_ip, COUNT(*) AS hits FROM '.$database.'.'.$table.' WHERE '.$datetime_col.'> "'.$dateStart.'" AND '.$datetime_col.'<= "'.$dateEnd.'" AND (ipcountry = "" OR ipcountry IS NULL) GROUP BY n_ip ORDER by hits desc;';
    if ($debug) print("sql: ".$sql."\n");
    $result = mysqli_query($db_hub, $sql);

    $hubzero_ipgeo_url="http://hubzero.org/ipinfo/v1";
    $hub_key="_HUBZERO_OPNSRC_V1_";

    // Update table records with location data:
    if($result) {

        if ($debug) print("resultset: ".mysqli_num_rows($result)."\n");
        if(mysqli_num_rows($result) > 0) {

            while($row = mysqli_fetch_assoc($result)) {
                $country = '';
                $n_ip = $row['n_ip'];

                // IP is provided in dotted quad format
                $ip_geodata = get_ip_geodata($hubzero_ipgeo_url, $hub_key, $n_ip);
                if ($debug) print_r($ip_geodata);

                // If location data returned, update table with this information:
                if($ip_geodata['countrySHORT'] <> '' && $ip_geodata['countrySHORT'] <> '-') {
                    // IP address is specified in dotted quad format
                    $sql_updt = 'UPDATE '.$database.'.'.$table.' SET ipcountry= '.dbquote($ip_geodata['countrySHORT']).' WHERE ip = '.dbquote($n_ip).' AND (ipcountry = "" OR ipcountry IS NULL)';
                    if ($debug) print("sql_updt: ".$sql_updt."\n");
                    db_exec($db_hub, $sql_updt);
                }
            }
        }
    } else {
        $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
        clean_exit($msg);
    }
}
