<?php
# @package      hubzero-metrics
# @file         func_misc.php
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

function get_tool_versions_aliases($db_hub, $aliases_x) {

    global $hub_db, $db_prefix;
    if ($aliases_x) {
        $aliases = $aliases_x.',';
        $sql = 'SELECT DISTINCT instance FROM '.$hub_db.'.'.$db_prefix.'tool_version WHERE toolname IN ('.$aliases_x.') AND instance NOT LIKE "%\_dev"';
        $result = mysqli_query($db_hub, $sql);
        if($result) {
            if(mysqli_num_rows($result) > 0) {
                while($row = mysqli_fetch_row($result)) {
                    $aliases .= '"'.$row[0].'",';
                }
            }
        } else {
            $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
            clean_exit($msg);
        }
        $aliases_x = rtrim ($aliases,',');
    }
    /* not needed as HUBs don't have jos_tool_version_alias tables (?)
    if ($aliases_x) {
        $sql = 'SELECT DISTINCT tva.alias FROM '.$hub_db.'.'.$db_prefix.'tool_version_alias AS tva, '.$hub_db.'.'.$db_prefix.'tool_version AS tv WHERE tva.tool_version_id = tv.id AND tv.toolname IN ('.$aliases_x.') AND tva.alias NOT LIKE "%\_dev"';
        $result = mysqli_query($db_hub, $sql);
        if($result) {
            if(mysqli_num_rows($result) > 0) {
                while($row = mysqli_fetch_row($result)) {
                    $aliases .= '"'.$row[0].'",';
                }
            }
        } else {
            $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
            clean_exit($msg);
        }
        $aliases_x = rtrim ($aliases,',');
    }
    if ($aliases_x) {
        $sql = 'SELECT DISTINCT instance FROM '.$hub_db.'.'.$db_prefix.'tool_version WHERE toolname IN ('.$aliases_x.') AND instance NOT LIKE "%\_dev"';
        $result = mysqli_query($db_hub, $sql);
        if($result) {
            if(mysqli_num_rows($result) > 0) {
                while($row = mysqli_fetch_row($result)) {
                    $aliases .= '"'.$row[0].'",';
                }
            }
        } else {
            $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
            clean_exit($msg);
        }
        $aliases_x = rtrim ($aliases,',');
    }
    */
    return $aliases_x;

}

# --------------------------------------------------------------------------------------------

