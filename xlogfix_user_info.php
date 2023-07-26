#!/usr/bin/php
<?php
# @package      hubzero-metrics
# @file         xlogfix_user_info.php
# @copyright    Copyright (c) 2016-2020 The Regents of the University of California.
# @license      http://opensource.org/licenses/MIT MIT
# @trademark    HUBzero is a registered trademark of The Regents of the University of California.
# =========================================================================
# This Script assigns countryresident, countrycitizen and orgtype to each 
# user in toolstart and sessionlog_metrics tables, where possible.
#
# USAGE: ./xlogfix_user_info.php <database-prefix> <table-name> [<YYYY-MM>]
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
}

if ($_SERVER['argv'][1] == "hub") {
    $table = $hub_db.'.'.$_SERVER['argv'][2];
} else if ($_SERVER['argv'][1] == "metrics") {
    $table = $metrics_db.'.'.$_SERVER['argv'][2];
} else {
    $msg = 'Invalid database type'."\n";
    clean_exit($msg);
}

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

$fix_params = array('countrycitizen','countryresident','orgtype');

// run the table updates for each parameter
foreach ($fix_params as $param) {
    // compute start and end times for each week, then run update_tables call for that week
    $calculationDates = findWeeks($yearMonth, $day);

    foreach ($calculationDates as $ddate) {
        if ($debug) print("Effective computation dates: \n");
        if ($debug) print_r($ddate);
        update_tables($db_hub, $param, $table, $ddate['start'], $ddate['end']);
    }
}

db_close($db_hub);

function update_tables($db_hub, $param, $table, $ddateStart, $ddateEnd) {

    global $metrics_db, $db_prefix, $hub_db, $debug;
    
    if ($debug) print("\n Finding all ".$table." records lacking ".$param."... \n");

    // r_param is the param name from the database
    if ($param == "countrycitizen") {
        $r_param = "countryorigin";
    } else {
        $r_param = $param;
    }
    if ($debug) print "set r_param:". $r_param."\n";

    $user_list = '';

    // column for datetime has different names in toolstart vs sessionlog_metrics table:
    // toolstart.datetime column:
    $datetime_col = '`datetime`';
    // sessionlog_metrics.start column:
    if (explode(".", $table)[1] == 'sessionlog_metrics') $datetime_col = '`start`';

    // Obtain list of user_ids that are missing params in the target table:
    $sql = 'SELECT id from '.$hub_db.'.'.$db_prefix.'users where username in (SELECT DISTINCT user FROM '.$table.' WHERE '.$datetime_col.'> "'.$ddateStart.'" AND '.$datetime_col.'<= "'.$ddateEnd.'" AND ('.$param.' = "" OR '.$param.' IS NULL))';
    if ($debug) print("sql: ".$sql."\n");
    $result = mysqli_query($db_hub, $sql);
    if($result) {
        if(mysqli_num_rows($result) > 0) {
            if ($debug) print("Found ". mysqli_num_rows($result)." users without ".$param."\n");
            while($row = mysqli_fetch_assoc($result)) {
                $user_list .= $row['id'].',';
            }
            if ($debug) print($user_list."\n");
        }
    } else {
        $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
        clean_exit($msg);
    }
    $user_list = rtrim($user_list,",");

    // select from the user profile table, jos_user_profiles
    if ($user_list) {
        $sql = "SELECT up.id, u.username, up.profile_value FROM ".$hub_db.".".$db_prefix."users u JOIN ".$hub_db.".".$db_prefix."user_profiles up ON u.id=up.user_id WHERE up.profile_key='".$r_param."'";
        if ($debug) print("sql: ".$sql."\n");
        $result = mysqli_query($db_hub, $sql);
        if($result) {
            if(mysqli_num_rows($result) > 0) {
                while($row = mysqli_fetch_assoc($result)) {
                    if ($row['profile_value']) {
                        $value = strtoupper($row['profile_value']);
                        $sql_updt = 'UPDATE '.$table.' SET '.$param.' = '.dbquote($value).' WHERE ('.$param.' = "" OR '.$param.' IS NULL) AND user = '.dbquote($row['username']);
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
}


/*
 * findWeeks()
 *
 * input:
 *   yearMonthStr YYYY-MM string indicating effective month for calculation
 *   endDayStr    dd      string indicating end day for calculation, if any
 *
 * output: array of strings indicating start/end dates for approx. 7 day 'weeks'
*/
function findWeeks($yearMonthStr, $endDayStr = NULL)
{
    global $debug;

    // Convert the yearMonth string to a DateTime object
    $yearMonth = DateTime::createFromFormat('Y-m', $yearMonthStr);

    if (!$yearMonth ) {
        return "Invalid input format. Please use YYYY-MM format for yearMonthStr.";
    }

    // Set the timezone to match your desired timezone if needed
    // $yearMonth->setTimezone(new DateTimeZone('Your/Timezone'));

    // Get the first day and last day of the month
    $firstDay = clone $yearMonth;
    $firstDay->modify('first day of this month')->modify('-1 day'); // Day before the start of the month

    // Set last day of calculation (or month) depending on whether $endDayStr is set
    if ( is_null($endDayStr) ) {
        $lastDay = clone $yearMonth;
        $lastDay->modify('last day of this month')->modify('+1 day'); // Day after the end of the month
    } else {
        $dateStr = $yearMonthStr.'-'.$endDayStr;
        $lastDay = DateTime::createFromFormat('Y-m-d', $dateStr);
    }

    // Initialize an array to store the periods
    $periods = array();

    // Start the first period from the day before the start of the month
    $periodStart = clone $firstDay;

    // Calculate approximate 7-day periods
    while ($periodStart < $lastDay) {
        $periodEnd = clone $periodStart;
        $periodEnd->modify('+7 days');

        // Adjust the calculated period end
        if ($periodEnd >= $lastDay) {
            $periodEnd = clone $lastDay;
        }

        // Add the current period to the array
        $periods[] = array(
            'start' => $periodStart->format('Y-m-d'),
            'end' => $periodEnd->format('Y-m-d')
        );

        // Move to the next period
        $periodStart->modify('+7 day');
    }

    return $periods;
}

?>
