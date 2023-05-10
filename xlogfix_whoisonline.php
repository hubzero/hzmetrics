#!/usr/bin/php
<?php
# @package      hubzero-metrics
# @file         xlogfix_whoisonline.php
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
# ------------------------------------------------------------------------- 
# This script generates data shown in the whoisonline maps for the HUB.
# - Updates hostname, domain name and IP Latitude & Longitute information 
#   for IPs in hub_metrics.user_session (logged in users)
# - Creates a file <hub_site_root_dir>/site/stats/maps/whoisonline.xml which is
#   read by google maps
#
# USAGE: xlogfix_whoisonline.php (executed as a cron every 5 minutes)
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

$idle_time = 3600;

$session_table = $hub_db.'.'.$db_prefix.'session_geo';

if (is_dir($hub_dir . '/app')) {
    $mapDir = $hub_dir.'/app/site/stats/maps';
}
else {
    $mapDir = $hub_dir.'/site/stats/maps';
}

$xmlFile = $mapDir."/whoisonline.xml";
if (!is_dir($mapDir)) {
    $msg = 'Directory '.$mapDir.' is missing. Please create it.'."\n";
    clean_exit($msg);
} else {
    $fh = fopen($xmlFile, 'w');
    if (!$fh) {
        $msg = 'Could not open or create new file '.$xmlFile.' for writing.'."\n";
        clean_exit($msg);
    }
}

# Populate the session table from the HUB jos_session table
$sql = 'INSERT IGNORE INTO '.$hub_db.'.'.$db_prefix.'session_geo (ip, session_id, username, time, guest, userid) SELECT ip, session_id, username, time, guest, userid FROM '.$hub_db.'.'.$db_prefix.'session WHERE (UNIX_TIMESTAMP()-time) < '.dbquote($idle_time).' GROUP BY ip, username';
db_exec($db_hub, $sql);

# Fix all sessions whose IP has already been looked up for another session...
#------------------------------------------------------------------------------
$sql = 'SELECT DISTINCT ip, host, domain FROM '.$hub_db.'.'.$db_prefix.'session_geo WHERE host <> "" AND host <> "(unknown)"';
$result = mysqli_query($db_hub, $sql);
if($result) {
    if(mysqli_num_rows($result) > 0) {
        while($row = mysqli_fetch_assoc($result)) {
            $ip = $row['ip'];
            $host = $row['host'];
            $domain = $row['domain'];
                $sql_ = 'UPDATE '.$hub_db.'.'.$db_prefix.'session_geo SET host = ' . dbquote($host) . ', domain = ' . dbquote($domain). ' WHERE ip = ' . dbquote($ip);
            db_exec($db_hub, $sql_);
        }
    }
} else {
    $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
    clean_exit($msg);
}

#  Fix all sessions whose IP requires a reverse DNS lookup...
#------------------------------------------------------------------------------
$sql = 'SELECT DISTINCT ip FROM '.$hub_db.'.'.$db_prefix.'session_geo WHERE (host = "" OR host IS NULL) AND ip <> ""';
$result = mysqli_query($db_hub, $sql);
if($result) {
    if(mysqli_num_rows($result) > 0) {
        while($row = mysqli_fetch_row($result)) {
            $ip = $row[0];
            $host = xgethostbyaddr($ip);
                $sql_ = 'UPDATE '.$hub_db.'.'.$db_prefix.'session_geo SET host = ' . dbquote($host) . ', domain = ' . dbquote(get_domain($ip, $host)). ' WHERE ip = ' . dbquote($ip);
            db_exec($db_hub, $sql_);
        }
    }
} else {
    $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
    clean_exit($msg);
}

