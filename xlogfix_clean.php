#!/usr/bin/php
<?php
# @package      hubzero-metrics
# @file         xlogfix_clean.php
# @copyright    Copyright (c) 2016-2020 The Regents of the University of California.
# @license      http://opensource.org/licenses/MIT MIT
# @trademark    HUBzero is a registered trademark of The Regents of the University of California.

# ------------------------------------------------------------------------- 
# This script clears the web and websessions tables of identified bots 
# for the indicated month and year.
#
# USAGE: ./xlogfix_clean.php <tablename> [YYYY-MM]
# <tablename> is 'web' or 'websessions'
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
$debug = 1;

if (!$_SERVER['argv'][1]) {
    $msg = "USAGE: " . $_SERVER['argv'][0] . " <tablename> [YYYY-MM]\n";
    clean_exit($msg);
} else {
    $table = $_SERVER['argv'][1];
}

// determine the month and end-date for processing.
// if no date passed, use current date:
if ($_SERVER['argc'] < 3) {
    // year and month
    $yearMonth = date('Y').'-'.date('m');
    // end date
    $day = date('d');
} else {
    // use YYYY-MM argument passed to script
    $yearMonth = $_SERVER['argv'][2];
    $day = NULL;
}

if ($debug) print("Effective computation month is: ".$yearMonth."\n");
if ($debug) print("Effective computation day is: ".$day."\n");

// clearing 'domain' type bots:

$sql = 'SELECT DISTINCT filter FROM '.$metrics_db.'.exclude_list WHERE type = "domain"';
$result = mysqli_query($db_hub, $sql);
if($result) {
    if(mysqli_num_rows($result) > 0) {
        while($row = mysqli_fetch_row($result)) {
            $sql_del = 'DELETE FROM '.$metrics_db.'.'.$table.' WHERE domain = '.dbquote($row[0]);

            if ($debug) print("$sql_del\n");
            db_exec($db_hub, $sql_del);
        }
    }
} else {
    $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
    clean_exit($msg);
}

// clearing 'host' type bots:

// run the processing for each week in the effective month:
$calculationDates = findWeeks($yearMonth, $day);
if ($debug) print("Effective computation dates: \n");
if ($debug) print_r($calculationDates);

$sql = 'SELECT DISTINCT filter FROM '.$metrics_db.'.exclude_list WHERE type = "host"';
$result = mysqli_query($db_hub, $sql);
if($result) {
    if(mysqli_num_rows($result) > 0) {
        while($row = mysqli_fetch_row($result)) {
            clear_bot_hosts($db_hub, $row, $calculationDates);
        }
    }
} else {
    $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
    clean_exit($msg);
}

# ------------------------

function clear_bot_hosts($db_hub, $row, $dates) {

    global $metrics_db, $table, $debug;

    foreach ($dates as $ddate) {

        $start = $ddate['start'];
        $end = $ddate['end'];

        $sql = "DELETE FROM $metrics_db.$table WHERE datetime > '$start' AND datetime <= '$end' AND host LIKE ".dbquote($row[0]);

        if ($debug) print("$sql\n");
        db_exec($db_hub, $sql);
    }
}

db_close($db_hub);

?>
