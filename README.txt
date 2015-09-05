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
