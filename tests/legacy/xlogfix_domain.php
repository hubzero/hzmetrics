#!/usr/bin/php
<?php
# @package      hubzero-metrics
# @file         xlogfix_domain.php
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
# =========================================================================
# This script resolves domain fields from host fields in various tables
#
# USAGE: ./xlogfix_domain.php <database> <table> [YYYY-MM]
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

if ($debug) print __FILE__."\n";

if (!$_SERVER['argv'][1] || !$_SERVER['argv'][2])
{
    $msg = 'Usage: ' . $_SERVER['argv'][0] . '<database>.<table>'."\n";
    clean_exit($msg);
} else {
    $database = $_SERVER['argv'][1];
    $table = $_SERVER['argv'][2];
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

if ($database == 'hub') {
    $database = $hub_db;
} else if ($database == 'metrics') {
    $database = $metrics_db;
} else {
    $msg = 'Invalid database type'."\n";
    clean_exit($msg);
}

# set the correct datetime col for the table we are using:
if ($table == "sessionlog_metrics")
{
    $datecol = "start";
} else {
    $datecol = "datetime";
}

# run the table updates for each week in the effective month:

$calculationDates = findWeeks($yearMonth, $day);

foreach ($calculationDates as $ddate) {
    if ($debug) print_r($ddate);
    $datestart = $ddate['start'];
    $dateend = $ddate['end'];

    # Select all web records missing domain names...
    # restricting by date

    #$sql = "SELECT id, LOWER(host) FROM $database.$table WHERE datetime >= '$datestart' AND datetime < '$dateend' AND (domain = '' OR domain = '?' OR domain IS NULL) AND host <> ''";
    $sql = "SELECT id, LOWER(host) FROM $database.$table WHERE $datecol >= '$datestart' AND $datecol < '$dateend' AND (domain = '' OR domain = '?' OR domain IS NULL) AND host <> ''";

    if ($debug) print($sql."\n");
    $result = mysqli_query($db_hub, $sql);
    if($result) {
        if ($debug) print "  row count: ".mysqli_num_rows($result)."\n \n";
        if(mysqli_num_rows($result) > 0) {
            while($row = mysqli_fetch_row($result)) {
                $id = $row[0];
                $host = $row[1];
                #  Update table  record...
                $sql_updt = 'UPDATE '.$database.'.'.$table.' SET domain = '.dbquote(get_domain($host)).' WHERE id = '.dbquote($id);
                db_exec($db_hub, $sql_updt);
            }
        }
    } else {
        $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
        clean_exit($msg);
    }
}

db_close($db_hub);

function get_domain($hostname) 
{
    $host = $hostname;

    $no2_3level["ub"] = 1;
    $mil_3level["af"] = 1;
    $mil_3level["army"] = 1;
    $mil_3level["navy"] = 1;
    $int_3level["com"] = 1;
    $int_3level["net"] = 1;
    $int_3level["org"] = 1;
    $int_3level["edu"] = 1;
    $int_3level["gov"] = 1;
    $int_3level["mil"] = 1;
    $int_3level["ac"] = 1;
    $int_3level["co"] = 1;
    $int_3level["ne"] = 1;
    $int_3level["or"] = 1;
    $int_3level["ed"] = 1;
    $us_4level["k12"] = 1;
    $us_4level["lib"] = 1;
    $us_4level["cc"] = 1;
    $us_4level["tec"] = 1;
    
    # process if host not null and contains ".":
    if (!is_null($host) && str_contains($host,'.')) 
    {
        $field = array_reverse(explode(".", $host));
    }

    if (isset($field[0]))
    {
        $domain = $field[0];
        
        if (isset($field[1]))
        {
            $domain = $field[1] . "." . $field[0];
            
            if (isset($field[2]))
            {
                if (!isset($no2_3level[$field[1]]) && strlen($field[1]) == 2 && strlen($field[0]) == 2
                    || isset($int_3level[$field[1]]) && strlen($field[0]) == 2
                    || isset($mil_3level[$field[1]]) && $field[0] == "mil") 
                {
                    $domain = $field[2] . "." . $field[1] . "." . $field[0];
                }
                
                if (isset($field[3]))
                {
                    if (isset($us_4level[$field[2]]) && $field[0] == "us") 
                    {
                        $domain = $field[3] . "." . $field[2] . "." . $field[1] . "." . $field[0];
                    }       
                }
            }
            elseif (preg_match('/^(.+\-.+\-.+\-.+)\-(.+)$/', $field[1]))
            {
                if (preg_match('/^(.+\-.+\-.+\-.+)\-(.+)$/', $field[1], $matches))
                {
                    $field[2] = $matches[1];
                    $field[1] = $matches[2];
                }
                $domain = $field[1] . "." . $field[0];
            }
            elseif (preg_match('/^(.+\_.+\_.+\_.+)\-(.+)$/', $field[1]))
            {
                if (preg_match('/^(.+\_.+\_.+\_.+)\-(.+)$/', $field[1], $matches))
                {
                    $field[2] = $matches[1];
                    $field[1] = $matches[2];
                }
                $domain = $field[1] . "." . $field[0];
            }
            elseif(preg_match('/^(.+\_.+\_.+\_.+)\_(.+)$/', $field[1]))
            {
                if (preg_match('/^(.+\_.+\_.+\_.+)\_(.+)$/', $field[1], $matches))
                {
                    $field[2] = $matches[1];
                    $field[1] = $matches[2];
                }
                $domain = $field[1] . "." . $field[0];
            }
            elseif(preg_match('/^(.+\-.+\-.+\-.+)\_(.+)$/', $field[1]))
            {
                if (preg_match('/^(.+\-.+\-.+\-.+)\_(.+)$/', $field[1], $matches))
                {
                    $field[2] = $matches[1];
                    $field[1] = $matches[2];
                }
                $domain = $field[1] . "." . $field[0];
            }
        }
    }

    if (!isset($domain) || $domain == ".") 
    {
        $domain = "?";
    }

    return($domain);
}

?>
