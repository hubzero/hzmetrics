#!/bin/bash 
SCRIPT=`readlink -f $0`
SCRIPTPATH=`dirname $SCRIPT`

# -----------------------------------------------------------------------------------------------
# Importing apache logs
# -----------------------------------------------------------------------------------------------
if [ -f $SCRIPTPATH/_hub_apache.log ]
then
	$SCRIPTPATH/xlogimport_webhits $SCRIPTPATH/_hub_apache.log >> $SCRIPTPATH/_apache_webhits.err
	$SCRIPTPATH/xlogfix_identify_bots $SCRIPTPATH/_hub_apache.log >> $SCRIPTPATH/_apache_bots.err
	$SCRIPTPATH/xlogimport_apache $SCRIPTPATH/_hub_apache.log >> $SCRIPTPATH/_apache_import.err
	mv $SCRIPTPATH/_hub_apache.log $SCRIPTPATH/_prev_hub_apache.log
fi

# -----------------------------------------------------------------------------------------------
# Importing CMS logs
# -----------------------------------------------------------------------------------------------
if [ -f $SCRIPTPATH/_hub_auth.log ]
then
	$SCRIPTPATH/xlogimport_authlog $SCRIPTPATH/_hub_auth.log >> $SCRIPTPATH/_cmsauth_import.err
	mv $SCRIPTPATH/_hub_auth.log $SCRIPTPATH/_prev_hub_auth.log
fi
