#!/usr/bin/perl -w
#
# @package      hubzero-metrics
# @file         logfix_session.pl
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
# USAGE: ./logfix_session.pl --date [YYYY-MM]

use strict;
use DBI;

use FindBin '$Bin';
my $filename = $ENV{HZMETRICS_ACCESS_CFG} || '/etc/hubzero-metrics/access.cfg';

our $hub_db;
our $metrics_db;
our $db_host;
our $db_user;
our $db_pass;
do $filename;

#my $procmonth = shift(@ARGV);
my @dbrow;
my $dbhandle;
my $dbsthandle;
my $dbrowvalues;
my $statement;
my $id;
my $datetime;
my $datetimeint;
my $content;
my $ip;
my $host;
my $duration;
my $domain;
my $s_id;
my $s_datetime;
my $s_datetimeint;
my $s_ip;
my $s_host;
my $s_duration;
my $s_domain;
my $s_jobs;
my @s_events;
my $s_webevents;
my $s_videoevents;
my $s_ee659;
my $s_videoend;
my $s_endfound;
my $prev_datetime;
my $prev_datetimeint;
my $i;
my $stime_inactive = 1800;

my $debug = 0;
my $year = '';
my $month = '';
my $dthis;

# get date from localtime or from passed param:
if(@ARGV < 1) {
   # handle localtime perl things
   ($month, $year) = (localtime)[4,5];
   $year += 1900;
   $month++;
# get date from passed param:
} else {
   $dthis = shift();
   ($year, $month) = split('-', $dthis);
}

if ($debug) {
   print " month: $month\n";
   print " year $year\n";
}

# Opening database...
$dbhandle = DBI->connect("DBI:mysql:$metrics_db:$db_host", $db_user, $db_pass);

# prepare array of start/end dates for the processing:
my $firstday = 1;
my $lastweekday = 1;

$lastweekday = $firstday + 7;

# -- create array of start/end date of weeks of the month --
my @weekbegindt = ("$year-$month-$firstday");
my @weekenddt = ("$year-$month-$lastweekday");

for (my $i = 1; $i < 4; $i += 1) {

    $firstday = $firstday + 8;
    push(@weekbegindt, "$year-$month-$firstday");

    if ($i < 3) {
       $lastweekday = $firstday + 7;
    } else {

        $lastweekday = 1;

        if ($month > 11) {
            $month = 1;
            $year = $year + 1;
        } else {
            $month = $month + 1;
        }
    }
    push(@weekenddt, "$year-$month-$lastweekday");
}

# here are the start/end dates of those weeks
#if ($debug) {
#    for (my $i = 0; $i < 4; $i += 1) {
#        print "  week $i: $weekbegindt[$i] to $weekenddt[$i]\n";
#    }
#}


#  Finding next available web session id...
$s_id = 0;
$statement = "SELECT MAX(id) FROM websessions";
$dbsthandle = $dbhandle->prepare($statement)
    or die "Error:  can't prepare statement \"$statement\" ($dbhandle->errstr).\n";
$dbrowvalues = $dbsthandle->execute
    or die "Error:  can't execute the query ($dbsthandle->errstr).\n";
if(@dbrow = $dbsthandle->fetchrow_array) {
    if($dbrow[0]) {
        $s_id = $dbrow[0];
    }
}

