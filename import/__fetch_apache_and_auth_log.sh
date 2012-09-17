#!/bin/bash 
SCRIPT=`readlink -f $0`
SCRIPTPATH=`dirname $SCRIPT`

if [ -f /etc/hubzero.conf ]
then
  site=$(grep site= /etc/hubzero.conf | sed 's/site=//')
else
  site=hub
fi

# -----------------------------------------------------------------------------------------------
# Fetching apache and CMS logs
# -----------------------------------------------------------------------------------------------
files=$(ls /var/log/apache2/daily/"$site"-access.log-* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	cat /var/log/apache2/daily/$site-access.log-* > $SCRIPTPATH/_hub_apache.log
fi

files=$(ls /var/log/hubzero/daily/cmsauth.log-* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	cat /var/log/hubzero/daily/cmsauth.log-* > $SCRIPTPATH/_hub_auth.log
fi
