# @package      hubzero-metrics
# @file         db_missing_hub_tables.sql
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

--
-- Unpublishing depreciated plugins from /usage
--

UPDATE jos_plugins SET published = 0 WHERE folder = "usage";
UPDATE jos_plugins SET published = 1 WHERE folder = "usage" AND element IN ("overview","maps");

--
-- Uninstalling depreciated plugins from /members and /resources
--

DELETE FROM jos_plugins WHERE folder = "members" AND element = "usages";
DELETE FROM jos_plugins WHERE folder = "resources" AND element = "usagenew";

--
-- Setting correct paraments for resource usage plugins
--

UPDATE jos_plugins SET params = "period=14 \nchart_path=/site/stats/chart_resources/ \nmap_path=/site/stats/resource_maps/" WHERE folder = "resources" AND element = "usage";
UPDATE jos_resource_types SET params="plg_citations=1\nplg_questions=1\nplg_recommendations=1\nplg_related=1\nplg_reviews=1\nplg_usage=1\nplg_versions=1\nplg_favorite=1\nplg_share=1\nplg_wishlist=1\nplg_supportingdocs=1" WHERE type="Tools";

--
-- Table structure for table `jos_resource_stats_tools_tops`
--

CREATE TABLE IF NOT EXISTS `jos_resource_stats_tools_tops` (
  `top` tinyint(4) NOT NULL default '0',
  `name` varchar(128) NOT NULL default '',
  `valfmt` tinyint(4) NOT NULL default '0',
  `size` tinyint(4) NOT NULL default '0',
  PRIMARY KEY  (`top`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8;

--
-- Inserting data into `jos_resource_stats_tools_tops`
--

LOCK TABLES `jos_resource_stats_tools_tops` WRITE;
INSERT IGNORE INTO `jos_resource_stats_tools_tops` VALUES (1,'Users By Country Of Residence',1,5),(2,'Top Domains By User Count',1,5),(3,'Users By Organization Type',1,5);

--
-- Table structure for table `jos_stats_tops`
--

CREATE TABLE IF NOT EXISTS `jos_stats_tops` (
  `id` tinyint(4) NOT NULL default '0',
  `name` varchar(128) NOT NULL default '',
  `valfmt` tinyint(4) NOT NULL default '0',
  `size` tinyint(4) NOT NULL default '0',
  PRIMARY KEY  (`id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8;

--
-- Inserting data into table `jos_stats_tops`
--

LOCK TABLES `jos_stats_tops` WRITE;
INSERT IGNORE INTO `jos_stats_tops` VALUES (1,'Top Tools by Ranking',1,5),(2,'Top Tools by Simulation Users',1,5),(3,'Top Tools by Interactive Sessions',1,5),(4,'Top Tools by Simulation Sessions',1,5),(5,'Top Tools by Simulation Runs',1,5),(6,'Top Tools by Simulation Wall Time',2,5),(7,'Top Tools by Simulation CPU Time',2,5),(8,'Top Tools by Simulation Interaction Time',2,5),(9,'Top Tools by Citations',1,5);
UNLOCK TABLES;

--
-- Table structure for table `jos_citations_secondary`
--

CREATE TABLE IF NOT EXISTS `jos_citations_secondary` (
  `id` int(11) NOT NULL auto_increment,
  `cid` int(11) NOT NULL,
  `sec_cits_cnt` int(11) default NULL,
  `search_string` tinytext,
  PRIMARY KEY  (`id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8;

--
-- Table structure for table `jos_session_geo`
--

CREATE TABLE IF NOT EXISTS `jos_session_geo` (
  `session_id` varchar(200) NOT NULL default '0',
  `username` varchar(150) default '',
  `time` varchar(14) default '',
  `guest` tinyint(4) default '1',
  `userid` int(11) default '0',
  `ip` varchar(15) default NULL,
  `host` varchar(128) default NULL,
  `domain` varchar(128) default NULL,
  `signed` tinyint(3) default '0',
  `countrySHORT` char(2) default NULL,
  `countryLONG` varchar(64) default NULL,
  `ipREGION` varchar(128) default NULL,
  `ipCITY` varchar(128) default NULL,
  `ipLATITUDE` double default NULL,
  `ipLONGITUDE` double default NULL,
  `bot` tinyint(4) default '0',
  PRIMARY KEY  (`session_id`),
  KEY `userid` (`userid`),
  KEY `time` (`time`),
  KEY `ip` (`ip`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8;

--
-- Table structure for table `jos_metrics_ipgeo_cache`
--

DROP TABLE IF EXISTS `jos_metrics_ipgeo_cache`;
CREATE TABLE IF NOT EXISTS `jos_metrics_ipgeo_cache` (
  `ip` int(10) NOT NULL DEFAULT '0000000000',
  `countrySHORT` char(2) NOT NULL DEFAULT '',
  `countryLONG` varchar(64) NOT NULL DEFAULT '',
  `ipREGION` varchar(128) NOT NULL DEFAULT '',
  `ipCITY` varchar(128) NOT NULL DEFAULT '',
  `ipLATITUDE` double DEFAULT NULL,
  `ipLONGITUDE` double DEFAULT NULL,
  `lookup_datetime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`ip`),
  KEY (`lookup_datetime`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8;