# Looking up Latitude and Longiture information for sessions currently online based on their IP addresses
#------------------------------------------------------------------------------
$sql = 'SELECT DISTINCT(INET_ATON(ip)) AS n_ip, domain FROM '.$hub_db.'.'.$db_prefix.'session_geo WHERE ipLATITUDE IS NULL';
$result = mysqli_query($db_hub, $sql);
if($result) {
    if(mysqli_num_rows($result) > 0) {
        while($row = mysqli_fetch_assoc($result)) {
            $n_ip = $row['n_ip'];
            $domain = $row['domain'];
            $bot = 0;
            $sql_bot = 'SELECT COUNT(*) FROM '.$metrics_db.'.exclude_list WHERE filter = '.dbquote($domain).' AND type = "domain"';
            $bot = db_exec($db_hub, $sql_bot);
            if ($bot)
                $bot = 1;
            $data = get_ip_geodata($hubzero_ipgeo_url, $hub_key, $n_ip);
            if ($data) {
                $sql_ = 'UPDATE '.$hub_db.'.'.$db_prefix.'session_geo SET countrySHORT = '.dbquote($data['countrySHORT']).', countryLONG = '.dbquote($data['countryLONG']).', ipREGION = '.dbquote($data['ipREGION']).', ipCITY = '.dbquote($data['ipCITY']).', ipLATITUDE = '.dbquote($data['ipLATITUDE']).', ipLONGITUDE = '.dbquote($data['ipLONGITUDE']).', bot = '.dbquote($bot).' WHERE INET_ATON(ip) = '.dbquote($n_ip);
                db_exec($db_hub, $sql_);
            }
        }
    }
} else {
    $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
    clean_exit($msg);
}

# Creating an xml file that will be read by google maps
#------------------------------------------------------------------------------
$xml = '<markers>'."\n";
$sql = 'SELECT DISTINCT ipLATITUDE, ipLONGITUDE, ipCITY, ipREGION, countrySHORT FROM '.$hub_db.'.'.$db_prefix.'session_geo WHERE ipLATITUDE <> "" GROUP BY ipLATITUDE, ipLONGITUDE';
$result = mysqli_query($db_hub, $sql);
if($result) {
    if(mysqli_num_rows($result) > 0) {
        while($row = mysqli_fetch_row($result)) {
            $location['lat'] = $row[0];
            $location['lng'] = $row[1];
            $city = "_b_".$row[2].", ".$row[3].", ".$row[4]."_bb_";
            $info = get_hosts($db_hub, $location);
            $sql_bot = 'SELECT bot FROM '.$hub_db.'.'.$db_prefix.'session_geo WHERE ipLATITUDE ='.dbquote($location['lat']).' AND ipLONGITUDE = '.dbquote($location['lng']).' ORDER BY bot DESC LIMIT 1';
            $bot = db_fetch($db_hub, $sql_bot);
            $xml .= '<marker lat="'.$location['lat'].'" lng="'.$location['lng'].'" info = "'.$city.'_hr_'.$info.'" bot = "'.$bot.'"/>';
            $xml .= "\n";
        }
    }
} else {
    $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
    clean_exit($msg);
}
$xml .= '</markers>'."\n";
fwrite($fh, $xml);

fclose($fh);

# Clearing out sessions idle longer than 30 mins
$sql = 'DELETE FROM '.$hub_db.'.'.$db_prefix.'session_geo WHERE (UNIX_TIMESTAMP()-time) > '.dbquote($idle_time);
db_exec($db_hub, $sql);

db_close($db_hub);

