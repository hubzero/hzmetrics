#!/usr/bin/bash
# @package      hubzero-metrics
# @file         xlogfix_dns_v2.sh
# @copyright    Copyright (c) 2016-2023 The Regents of the University of California.
# @license      http://opensource.org/licenses/MIT MIT
# @trademark    HUBzero is a registered trademark of The Regents of the University of California.
#
# =========================================================================
# This script resolves host fields from ip address fields in indicated table, for selected dates
#
# USAGE: ./xlogfix_dns_v2.sh <database-prefix> <table-name> [YYYY-MM]

SCRIPT=`readlink -f $0`
SCRIPTPATH=`dirname $SCRIPT`
DEBUG="0"

# determine the date range to process:
if [ $# -lt 3 ]
# only two args passed (db name and table name)
then
    #echo "xlogfix_dns_v2: no date arg passed, using 'date'"
    limitdate=`date '+%C%y-%m-%d' -d "$end_date-7 days"`
    begdate=`date '+%C%y-%m-%d' -d "$end_date-1 days"`
    enddate=`date '+%C%y-%m-%d'`
else
# year and month were passed; run dns resolver for each day of that month
    #echo "xlogfix_dns_v2: date arg passed: $3"
    # limitdate: first day of specified month
    limitdate=`date -d "$3-01" '+%F'`
    # enddate: last day of specified month
    enddate=`date -d "$limitdate +1 month -1 day" '+%F'`
    # begdate: last day but one of specified month. Work backwards from there.
    begdate=`date -d "$enddate -1 day" '+%F'`
fi

if [ $DEBUG == "1" ]
then
    echo "limitdate=$limitdate"
    echo "begdate=$begdate"
    echo "enddate=$enddate"
    echo "starting calculation... "
fi

# call the worker script, walking backwards through the date range:
while [ $begdate != $limitdate ]
do
    if [ $DEBUG == "1" ]
    then
        echo "calling xlogfix_dns_worker.php with: " $1 $2 $begdate $enddate&
    fi
    $SCRIPTPATH/xlogfix_dns_worker.php $1 $2 $begdate $enddate&
    enddate=$begdate
    begdate=`date '+%C%y-%m-%d' --date="$begdate -1 days"`
done

# now do one more, the first day:
if [ $DEBUG == "1" ]
then
    echo "last call to xlogfix_dns_worker.php with: " $1 $2 $begdate $enddate
fi
$SCRIPTPATH/xlogfix_dns_worker.php $1 $2 $begdate $enddate&

ps aux |grep "xlogfix_dns_worker"|grep -qv grep
while [ $? = 0 ]
do
    sleep 1
    ps aux |grep "xlogfix_dns_worker"|grep -qv grep
done