#  Select all web records missing corresponding session records...
for (my $week_num = 0; $week_num < 4; $week_num += 1) {

    if ($debug) {
        print "  week $week_num: $weekbegindt[$week_num] to $weekenddt[$week_num]\n";
    }

    #$statement = "SELECT id, datetime, content, ip, host, domain, UNIX_TIMESTAMP(datetime) FROM web WHERE (sessionid = '0' OR sessionid IS NULL) AND (ip <> '' OR (host <> '' AND host <> '?' AND host IS NOT NULL)) ORDER BY ip, host, datetime";
    $statement = "SELECT id, datetime, content, ip, host, domain, UNIX_TIMESTAMP(datetime) FROM web WHERE datetime >= '$weekbegindt[$week_num]' AND datetime < '$weekenddt[$week_num]' AND (sessionid = '0' OR sessionid IS NULL) AND (ip <> '' OR (host <> '' AND host <> '?' AND host IS NOT NULL)) ORDER BY ip, host, datetime";

    if ($debug) {
        print "  $statement\n";
    }

    $dbsthandle = $dbhandle->prepare($statement)
        or die "Error:  can't prepare statement \"$statement\" ($dbhandle->errstr).\n";
    $dbrowvalues = $dbsthandle->execute
        or die "Error:  can't execute the query ($dbsthandle->errstr).\n";

    #  Loop through all web activity...
    #-----------------------------------
    while(@dbrow = $dbsthandle->fetchrow_array) {
        $id = $dbrow[0];
        $datetime = $dbrow[1];
        $content = $dbrow[2];
        $ip = $dbrow[3];
        $host = $dbrow[4];
        $domain = $dbrow[5];
        $datetimeint = $dbrow[6];

        #  Check for end of session...
        #------------------------------
        if($s_datetime && ($s_ip && $s_ip ne $ip) || (!$s_ip && $s_host && $s_host ne $host)) {
            # New IP/host...
            $s_endfound = 1;
        }
        elsif(($s_ip || $s_host) && ($datetimeint - $prev_datetimeint > $stime_inactive) && ($datetimeint - $s_videoend > $stime_inactive)) {
            # Timed out...
            $s_endfound = 1;
        }
        else {
            $s_endfound = 0;
        }

        #  Session end found, insert new websession record...
        #-----------------------------------------------------
        if($s_endfound) {
            if($s_videoend > $prev_datetimeint) {
                $prev_datetimeint = $s_videoend;
            }
            $s_duration = $prev_datetimeint - $s_datetimeint;
            $s_id++;
            $s_jobs = iphost_jobs($dbhandle, $s_id, $s_ip, $s_host, $s_datetime, $prev_datetime);
            $statement = "INSERT INTO websessions (id, datetime, ip, host, duration, domain, jobs, webevents) VALUES ("
                 . $dbhandle->quote($s_id) . ", "
                 . $dbhandle->quote($s_datetime) . ", ";
            if($s_ip) {
                $statement .= $dbhandle->quote($s_ip);
            }
            else {
                $statement .= "''";
            }
            $statement .= ", ";
            if($s_host) {
                $statement .= $dbhandle->quote($s_host);
            }
            else {
                $statement .= "''";
            }
            $statement .= ", " . $dbhandle->quote($s_duration) . ", ";
            if($s_domain) {
                $statement .= $dbhandle->quote($s_domain);
            }
            else {
                $statement .= "''";
            }
            $statement .= ", "
                 . $dbhandle->quote($s_jobs) . ", "
                 . $dbhandle->quote($s_webevents) . ") ";

            if ($debug) {
                print "$statement\n";
	    }
            $dbhandle->do($statement)
                or die "Error:  can't execute statement \"$statement\" ($dbhandle->errstr).\n";

            $statement = "UPDATE web SET sessionid = "
                 . $dbhandle->quote($s_id) . " WHERE ";
            for($i = 0; $i < scalar(@s_events); $i++) {
                if($i > 0) {
                    $statement .= " OR ";
                }
                $statement .= "id = " . $dbhandle->quote($s_events[$i]);
            }

            if ($debug) {
	        print "$statement\n";
            }
            $dbhandle->do($statement)
                or die "Error:  can't execute statement \"$statement\" ($dbhandle->errstr).\n";

            $s_datetime = "";
        }

        #  Session beginning found, restart tracking...
        #-----------------------------------------------
        if(!$s_datetime) {
            $s_webevents = 0;
            $s_videoevents = 0;
            $s_videoend = 0;
            $s_ee659 = 0;
            $s_datetime = $datetime;
            $s_datetimeint = $datetimeint;
            $s_ip = "";
            $s_host = "";
            $s_domain = "";
            @s_events = ();
        }

        #  Watch for potentially missing/partial session information...
        #---------------------------------------------------------------
        if(!$s_ip && $ip) {
            $s_ip = $ip;
        }
        if(!$s_host && $host) {
            $s_host = $host;
        }
        if(!$s_domain && $domain) {
            $s_domain = $domain;
        }

        $prev_datetime = $datetime;
        $prev_datetimeint = $datetimeint;
        push(@s_events, $id);
    }
}

#  Close database...
$dbsthandle->finish;
$dbhandle->disconnect;

#function iphost_jobs($dbhandle, $s_id, $ip, $host, $dstart, $dstop) {
sub iphost_jobs {
    my $dbhandle;
    my $s_id;
    my $ip;
    my $host;
    my $dstart;
    my $dstop;
    my $dbsthandle;
    my @dbrow;
    my @s_jobs;
    my $i;
    my $jobs = 0;

    ($dbhandle, $s_id, $ip, $host, $dstart, $dstop) = @_;

    $statement = "SELECT id FROM toolstart WHERE datetime >= " . $dbhandle->quote($dstart) . " AND UNIX_TIMESTAMP(datetime) <= (UNIX_TIMESTAMP("
        . $dbhandle->quote($dstop) . ")+1799) AND success = '1' AND ";
    if($ip && $host) {
        $statement .= "(ip = " . $dbhandle->quote($ip) . " OR host = " . $dbhandle->quote($host) . ")";
    }
    elsif($ip) {
        $statement .= "ip = " . $dbhandle->quote($ip);
    }
    elsif($host) {
        $statement .= "host = " . $dbhandle->quote($host);
    }
    else {
        return(0);
    }
    $dbsthandle = $dbhandle->prepare($statement)
        or die "Error:  can't prepare statement \"$statement\" ($dbhandle->errstr).\n";
    $dbrowvalues = $dbsthandle->execute
        or die "Error:  can't execute the query ($dbsthandle->errstr).\n";
    @s_jobs = ();
    while(@dbrow = $dbsthandle->fetchrow_array) {
        push(@s_jobs, $dbrow[0]);
    }
    $dbsthandle->finish;
    $jobs = scalar(@s_jobs);
    if($jobs) {
        $statement = "UPDATE toolstart SET sessionid = " . $dbhandle->quote($s_id) . " WHERE ";
        for($i = 0; $i < $jobs; $i++) {
            if($i > 0) {
                $statement .= " OR ";
            }
            $statement .= "id = " . $dbhandle->quote($s_jobs[$i]);
        }

        if ($debug) {
	     print "$statement\n";
	}
        $dbhandle->do($statement)
            or die "Error:  can't execute statement \"$statement\" ($dbhandle->errstr).\n";

    }
    return($jobs);
}

