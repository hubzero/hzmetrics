/*M!999999\- enable the sandbox mode */ 
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `joblog` (
  `sessnum` bigint(20) unsigned NOT NULL DEFAULT 0,
  `job` int(10) unsigned NOT NULL DEFAULT 0,
  `superjob` bigint(20) unsigned NOT NULL DEFAULT 0,
  `event` varchar(40) NOT NULL DEFAULT '',
  `start` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  `walltime` double unsigned DEFAULT 0,
  `cputime` double unsigned DEFAULT 0,
  `ncpus` smallint(5) unsigned NOT NULL DEFAULT 0,
  `status` smallint(5) unsigned DEFAULT 0,
  `venue` varchar(80) NOT NULL DEFAULT '',
  `zone_id` int(11) NOT NULL DEFAULT 0,
  `end` timestamp /* mariadb-5.3 */ NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`sessnum`,`job`,`event`,`venue`),
  KEY `idx_sessnum` (`sessnum`),
  KEY `idx_event` (`event`),
  KEY `idx_job` (`job`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `sessionlog` (
  `sessnum` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `username` varchar(32) NOT NULL DEFAULT '',
  `remoteip` varchar(40) NOT NULL DEFAULT '',
  `remotehost` varchar(40) NOT NULL DEFAULT '',
  `exechost` varchar(40) NOT NULL DEFAULT '',
  `dispnum` int(10) unsigned DEFAULT 0,
  `start` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  `appname` varchar(80) NOT NULL DEFAULT '',
  `walltime` double unsigned DEFAULT 0,
  `viewtime` double unsigned DEFAULT 0,
  `cputime` double unsigned DEFAULT 0,
  `status` smallint(5) unsigned DEFAULT 0,
  `zone_id` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`sessnum`)
) ENGINE=MyISAM AUTO_INCREMENT=2427 DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_metrics_ipgeo_cache` (
  `ip` int(10) NOT NULL DEFAULT 0,
  `countrySHORT` char(2) NOT NULL DEFAULT '',
  `countryLONG` varchar(64) NOT NULL DEFAULT '',
  `ipREGION` varchar(128) NOT NULL DEFAULT '',
  `ipCITY` varchar(128) NOT NULL DEFAULT '',
  `ipLATITUDE` double DEFAULT NULL,
  `ipLONGITUDE` double DEFAULT NULL,
  `lookup_datetime` timestamp /* mariadb-5.3 */ NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`ip`),
  KEY `idx_lookup_datetime` (`lookup_datetime`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_resources` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `title` varchar(250) NOT NULL DEFAULT '',
  `type` int(11) NOT NULL DEFAULT 0,
  `logical_type` int(11) NOT NULL DEFAULT 0,
  `introtext` text NOT NULL DEFAULT '',
  `fulltxt` text NOT NULL DEFAULT '',
  `footertext` text NOT NULL DEFAULT '',
  `created` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  `created_by` int(11) NOT NULL DEFAULT 0,
  `modified` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  `modified_by` int(11) NOT NULL DEFAULT 0,
  `published` int(1) NOT NULL DEFAULT 0,
  `publish_up` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  `publish_down` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  `access` int(11) NOT NULL DEFAULT 0,
  `hits` int(11) NOT NULL DEFAULT 0,
  `path` varchar(200) NOT NULL DEFAULT '',
  `checked_out` int(11) NOT NULL DEFAULT 0,
  `checked_out_time` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  `standalone` tinyint(1) NOT NULL DEFAULT 0,
  `group_owner` varchar(250) NOT NULL DEFAULT '',
  `group_access` text DEFAULT NULL,
  `rating` decimal(2,1) NOT NULL DEFAULT 0.0,
  `times_rated` int(11) NOT NULL DEFAULT 0,
  `params` text DEFAULT NULL,
  `attribs` text DEFAULT NULL,
  `alias` varchar(100) NOT NULL DEFAULT '',
  `ranking` float NOT NULL DEFAULT 0,
  `master_doi` varchar(100) DEFAULT '',
  `license` char(255) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_license` (`license`),
  FULLTEXT KEY `ftidx_title` (`title`),
  FULLTEXT KEY `ftidx_introtext_fulltxt` (`introtext`,`fulltxt`),
  FULLTEXT KEY `ftidx_title_introtext_fulltxt` (`title`,`introtext`,`fulltxt`)
) ENGINE=MyISAM AUTO_INCREMENT=2233 DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_resource_assoc` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `parent_id` int(11) NOT NULL DEFAULT 0,
  `child_id` int(11) NOT NULL DEFAULT 0,
  `ordering` int(11) NOT NULL DEFAULT 0,
  `grouping` int(11) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `id` (`id`),
  KEY `idx_parent_id_child_id` (`parent_id`,`child_id`)
) ENGINE=MyISAM AUTO_INCREMENT=1415 DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_resource_stats` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `resid` bigint(20) NOT NULL DEFAULT 0,
  `restype` int(11) DEFAULT NULL,
  `users` bigint(20) DEFAULT NULL,
  `jobs` bigint(20) DEFAULT NULL,
  `avg_wall` int(20) DEFAULT NULL,
  `tot_wall` int(20) DEFAULT NULL,
  `avg_cpu` int(20) DEFAULT NULL,
  `tot_cpu` int(20) DEFAULT NULL,
  `datetime` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT -1,
  `processed_on` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uidx_resid_restype_datetime_period` (`resid`,`restype`,`datetime`,`period`)
) ENGINE=MyISAM AUTO_INCREMENT=41875 DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_resource_stats_tools` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `resid` bigint(20) NOT NULL DEFAULT 0,
  `restype` int(11) NOT NULL DEFAULT 0,
  `users` bigint(20) DEFAULT NULL,
  `sessions` bigint(20) DEFAULT NULL,
  `simulations` bigint(20) DEFAULT NULL,
  `jobs` bigint(20) DEFAULT NULL,
  `avg_wall` double unsigned DEFAULT 0,
  `tot_wall` double unsigned DEFAULT 0,
  `avg_cpu` double unsigned DEFAULT 0,
  `tot_cpu` double unsigned DEFAULT 0,
  `avg_view` double unsigned DEFAULT 0,
  `tot_view` double unsigned DEFAULT 0,
  `avg_wait` double unsigned DEFAULT 0,
  `tot_wait` double unsigned DEFAULT 0,
  `avg_cpus` int(20) DEFAULT NULL,
  `tot_cpus` int(20) DEFAULT NULL,
  `datetime` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT -1,
  `processed_on` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  PRIMARY KEY (`id`)
) ENGINE=MyISAM AUTO_INCREMENT=1591 DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_resource_stats_tools_topvals` (
  `id` bigint(20) NOT NULL DEFAULT 0,
  `top` tinyint(4) NOT NULL DEFAULT 0,
  `rank` tinyint(4) NOT NULL DEFAULT 0,
  `name` varchar(255) DEFAULT NULL,
  `value` bigint(20) NOT NULL DEFAULT 0
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_session` (
  `session_id` varchar(200) NOT NULL DEFAULT '',
  `client_id` tinyint(3) unsigned NOT NULL DEFAULT 0,
  `guest` tinyint(4) unsigned DEFAULT 1,
  `time` varchar(14) DEFAULT '',
  `data` mediumtext DEFAULT NULL,
  `userid` int(11) DEFAULT 0,
  `username` varchar(150) DEFAULT '',
  `usertype` varchar(50) DEFAULT '',
  `ip` varchar(15) DEFAULT NULL,
  PRIMARY KEY (`session_id`),
  KEY `whosonline` (`guest`,`usertype`),
  KEY `userid` (`userid`),
  KEY `time` (`time`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_session_geo` (
  `session_id` varchar(200) NOT NULL DEFAULT '0',
  `username` varchar(150) DEFAULT '',
  `time` varchar(14) DEFAULT '',
  `guest` tinyint(4) DEFAULT 1,
  `userid` int(11) DEFAULT 0,
  `ip` varchar(15) DEFAULT NULL,
  `host` varchar(128) DEFAULT NULL,
  `domain` varchar(128) DEFAULT NULL,
  `signed` tinyint(3) DEFAULT 0,
  `countrySHORT` char(2) DEFAULT NULL,
  `countryLONG` varchar(64) DEFAULT NULL,
  `ipREGION` varchar(128) DEFAULT NULL,
  `ipCITY` varchar(128) DEFAULT NULL,
  `ipLATITUDE` double DEFAULT NULL,
  `ipLONGITUDE` double DEFAULT NULL,
  `bot` tinyint(4) DEFAULT 0,
  PRIMARY KEY (`session_id`),
  KEY `idx_userid` (`userid`),
  KEY `idx_time` (`time`),
  KEY `idx_ip` (`ip`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_stats_topvals` (
  `id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `top` tinyint(4) NOT NULL DEFAULT 0,
  `datetime` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  `period` tinyint(4) NOT NULL DEFAULT 1,
  `rank` smallint(6) NOT NULL DEFAULT 0,
  `name` varchar(255) DEFAULT NULL,
  `value` bigint(20) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  KEY `idx_top` (`top`),
  KEY `idx_top_rank` (`top`,`rank`),
  KEY `idx_top_datetime` (`top`,`datetime`),
  KEY `idx_top_datetime_rank` (`top`,`datetime`,`rank`),
  KEY `idx_top_datetime_period` (`top`,`datetime`,`period`)
) ENGINE=MyISAM AUTO_INCREMENT=101761 DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_tool_version` (
  `id` int(10) NOT NULL AUTO_INCREMENT,
  `toolname` varchar(64) NOT NULL DEFAULT '',
  `instance` varchar(31) NOT NULL DEFAULT '',
  `title` varchar(127) NOT NULL DEFAULT '',
  `description` text DEFAULT NULL,
  `fulltxt` text DEFAULT NULL,
  `version` varchar(15) DEFAULT NULL,
  `revision` int(11) DEFAULT NULL,
  `toolaccess` varchar(15) DEFAULT NULL,
  `codeaccess` varchar(15) DEFAULT NULL,
  `wikiaccess` varchar(15) DEFAULT NULL,
  `state` int(15) DEFAULT NULL,
  `released_by` varchar(31) DEFAULT NULL,
  `released` datetime /* mariadb-5.3 */ DEFAULT NULL,
  `unpublished` datetime /* mariadb-5.3 */ DEFAULT NULL,
  `exportControl` varchar(16) DEFAULT NULL,
  `license` text DEFAULT NULL,
  `vnc_geometry` varchar(31) DEFAULT NULL,
  `vnc_depth` int(11) DEFAULT NULL,
  `vnc_timeout` int(11) DEFAULT NULL,
  `vnc_command` varchar(100) DEFAULT NULL,
  `mw` varchar(31) DEFAULT NULL,
  `toolid` int(11) DEFAULT NULL,
  `priority` int(11) DEFAULT NULL,
  `params` text DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uidx_toolname_instance` (`toolname`,`instance`),
  KEY `idx_instance` (`instance`)
) ENGINE=MyISAM AUTO_INCREMENT=35 DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_tool_version_alias` (
  `tool_version_id` int(11) NOT NULL DEFAULT 0,
  `alias` varchar(255) NOT NULL DEFAULT ''
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_user_profiles` (
  `id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `user_id` int(11) NOT NULL DEFAULT 0,
  `profile_key` varchar(100) NOT NULL DEFAULT '',
  `profile_value` text NOT NULL DEFAULT '',
  `ordering` int(11) NOT NULL DEFAULT 0,
  `access` int(10) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  KEY `idx_user_id` (`user_id`)
) ENGINE=MyISAM AUTO_INCREMENT=6434 DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci COMMENT='Simple user profile storage table';
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL DEFAULT '',
  `givenName` varchar(255) NOT NULL DEFAULT '',
  `middleName` varchar(255) NOT NULL DEFAULT '',
  `surname` varchar(255) NOT NULL DEFAULT '',
  `username` varchar(150) NOT NULL DEFAULT '',
  `email` varchar(100) NOT NULL DEFAULT '',
  `password` varchar(127) NOT NULL DEFAULT '',
  `usertype` varchar(25) NOT NULL DEFAULT '',
  `block` tinyint(4) NOT NULL DEFAULT 0,
  `approved` tinyint(4) NOT NULL DEFAULT 2,
  `sendEmail` tinyint(4) DEFAULT 0,
  `registerDate` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `registerIP` varchar(40) NOT NULL DEFAULT '',
  `lastvisitDate` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `activation` int(11) NOT NULL DEFAULT 0,
  `params` text NOT NULL DEFAULT '',
  `lastResetTime` datetime NOT NULL DEFAULT '0000-00-00 00:00:00' COMMENT 'Date of last password reset',
  `resetCount` int(11) NOT NULL DEFAULT 0 COMMENT 'Count of password resets since lastResetTime',
  `access` int(10) NOT NULL DEFAULT 0,
  `usageAgreement` tinyint(2) NOT NULL DEFAULT 0,
  `homeDirectory` varchar(255) NOT NULL DEFAULT '',
  `loginShell` varchar(255) NOT NULL DEFAULT '',
  `ftpShell` varchar(255) NOT NULL DEFAULT '',
  `secret` char(32) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uidx_username` (`username`),
  UNIQUE KEY `secret` (`secret`),
  KEY `usertype` (`usertype`),
  KEY `idx_name` (`name`),
  KEY `idx_block` (`block`),
  KEY `username` (`username`),
  KEY `email` (`email`)
) ENGINE=MyISAM AUTO_INCREMENT=2617 DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8mb4 */;
CREATE TABLE `jos_xprofiles` (
  `uidNumber` int(11) NOT NULL DEFAULT 0,
  `name` varchar(255) NOT NULL DEFAULT '',
  `username` varchar(150) NOT NULL DEFAULT '',
  `email` varchar(100) NOT NULL DEFAULT '',
  `registerDate` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  `gidNumber` varchar(11) NOT NULL DEFAULT '',
  `homeDirectory` varchar(255) NOT NULL DEFAULT '',
  `loginShell` varchar(255) NOT NULL DEFAULT '',
  `ftpShell` varchar(255) NOT NULL DEFAULT '',
  `userPassword` varchar(255) NOT NULL DEFAULT '',
  `gid` varchar(255) NOT NULL DEFAULT '',
  `orgtype` varchar(255) NOT NULL DEFAULT '',
  `organization` varchar(255) NOT NULL DEFAULT '',
  `countryresident` char(2) NOT NULL DEFAULT '',
  `countryorigin` char(2) NOT NULL DEFAULT '',
  `gender` varchar(255) NOT NULL DEFAULT '',
  `url` varchar(255) NOT NULL DEFAULT '',
  `reason` text NOT NULL DEFAULT '',
  `mailPreferenceOption` int(11) NOT NULL DEFAULT -1,
  `usageAgreement` int(11) NOT NULL DEFAULT 0,
  `modifiedDate` datetime /* mariadb-5.3 */ NOT NULL DEFAULT '0000-00-00 00:00:00',
  `emailConfirmed` int(11) NOT NULL DEFAULT 0,
  `regIP` varchar(255) NOT NULL DEFAULT '',
  `regHost` varchar(255) NOT NULL DEFAULT '',
  `nativeTribe` varchar(255) NOT NULL DEFAULT '',
  `phone` varchar(255) NOT NULL DEFAULT '',
  `proxyPassword` varchar(255) NOT NULL DEFAULT '',
  `proxyUidNumber` varchar(255) NOT NULL DEFAULT '',
  `givenName` varchar(255) NOT NULL DEFAULT '',
  `middleName` varchar(255) NOT NULL DEFAULT '',
  `surname` varchar(255) NOT NULL DEFAULT '',
  `picture` varchar(255) NOT NULL DEFAULT '',
  `vip` int(11) NOT NULL DEFAULT 0,
  `public` tinyint(2) NOT NULL DEFAULT 0,
  `params` text NOT NULL DEFAULT '',
  `note` text NOT NULL DEFAULT '',
  `shadowExpire` int(11) DEFAULT NULL,
  `locked` tinyint(4) NOT NULL DEFAULT 0,
  `orcid` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`uidNumber`),
  KEY `idx_username` (`username`),
  FULLTEXT KEY `ftidx_givenName_surname` (`givenName`,`surname`),
  FULLTEXT KEY `ftidx_name` (`name`),
  FULLTEXT KEY `ftidx_fullname` (`givenName`,`middleName`,`surname`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

