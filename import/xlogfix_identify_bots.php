#!/usr/bin/php
<?php
# @package      hubzero-metrics
# @file         xlogfix_identify_bots.php
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
# This Script reads the apache log and populates the bot_useragents table
#
# USAGE: ./xlogfix_identify_bots.php <filename>
#

error_reporting(E_ALL & ~E_NOTICE);
@ini_set('display_errors','1');

if(!defined('__DIR__')) {
    $fPos = strrpos(__FILE__, "/");
    define("__DIR__", substr(__FILE__, 0, $fPos) . "/");
}

require_once(__DIR__."/../includes/hub_parameters.php");
require_once(__DIR__."/../includes/db_connect.php");
require_once(__DIR__."/../includes/func_misc.php");

$db_hub = db_connect('db_hub');

$filehandle = fopen($_SERVER['argv'][1], "r");

if (!$filehandle) {
    $msg = 'Error opening file: '.$_SERVER['argv'][1]."\n";
    clean_exit($msg);
}

$unrec = '';

$filters = array("feedfetcher","msnbot","gsa-crawler","googlebot","yandex","spider","bot","search","crawl","archive","harvest","slurp","feed","nutch","robot","fetch","findlinks");

$log_pattern_old = '/^(\d{4}-\d{2}-\d{2})\s+(\d+:\d{2}:\d{2})\s+([\w\-\d]+)\s+(\S+)\s+\"(.+)\"\s+([\-\d]+)\s+([\d]+)\s+([\w\-\.\d]+)\s+\"(.*)\"\s+\"(.*)\"\s+([\w\-\.\d]+)\s+([\w\-\d]+)\s+([\w\-\d]+)\s+(.*)$/';

$log_pattern_new = '/^(\d{4}-\d{2}-\d{2})\s+(\d+:\d{2}:\d{2})\s+([\w\-\d]+)\s+([\d]+)\s+(\S+)\s+\"(.+)\"\s+([\-\d]+)\s+([\d]+)\s+([\w\-\.\d]+)\s+\"(.*)\"\s+\"(.*)\"\s+([\w\-\.\d]+)\s+([\w\-\d]+)\s+([\w\-\d]+)\s+([\-\d]+)\s+([^_].*)\s+([^_].*)\s+([^_].*)\s+([^_].*)\s+([^_].*)\s+([^_].*)\s+([^_].*)\s+([^_].*)\s*$/';

$useragent_strings = array();
$cnt = 1;
while(1)
{
    $line = fgets($filehandle);

    if (feof($filehandle))
        break;

    if (preg_match($log_pattern_new, $line, $matches)) {

        $datestamp = $matches[1];
        $timestamp = $matches[2];
        $timezone  = $matches[3];
        $pid       = $matches[4];
        $user      = $matches[5];
        $firstline = $matches[6];
        $return    = $matches[7];
        $bytes     = $matches[8];
        $ip        = $matches[9];
        $referrer  = $matches[10];
        $useragent = $matches[11];
        $sslport   = $matches[12];
        $ts        = $matches[13];
        $tms       = $matches[14];
        $uidNumber = $matches[15];
        $joomla_id = $matches[16];
        $st_cookie = $matches[17];
        $auth_type = $matches[18];
        $comp_name = $matches[19];
        $view_name = $matches[20];
        $task_name = $matches[21];
        $actn_name = $matches[22];
        $item_name = $matches[23];

    } else if (preg_match($log_pattern_old, $line, $matches)) {

        $datestamp = $matches[1];
        $timestamp = $matches[2];
        $timezone  = $matches[3];
        $pid       = '';
        $user      = $matches[4];
        $firstline = $matches[5];
        $return    = $matches[6];
        $bytes     = $matches[7];
        $ip        = $matches[8];
        $referrer  = $matches[9];
        $useragent = $matches[10];
        $sslport   = $matches[11];
        $ts        = $matches[12];
        $tms       = $matches[13];
        $uidNumber = '';
        $joomla_id = '';
        $st_cookie = $matches[14];
        $auth_type = '';
        $comp_name = '';
        $view_name = '';
        $task_name = '';
        $actn_name = '';
        $item_name = '';

    } else {

        $unrec .= 'Unrecognized log format: '.$line;
        continue;

    }

    if ($useragent) {
        array_push($useragent_strings, $useragent);
        $cnt++;
    }
    if ($cnt > 1000) {
        $cnt = 1;
        $useragent_strings = array_unique($useragent_strings);
    }
}


$useragent_strings = array_unique($useragent_strings);
foreach($useragent_strings as $agent) {
    foreach ($filters as $filter) {
        if (stripos($agent, $filter) !== false) {
            $sql_ins = 'INSERT IGNORE INTO '.$metrics_db.'.bot_useragents (useragent) VALUES ('.dbquote($agent).')';
            db_exec($db_hub, $sql_ins);
        }
    }
}

$sql = 'DELETE FROM '.$metrics_db.'.bot_useragents WHERE (useragent LIKE "%searchtool%" OR useragent LIKE "% feed/%")';
db_exec($db_hub, $sql);

if ($unrec)
    print $unrec;

fclose($filehandle);
db_close($db_hub);

?>