/*
 * findWeeks()
 *
 * Compute start and end dates for approximately week-long periods in
 * specified YYYY-MM interval. Return computed start/end dates as strings in an array.
 *
 * input:
 *   yearMonthStr YYYY-MM string indicating effective month for calculation
 *   endDayStr    dd      string indicating end day for calculation, if any
 *
 * output:
 *   periods  array of strings indicating start/end dates for approx. 7 day 'weeks'
 *      indexed as 'start' and 'end'
*/
function findWeeks($yearMonthStr, $endDayStr = NULL)
{
    // Convert the yearMonth string to a DateTime object
    $yearMonth = DateTime::createFromFormat('Y-m', $yearMonthStr);

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

    // Calculate approximately 7-day periods
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


# --------------------------------------------------------------------------------------------

# Execute sql function used for SELECT statements with single returns (for $db_hub only)
function db_fetch(&$db_hub, $sql) {

    global $debug;
    if ($debug)
        print $sql."\n";

    $val = '';
    if ($db_hub instanceof mysqli) {
        if (!mysqli_ping($db_hub))
            $db_hub = db_connect('db_hub');

        $result = mysqli_query($db_hub, $sql);
        if($result) {
            if(mysqli_num_rows($result) > 0) {
                while($row = mysqli_fetch_row($result)) {
                    $val = $row[0];
                }
            }
        } else {
            $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
            clean_exit($msg);
        }
    }
    return $val;
}

# --------------------------------------------------------------------------------------------
# Execute sql function used for INSERTs, DELETEs and UPDATEs
function db_exec($db, $sql) {

    $result = mysqli_query($db, $sql);
    if(!$result) {
        $msg = mysqli_error($db).' while executing '.$sql."\n";
        clean_exit($msg);
    }
}

# --------------------------------------------------------------------------------------------

function get_dates($dthis_, $period) {

    $dt_pattern_1 = '/^(\d{4})-(\d{2})-(\d{2})$/';
    $dt_pattern_2 = '/^(\d{4})-(\d{2})$/';

    if ( (preg_match($dt_pattern_1, $dthis_, $matches) <> 0) && ($matches[3] <> '00') ) {
        $dthis = $matches[1].'-'.$matches[2];
        $dates = get_dates_for_period($dthis, $period);
    } else if (preg_match($dt_pattern_2, $dthis_, $matches) <> 0)  {
        $dates = get_dates_for_period($matches[0], $period);
    } else {
        $msg = 'Invalid Date '.$dthis_."\n";
        clean_exit($msg);
    }
    $dates['dthis'] =  $matches[1]."-".$matches[2].'-00';
    return $dates;
}

# --------------------------------------------------------------------------------------------

function dateformat_plot($seldate) {
    $year = substr($seldate, 0, 4);
    $month = substr($seldate, 5, 2);
    $day = substr($seldate, 8, 2);
    if($day > 0) {
        return(sprintf("%02d/%02d/%04d", $month, $day, $year));
    }
    else {
        return(sprintf("%02d/%04d", $month, $year));
    }
}

// --------------------------------------------------------------

function get_dates_for_period($dthis, $period) {

    $givendate = $dthis.'-01';
    $cd = strtotime($givendate);
    $d_month = date('m', $cd);
    $d_year = date('Y', $cd);
    $dates = NULL;
    $date1 = NULL;
    $date2 = NULL;

    switch ($period) {

        // Calendar Year
        case 0:
            $date1 = date('Y', mktime(idate('h',$cd),idate('i',$cd), idate('s',$cd), idate('m',$cd), idate('d',$cd), idate('Y',$cd))).'-01-01';
            $date2 = date('Y-m', mktime(idate('h',$cd),idate('i',$cd), idate('s',$cd), idate('m',$cd)+1, idate('d',$cd), idate('Y',$cd))).'-01';
            $dates = array("start"=>$date1,"stop"=>$date2);
            break;

        // Month
        case 1:
            $date1 = date('Y-m', mktime(idate('h',$cd),idate('i',$cd), idate('s',$cd), idate('m',$cd), idate('d',$cd), idate('Y',$cd))).'-01';
            $date2 = date('Y-m', mktime(idate('h',$cd),idate('i',$cd), idate('s',$cd), idate('m',$cd)+1, idate('d',$cd), idate('Y',$cd))).'-01';
            $dates = array("start"=>$date1,"stop"=>$date2);
            break;

        // Quarter
        case 3:
            if ($d_month >= 1 && $d_month <=3) {
                $date1 = $d_year."-01-01";
            } else if ($d_month >= 4 && $d_month <=6) {
                $date1 = $d_year."-04-01";
            } else if ($d_month >= 7 && $d_month <=9) {
                $date1 = $d_year."-07-01";
            } else if ($d_month >= 10 && $d_month <=12) {
                $date1 = $d_year."-10-01";
            }
            $date2 = date('Y-m', mktime(idate('h',$cd),idate('i',$cd), idate('s',$cd), idate('m',$cd)+1, idate('d',$cd), idate('Y',$cd))).'-01';
            $dates = array("start"=>$date1,"stop"=>$date2);
            break;

        // 12 months
        case 12:
            $date1 = date('Y-m', mktime(idate('h',$cd),idate('i',$cd), idate('s',$cd), idate('m',$cd)-11, idate('d',$cd), idate('Y',$cd))).'-01';
            $date2 = date('Y-m', mktime(idate('h',$cd),idate('i',$cd), idate('s',$cd), idate('m',$cd)+1, idate('d',$cd), idate('Y',$cd))).'-01';
            $dates = array("start"=>$date1,"stop"=>$date2);
            break;

        // Fiscal Year (Oct - Sep)
        case 13:
            $date2 = date('Y-m', mktime(idate('h',$cd),idate('i',$cd), idate('s',$cd), idate('m',$cd)+1, idate('d',$cd), idate('Y',$cd))).'-01';
            if ($d_month >= 10) {
                $date1 = $d_year.'-10-01';
            } else {
                $date1 = ($d_year-1).'-10-01';
            }
            $dates = array("start"=>$date1,"stop"=>$date2);
            break;

        // Overall Time period
        case 14:
            $date1 = "1995-01-01";
            $date2 = date('Y-m', mktime(idate('h',$cd),idate('i',$cd), idate('s',$cd), idate('m',$cd)+1, idate('d',$cd), idate('Y',$cd))).'-01';
            $dates = array("start"=>$date1,"stop"=>$date2);
            break;

        default:
            $msg = 'Invalid Period '.$period."\n";
            clean_exit($msg);

    }

    return $dates;

}

function xgethostbyaddr($ip, $timeout = 1)
{
    $cmd = "/bin/bash -c \"/usr/bin/host -W $timeout $ip 2>/dev/null\"";
    $output = shell_exec($cmd);

    if (preg_match('/.*pointer ([A-Za-z0-9.-]+)\..*/',$output,$regs))
        return $regs[1];

    return $ip;
}

function dbquote($str) {

    global $db_hub;

    if ($db_hub instanceof mysqli) {
        return '"' . mysqli_real_escape_string($db_hub, $str) . '"';
    }
}

function get_countries(&$db_hub, $sql) {

    if (!mysqli_ping($db_hub))
        $db_hub = db_connect('db_hub');

    $countries = "";
    $result = mysqli_query($db_hub, $sql);
    if($result) {
        if(mysqli_num_rows($result) > 0) {
            while($row = mysqli_fetch_row($result)) {
                $countries .= '"'.$row[0].'",';
            }
        }
    } else {
        $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
        clean_exit($msg);
    }
    $countries = rtrim($countries,',');
    return $countries;

} 

function get_rappture_tools() {

    $rappture_tools = '"workspace",';

    $cmd = "/bin/bash -c \"find /apps -name 'tool.xml' 2>/dev/null\"";
    $cmd_res = shell_exec($cmd);
    if ($cmd_res) {
        $applines = explode("\n", $cmd_res);
        $applines = array_filter($applines);

        $apps = array();
        foreach ($applines as $appline) {
            $tmp = explode("/",$appline);
            if ($tmp[2])
                array_push($apps, $tmp[2]);
        }

        $apps = array_unique($apps);
        foreach ($apps as $app) 
            $rappture_tools .= '"'.$app.'",';

    }
    $rappture_tools = rtrim($rappture_tools, ',');
    return $rappture_tools;

}

function get_ip_list(&$db_hub, $sql) {

    $login_ips = '"127.0.0.1",';
    $result = mysqli_query($db_hub, $sql);
    if($result) {
        if(mysqli_num_rows($result) > 0) {
            while($row = mysqli_fetch_row($result)) {
                $login_ips .= '"'.$row[0].'",';
            }
        }
    } else {
        $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
        clean_exit($msg);
    }
    $login_ips = rtrim($login_ips,',');

    return $login_ips;
}   

function search_array($needle, $haystack)
{
    if (is_array($haystack)) {
        foreach ($haystack as $hay) {
            if (stripos($needle, $hay) !== false) {
                return true;
            }
        }
    }
    return false;
}

function gen_exclude_list($type) {

    global $metrics_db, $db_hub;

    if ($type == "ip")
        $arr = array("127.0.0.1");
    if ($type == "url")
        $arr = array("task=diskusage");
    if ($type == "useragent")
        $arr = array("gsa-purdue-crawler");

    $arr = array();

    $sql = 'SELECT filter FROM '.$metrics_db.'.exclude_list WHERE type = '.dbquote($type);
    if ($db_hub instanceof mysqli) {
        $result = mysqli_query($db_hub, $sql);
        if($result) {
            if(mysqli_num_rows($result) > 0) {
                while($row = mysqli_fetch_row($result)) {
                    array_push($arr, $row[0]);
                }
            }
        } else {
            $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
            clean_exit($msg);
        }
    }
    return $arr;

}

function checkbot (&$db_hub, $useragent) {

    global $metrics_db;

    $bot = 0;

    $sql = 'SELECT COUNT(*) FROM '.$metrics_db.'.bot_useragents WHERE useragent = '.dbquote($useragent);
    $bot = db_fetch($db_hub, $sql);

    return $bot;
}

function get_ip_geodata($hubzero_ipgeo_url, $hub_key, $ip) {

    global $hub_db, $db_hub, $db_prefix;

    // Convert provided ip address from dotted quad to a long:
    $n_ip = ip2long($ip);

    $geo_data = array();
    $geo_data['n_ip'] = $n_ip;
    $geo_data['countrySHORT'] = '-';
    $geo_data['countryLONG'] = '-';
    $geo_data['ipREGION'] = '-';
    $geo_data['ipCITY'] = '-';
    $geo_data['ipLATITUDE'] = '-';
    $geo_data['ipLONGITUDE'] = '-';

    $local_exists = 0;
    if (!is_numeric($n_ip))
        return $geo_data;

    // If possible, determine location information using the local database table. IP is expected in long format:
    $sql = 'SELECT COUNT(*) FROM '.$hub_db.'.'.$db_prefix.'metrics_ipgeo_cache WHERE ip = '.dbquote($n_ip).' AND TO_DAYS(CURDATE())-TO_DAYS(lookup_datetime) <= 90';
    $local_exists = db_fetch($db_hub, $sql);
    if ($local_exists) {
        $sql = 'SELECT countrySHORT, countryLONG, ipREGION, ipCITY, ipLATITUDE, ipLONGITUDE, lookup_datetime FROM '.$hub_db.'.'.$db_prefix.'metrics_ipgeo_cache WHERE ip = '.dbquote($n_ip).' AND TO_DAYS(CURDATE())-TO_DAYS(lookup_datetime) <= 90';
        $result = mysqli_query($db_hub, $sql);
        if($result) {
            if(mysqli_num_rows($result) > 0) {
                while($row = mysqli_fetch_assoc($result)) {
                    $geo_data['countrySHORT'] =  $row['countrySHORT'];
                    $geo_data['countryLONG'] = $row['countryLONG'];
                    $geo_data['ipREGION'] = $row['ipREGION'];
                    $geo_data['ipCITY'] = $row['ipCITY'];
                    $geo_data['ipLATITUDE'] = $row['ipLATITUDE'];
                    $geo_data['ipLONGITUDE'] = $row['ipLONGITUDE'];
                    return $geo_data;
                }
            }
        } else {
            $msg = mysqli_error($db_hub).' while executing '.$sql."\n";
            clean_exit($msg);
        }
    // Otherwise, determine location information using the ipgeo url:
    } else {
        $url = $hubzero_ipgeo_url.'/?&hub_key='.$hub_key.'&n_ip='.$n_ip;
        $xml = @simplexml_load_file($url);
        if (!$xml)
            print 'Warning: Could not connect to remote ip-location lookup webservice on '.$hubzero_ipgeo_url."\n";
        if ( ($xml->status == "_SUCCESS_") && ($n_ip == $xml->ipset->n_ip) ) {
            $geo_data['n_ip'] = $xml->ipset->n_ip;
            $geo_data['countrySHORT'] = $xml->ipset->countryCode;
            $geo_data['countryLONG'] = $xml->ipset->countryName;
            $geo_data['ipREGION'] = $xml->ipset->region;
            $geo_data['ipCITY'] = $xml->ipset->city;
            $geo_data['ipLATITUDE'] = $xml->ipset->lat;
            $geo_data['ipLONGITUDE'] = $xml->ipset->long;
            if ($geo_data['countrySHORT'] <> '-') {
                $sql_ins = 'INSERT INTO '.$hub_db.'.'.$db_prefix.'metrics_ipgeo_cache (ip, countrySHORT, countryLONG, ipREGION, ipCITY, ipLATITUDE, ipLONGITUDE) VALUES ('.dbquote($geo_data['n_ip']).','.dbquote($geo_data['countrySHORT']).','.dbquote($geo_data['countryLONG']).','.dbquote($geo_data['ipREGION']).','.dbquote($geo_data['ipCITY']).','.dbquote($geo_data['ipLATITUDE']).','.dbquote($geo_data['ipLONGITUDE']).') ON DUPLICATE KEY UPDATE countrySHORT = '.dbquote($geo_data['countrySHORT']).', countryLONG = '.dbquote($geo_data['countryLONG']).', ipREGION = '.dbquote($geo_data['ipREGION']).', ipCITY = '.dbquote($geo_data['ipCITY']).', ipLATITUDE = '.dbquote($geo_data['ipLATITUDE']).', ipLONGITUDE = '.dbquote($geo_data['ipLONGITUDE']);
                db_exec($db_hub, $sql_ins);
            }
        } else if ( $xml->status == "_INVALID_KEY_OR_KEY-HUB_HOSTNAME_MISMATCH_" ) {
            print 'Warning: HUBzero.org IP-Geo location key is invalid. Please check hubconfiguration.php for "$hubzero_ipgeo_key". Please submit a support ticket on hubzero.org if the problem persists.'."\n";
        }
    }
    return $geo_data;

}

function clean_exit($msg="") {

    global $db_hub;

    print $msg;

    if (isset($db_hub) && mysqli_ping($db_hub))
        db_close($db_hub);

    die;
}
?>
