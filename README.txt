# @package      hubzero-metrics
# @file         README.txt
# @author       Swaroop Shivarajapura <swaroop@purdue.edu>
# @copyright    Copyright (c) 2011-2013 HUBzero Foundation, LLC.
# @license      http://www.gnu.org/licenses/lgpl-3.0.html LGPLv3
#
# Copyright (c) 2011-2013 HUBzero Foundation, LLC.
#
# This file is part of: The HUBzero(R) Platform for Scientific Collaboration
#
# The HUBzero(R) Platform for Scientific Collaboration (HUBzero) is free
# software: you can redistribute it and/or modify it under the terms of
# the GNU Lesser General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# HUBzero is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# HUBzero is a registered trademark of HUBzero Foundation, LLC.
#

-------------------------------------------------------------------------
A. General Info
-------------------------------------------------------------------------

Using 'myhub.org' as an example.  HUB content database is 'myhub' and metrics database is 'myhub_metrics'.

Usage metrics are gathered daily from log files and from tables in the Joomla database. Also, IP geolocation (what city an IP address comes from) is gathered from an outside web service. Google maps provides the map visualization. All this data is saved in a new database on the hub named <myhub>_metrics.

The display is composed of plugins within a Joomla component. The plugins get the data from the database.

Where files are kept:

Component:
/www/myhub/components/com_usage

Plugins:
/www/myhub/plugins/usage

Logging stuff:
/etc/cron.d/crontab_root
/etc/cron.d/crontab_metrics (created by the installer file)
/etc/logrotate.d/hubzero

code that crontab_metrics runs to gather usage data: /opt/hubzero/bin/metrics

Location of log files
Apache: /var/log/apache2/hub-access.log
HUB CMS: /var/log/hubzero/cms-auth.log

-------------------------------------------------------------------------
B. Instructions for installing Usage metrics on open source HUBzero 1.0.0
-------------------------------------------------------------------------

1. Copy the contents of the metrics package (hubzero_metrics.zip) to /opt/hubzero/bin/metrics/

2. Install graphics tools:

	sudo apt-get install gnuplot
	sudo apt-get install netpbm

3. Add the following variables to /www/myhub/hubconfiguration.php

	var $hubzero_ipgeo_url = 'http://hubzero.org/ipinfo/v1';
	var $hubzero_ipgeo_key = '_HUBZERO_OPNSRC_V1_';

4. Login to MySQL as root and execute the following command.

	CREATE DATABASE IF NOT EXISTS myhub_metrics;

5. Delete the following directory: /www/myhub/installation/

6. Configure the usage component from the backend with database connection parameters and Google Maps API

	myhub.org/administrator Components -> Usage -> Parameters

	where to sign up: http://code.google.com/apis/maps/signup.html

7. Set the apache log file and HUBzero CMS auth log file to rotate every night at 11:59PM 

	/var/log/apache2/hub-access.log TO /var/log/apache2/daily/hub-access.log-YYYYMMDD 
	/var/log/hubzero/cmsauth.log TO /var/log/hubzero/daily/cmsauth.log-YYYYMMDD

	Create file /etc/cron.d/crontab_root 
	permissions should be set to 
	-rw-r--r--   1 root root   68 2012-02-17 16:07 crontab_root

	put this entry in /etc/cron.d/crontab_root:

	59    23 * * *          root   /usr/sbin/logrotate /etc/logrotate.conf

	Make sure that logrotate isn't run from cron.daily: comment out the file /etc/cron.daily/logrotate

	#!/bin/sh

	#test -x /usr/sbin/logrotate || exit 0
	#/usr/sbin/logrotate /etc/logrotate.conf

	Create /etc/logrotate.d/hubzero
	/etc/logrotate.d/hubzero content:

	/var/log/hubzero/cmsauth.log /var/log/hubzero/cmsdebug.log {
    	    rotate 1000000
        	olddir /var/log/hubzero/daily
       		daily
        	nomail
        	dateext
        	nocompress
        	notifempty
        	missingok
	}

	Remove /etc/cron.d/hubzero-cms

	The hub-access logs are already being rotated daily by scripts under /etc/apache2 so nothing needed for them.

8. Execute the following script (as root) to create the database accounts, install metrics database tables and setup cron. PLEASE NOTE THAT THIS SCRIPT WILL DELETE ALL EXISTING DATA (IF ANY) IN THE METRICS DATABASE 

	/opt/hubzero/bin/metrics/_install$ sudo ./xsetup_hubmetrics 

9. Delete the directory "/opt/hubzero/bin/metrics/_install/" after the above script executes successfully

-------------------------------------------------------------------------
C. Verify Installation
-------------------------------------------------------------------------

1. Tests to run the next day:

	sudo ls /var/log/hubzero/imported/

	you should see cmsauth files ending in dates.

	sudo ls /var/log/apache2/imported/

	you should see hub-access files ending in dates.

2. Now check the database to see if the data got in.

	mysql> use myhub_metrics;
	select * from userlogin;
	select * from webhits;
	select * from web;
	select * from bot_useragents;

	There should be data in each table.

	Test that xlogfix_prep ran:

	/opt/hubzero/bin/metrics$ more includes/access.cfg
	you should see database login info.

	Test that xlogimport_tool_and_reg_user_data ran:

	mysql> select * from toolstart;
	There should be data with some positive values for cputime (the last column).

3. Create a link from your menu to the usage component in Joomla. There may not be any data in the Overview view.

	You can check for data in mysql

	That is because the __process_usage_metrics_summary.sh script only runs on the first of the month. Run it, and you'll see data.

	sudo /opt/hubzero/bin/metrics/__process_usage_metrics_summary.sh
