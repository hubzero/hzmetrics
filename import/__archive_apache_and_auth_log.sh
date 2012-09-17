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
# Archiving apache and CMS logs
# -----------------------------------------------------------------------------------------------
files=$(ls /var/log/apache2/daily/$site-access.log-* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	mv --backup=numbered /var/log/apache2/daily/$site-access.log-* /var/log/apache2/imported/
fi

files=$(ls /var/log/hubzero/daily/cmsauth.log-* 2> /dev/null | wc -l)
if [ "$files" != "0" ]
then
	mv --backup=numbered /var/log/hubzero/daily/cmsauth.log-* /var/log/hubzero/imported/
fi