function get_domain($ip, $host) {

    if ($ip == $host)
        return '(unknown)';

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

    $force = array("brain.grub.org","crawl.yahoo.net","crawl8-public.alexa.com","hanta.yahoo.com","idle.eidetica.com","morgue1.corp.yahoo.com","msnbot.msn.com","panchma.tivra.com","tpiol.tpiol.com","xs4.kso.co.uk","zeus.nj.nec.com","punch.purdue.edu","san2.attens.net","search.msn.com","sac.overture.com","66.237.109.194.ptr.us.xo.net","67.108.223.130.ptr.us.xo.net","67.106.152.131.ptr.us.xo.net");

    $field = array_reverse(explode(".", $host));
    $domain = $host;
    $force_found = 0;

    foreach($force as $forcedomain) {

        $pattern = "/$forcedomain$/";
        if (preg_match($pattern, $host) )
        {
            $domain = $forcedomain;
            $force_found = 1;
        }
    }

    if (!$force_found && $field[0]) {

        $domain = $field[0];
    
        if($field[1]) {
            $domain = $field[1] . "." . $field[0];
        
            if($field[2]) {
                if (!isset($no2_3level[$field[1]]) && strlen($field[1]) == 2 && strlen($field[0]) == 2
                    || isset($int_3level[$field[1]]) && strlen($field[0]) == 2
                    || isset($mil_3level[$field[1]]) && $field[0] == "mil") 
                {
                    $domain = $field[2] . "." . $field[1] . "." . $field[0];
                }
            
                if ($field[3]) {
                    if (isset($us_4level[$field[2]]) && $field[0] == "us") 
                    {
                        $domain = $field[3] . "." . $field[2] . "." . $field[1] . "." . $field[0];
                    }       
                }
            } elseif (preg_match('/^(.+\-.+\-.+\-.+)\-(.+)$/', $field[1])) {
                if (preg_match('/^(.+\-.+\-.+\-.+)\-(.+)$/', $field[1], $matches)) {
                    $field[2] = $matches[1];
                    $field[1] = $matches[2];
                }
                $domain = $field[1] . "." . $field[0];
            } elseif (preg_match('/^(.+\_.+\_.+\_.+)\-(.+)$/', $field[1])) {
                if (preg_match('/^(.+\_.+\_.+\_.+)\-(.+)$/', $field[1], $matches)) {
                    $field[2] = $matches[1];
                    $field[1] = $matches[2];
                }
                $domain = $field[1] . "." . $field[0];
            } elseif(preg_match('/^(.+\_.+\_.+\_.+)\_(.+)$/', $field[1])) {
                if (preg_match('/^(.+\_.+\_.+\_.+)\_(.+)$/', $field[1], $matches)) {
                    $field[2] = $matches[1];
                    $field[1] = $matches[2];
                }
                $domain = $field[1] . "." . $field[0];
            }
            elseif(preg_match('/^(.+\-.+\-.+\-.+)\_(.+)$/', $field[1])) {
                if (preg_match('/^(.+\-.+\-.+\-.+)\_(.+)$/', $field[1], $matches)) {
                    $field[2] = $matches[1];
                    $field[1] = $matches[2];
                }
                $domain = $field[1] . "." . $field[0];
            }
        }
    }

    if (!$domain || $domain == ".") {
        $domain = "(unknown)";
    }

    return($domain);
}

function get_hosts($db_hub, $location) {

    global $hub_db, $db_prefix;
    
    $info = '';

    $sql = 'SELECT DISTINCT(domain) FROM '.$hub_db.'.'.$db_prefix.'session_geo WHERE ipLATITUDE = '.dbquote($location['lat']).' AND ipLONGITUDE = '.dbquote($location['lng']);
    $result = mysqli_query($db_hub, $sql);
    if($result) {
        if(mysqli_num_rows($result) > 0) {
            while($row = mysqli_fetch_row($result)) {
                $info .= "_b_".$row[0]."_bb_".get_count($db_hub, $row[0], $location);
            }
        }
    } else {
        $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
        clean_exit($msg);
    }
    return rtrim($info,'_br_');
}

function get_count($db_hub, $domain, $location) {

    global $hub_db, $db_prefix;

    $info = '';

    $sql = 'SELECT COUNT(DISTINCT username) FROM '.$hub_db.'.'.$db_prefix.'session_geo WHERE guest = 0 AND domain = '.dbquote($domain).' AND ipLATITUDE = '.dbquote($location['lat']).' AND ipLONGITUDE = '.dbquote($location['lng']).' LIMIT 1';
    $users = db_fetch($db_hub, $sql);
    if ($users) { 
        $info = "_br_ - Users: ".$users;
    }
    $sql = 'SELECT COUNT(DISTINCT ip) FROM '.$hub_db.'.'.$db_prefix.'session_geo WHERE guest = 1 AND domain = '.dbquote($domain).' AND bot = 0 AND ipLATITUDE = '.dbquote($location['lat']).' AND ipLONGITUDE = '.dbquote($location['lng']).' LIMIT 1';
    $guests = db_fetch($db_hub, $sql);
    if ($guests) { 
        $info .= "_br_ - Guests: ".$guests;
    }
    $sql = 'SELECT COUNT(DISTINCT ip) FROM '.$hub_db.'.'.$db_prefix.'session_geo WHERE guest = 1 AND domain = '.dbquote($domain).' AND bot = 1 AND ipLATITUDE = '.dbquote($location['lat']).' AND ipLONGITUDE = '.dbquote($location['lng']).' LIMIT 1';
    $bots = db_fetch($db_hub, $sql);
    if ($bots) { 
        $info .= "_br_ - Bots: ".$bots;
    }

    if($info){
        $info = $info."_br_";
        return $info;
    } else {
        return "_br_";
    }
}

?>
