#!/usr/bin/php
<?php
# @package      hubzero-metrics
# @file         gen_tool_toplists.php
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
# This script computes the simulation tool stats for the current month
#
# USAGE: ./gen_tool_toplists.php [<YYYY-MM>]
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

if ($_SERVER['argc'] < 2) {
    $dthis_ = date('Y').'-'.date('m').'-'.date('d');
} else {
    $dthis_ = $_SERVER['argv'][1];
}

$tops  = array(2, 5, 6, 7, 8);
foreach ($tops as $top) {
    compute_data($db_hub, $dthis_, $top);
}

db_close($db_hub);

function compute_data(&$db_hub, $dthis_, $top) {

    global $hub_db, $metrics_db, $db_prefix;

    $periods  = array(0, 1, 3, 12, 13, 14);

    foreach ($periods as $period) {

        $dates = get_dates($dthis_, $period);
        $period = dbquote($period);
        $dstart = dbquote($dates['start']);
        $dstop = dbquote($dates['stop']);
        $dthis = dbquote($dates['dthis']);

        $sql = 'DELETE FROM '.$hub_db.'.'.$db_prefix.'stats_topvals WHERE top = '.dbquote($top).' AND datetime = '.$dthis.' AND period = '.$period;
        db_exec($db_hub, $sql);

        if($top == '2') { // Top Tools by Simulation Users

            $top_type = 'Total Simulation Users';
            $sql = 'SELECT COUNT(DISTINCT user) FROM '.$metrics_db.'.sessionlog_metrics WHERE start > '.$dstart.' AND start < '.$dstop;
            $total = db_fetch($db_hub, $sql);
            $sql_ins = 'INSERT INTO '.$hub_db.'.'.$db_prefix.'stats_topvals VALUES (NULL,'.dbquote($top).','.$dthis.','.$period.',"0",'.dbquote($top_type).','.dbquote($total).')';
            db_exec($db_hub, $sql_ins);

            $sql = 'SELECT res.title, rt.resid, rt.users AS cnt FROM '.$hub_db.'.'.$db_prefix.'resource_stats_tools AS rt, '.$hub_db.'.'.$db_prefix.'resources AS res WHERE res.id = rt.resid AND res.published = 1 AND period = '.$period.' and datetime = '.$dthis.' ORDER BY cnt DESC';
            gen_top_tools($db_hub, $sql, $top, $dthis, $period);

        } else if ($top == '5') { // Top Tools by Sim Runs
    
            $top_type = "Total Simulation Runs";
            $sql = 'SELECT SUM(jobs) FROM '.$hub_db.'.'.$db_prefix.'resource_stats_tools WHERE period = '.$period.' AND datetime = '.$dthis;
            $total = db_fetch($db_hub, $sql);
            $sql_ins = 'INSERT INTO '.$hub_db.'.'.$db_prefix.'stats_topvals VALUES (NULL,'.dbquote($top).','.$dthis.','.$period.',"0",'.dbquote($top_type).','.dbquote($total).')';
            db_exec($db_hub, $sql_ins);

            $sql = 'SELECT res.title, rt.resid, rt.jobs AS cnt FROM '.$hub_db.'.'.$db_prefix.'resource_stats_tools AS rt, '.$hub_db.'.'.$db_prefix.'resources AS res WHERE res.id = rt.resid AND res.published = 1 AND period = '.$period.' and datetime = '.$dthis.' ORDER BY cnt DESC';
            gen_top_tools($db_hub, $sql, $top, $dthis, $period);

        } else if ($top == '6') { // Top Tools by Sim Wall Time
    
            $top_type = "Total Simulation Wall Time";
            $sql = 'SELECT SUM(tot_wall) FROM '.$hub_db.'.'.$db_prefix.'resource_stats_tools WHERE period = '.$period.' AND datetime = '.$dthis;
            $total = db_fetch($db_hub, $sql);
            $sql_ins = 'INSERT INTO '.$hub_db.'.'.$db_prefix.'stats_topvals VALUES (NULL,'.dbquote($top).','.$dthis.','.$period.',"0",'.dbquote($top_type).','.dbquote($total).')';
            db_exec($db_hub, $sql_ins);

            $sql = 'SELECT res.title, rt.resid, rt.tot_wall AS cnt FROM '.$hub_db.'.'.$db_prefix.'resource_stats_tools AS rt, '.$hub_db.'.'.$db_prefix.'resources AS res WHERE res.id = rt.resid AND res.published = 1 AND period = '.$period.' and datetime = '.$dthis.' ORDER BY cnt DESC';
            gen_top_tools($db_hub, $sql, $top, $dthis, $period);

        } else if ($top == '7') { // Top Tools by Sim CPU Time
    
            $top_type = "Total Simulation CPU Time";
            $sql = 'SELECT SUM(tot_cpu) FROM '.$hub_db.'.'.$db_prefix.'resource_stats_tools WHERE period = '.$period.' AND datetime = '.$dthis;
            $total = db_fetch($db_hub, $sql);
            $sql_ins = 'INSERT INTO '.$hub_db.'.'.$db_prefix.'stats_topvals VALUES (NULL,'.dbquote($top).','.$dthis.','.$period.',"0",'.dbquote($top_type).','.dbquote($total).')';
            db_exec($db_hub, $sql_ins);

            $sql = 'SELECT res.title, rt.resid, rt.tot_cpu AS cnt FROM '.$hub_db.'.'.$db_prefix.'resource_stats_tools AS rt, '.$hub_db.'.'.$db_prefix.'resources AS res WHERE res.id = rt.resid AND res.published = 1 AND period = '.$period.' and datetime = '.$dthis.' ORDER BY cnt DESC';
            gen_top_tools($db_hub, $sql, $top, $dthis, $period);

        } else if ($top == '8') { // Top Tools by Sim Interactive Time
    
            $top_type = "Total Simulation Interaction Time";
            $sql = 'SELECT SUM(tot_view) FROM '.$hub_db.'.'.$db_prefix.'resource_stats_tools WHERE period = '.$period.' AND datetime = '.$dthis;
            $total = db_fetch($db_hub, $sql);
            $sql_ins = 'INSERT INTO '.$hub_db.'.'.$db_prefix.'stats_topvals VALUES (NULL,'.dbquote($top).','.$dthis.','.$period.',"0",'.dbquote($top_type).','.dbquote($total).')';
            db_exec($db_hub, $sql_ins);

            $sql = 'SELECT res.title, rt.resid, rt.tot_view AS cnt FROM '.$hub_db.'.'.$db_prefix.'resource_stats_tools AS rt, '.$hub_db.'.'.$db_prefix.'resources AS res WHERE res.id = rt.resid AND res.published = 1 AND period = '.$period.' and datetime = '.$dthis.' ORDER BY cnt DESC';
            gen_top_tools($db_hub, $sql, $top, $dthis, $period);

        } else {
            $msg = 'No Tops to compute'."\n";
            clean_exit($msg);
        }
    }
}

function gen_top_tools($db_hub, $sql, $top, $dthis, $period) {

    global $hub_db, $db_prefix;

    $rank = 1;
    $result = mysqli_query($db_hub, $sql);
    if($result) {
        if(mysqli_num_rows($result) > 0) {
            while($row = mysqli_fetch_assoc($result)) {
                $name = $row['resid']." ~ ".$row['title'];
                $sql_ins = 'INSERT INTO '.$hub_db.'.'.$db_prefix.'stats_topvals VALUES (NULL,'.dbquote($top).','.$dthis.','.$period.','.dbquote($rank).','.dbquote($name).','.dbquote($row['cnt']).')';
                db_exec($db_hub, $sql_ins);
                $rank = $rank + 1;
            }
        }
    } else {
        $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
        clean_exit($msg);
    }
}

?>
