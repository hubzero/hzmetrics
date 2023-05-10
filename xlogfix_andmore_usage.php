#!/usr/bin/php
<?php
# @package      hubzero-metrics
# @file         xlogfix_andmore_usage.php
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
# This script computes usage metrics for "and more" resources
#
# USAGE: ./xlogfix_andmore_usage.php <YYYY-MM>
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
require_once(__DIR__."/includes/func_andmore.php");

$db_hub = db_connect('db_hub');

if ($_SERVER['argc'] < 2) {
        $dthis_ = date('Y').'-'.date('m').'-'.date('d');
    $processed_on = date('Y').'-'.date('m').'-01';
} else {
    $dthis_ = $_SERVER['argv'][1];
    $processed_on = $dthis_.'-01';
}

$overwrite = 1;
$dbug = 0;

$periods  = array(12, 14, 1);

# --------------------------------------------------------------------------------------------
$sql = 'SELECT DISTINCT id, type FROM '.$hub_db.'.'.$db_prefix.'resources WHERE published = 1 AND standalone = 1 AND type <> 7 ORDER BY publish_up DESC';
$result = mysqli_query($db_hub, $sql);
if($result) {
    if(mysqli_num_rows($result) > 0) {
        while($row = mysqli_fetch_assoc($result)) {
            $resid = $row['id'];
            $restype = $row['type'];
            $child_resid_list = array();
            array_push($child_resid_list, $resid);
            get_child_resources($db_hub, $resid, $child_resid_list);
            $resid_list = '';
            foreach ($child_resid_list as $childid) {
                $resid_list .= '"'.$childid.'",';
            }
            $resid_list = rtrim($resid_list,",");
            $match_string = '';
            $match_string = get_paths($db_hub, $resid_list);
        
            $users = 0;
            if ($match_string) {
                foreach ($periods as $period) {
                    $dates = get_dates($dthis_, $period);
                    $dstart = $dates['start'];
                    $dstop = $dates['stop'];
            
                    if (!mysqli_ping($db_hub))
                        $db_hub = db_connect('db_hub');
                    $id_ = 0;
                    $sql = 'SELECT id FROM '.$hub_db.'.'.$db_prefix.'resource_stats WHERE resid = '.dbquote($resid).' AND datetime = '.dbquote($processed_on).' AND period = '.dbquote($period);
                    $id_  = db_fetch($db_hub, $sql);
                    if ($id_) {
                        if ($overwrite) {
                            $users = get_usage($db_hub, $match_string, $dstart, $dstop);
                            if (!mysqli_ping($db_hub))
                                $db_hub = db_connect('db_hub');
                            $sql_updt = 'UPDATE '.$hub_db.'.'.$db_prefix.'resource_stats SET users = '.dbquote($users).' WHERE id = '.dbquote($id_);
                            if ($dbug) {
                                print $sql_updt.';'."\n";
                            } else {
                                db_exec($db_hub,$sql_updt);
                            }
                        
                        }
                    } else {
                        $users = get_usage($db_hub, $match_string,  $dstart, $dstop);
                        if (!mysqli_ping($db_hub))
                            $db_hub = db_connect('db_hub');
                        $sql_ins = 'INSERT INTO '.$hub_db.'.'.$db_prefix.'resource_stats (resid, restype, users, datetime, period) VALUES ('.dbquote($resid).','.dbquote($restype).','.dbquote($users).','.dbquote($processed_on).','.dbquote($period).')';
                        if ($dbug) {
                            print $sql_ins.';'."\n";
                        } else {
                            db_exec($db_hub,$sql_ins);
                        }
                    }
                }
            }
        }
    }
} else {
    echo mysqli_error($db_hub)." while executing ".$sql."\n";
    die;
}

db_close($db_hub);

# --------------------------------------------------------------------------------------------
function get_usage($db_hub, $match_string, $dstart, $dstop) {

    global $metrics_db;

    $sql = 'SELECT COUNT(DISTINCT ip, host) FROM '.$metrics_db.'.web';
    if ($match_string) {
        $sql .= ' WHERE ('.$match_string.')';
    } else {
        print "something is horribly wrong\n\n\n";
    }
    $sql .=  ' AND datetime >= '.dbquote($dstart).' and datetime < '.dbquote($dstop);
    if (!mysqli_ping($db_hub))
    $db_hub = db_connect('db_hub');
    $result = mysqli_query($db_hub, $sql);
    if($result) {
        if(mysqli_num_rows($result) > 0) {
            while($row = mysqli_fetch_row($result)) {
                $users = $row['0'];
            }
        }
    } else {
        echo mysqli_error($db_hub)." while executing ".$sql."\n";
        die;
    }

    return $users;
}

?>
