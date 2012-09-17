<?php
# @package      hubzero-metrics
# @file         hub_parameters.php
# @author       Swaroop Shivarajapura <swaroop@purdue.edu>
# @copyright    Copyright (c) 2011-2012 HUBzero Foundation, LLC.
# @license      http://www.gnu.org/licenses/lgpl-3.0.html LGPLv3
#
# Copyright (c) 2011-2012 HUBzero Foundation, LLC.
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

error_reporting(E_ALL & ~E_NOTICE);
@ini_set('display_errors','1');

define('n', "\n");
define('t', "\t");

$result = parse_ini_file('/etc/hubzero.conf.metrics', true);

if (!is_array($result))
{
    print date('Y-m-d H:is:s T').' '.$_SERVER['argv'][0].': '.'Hubzero Configuration file /etc/hubzero.conf missing or invalid'.n;
    die;
}

if (is_array($result['default']))
    $DocumentRoot = $result[$result['default']['site']]['DocumentRoot'];
else if (is_array($result[key($result)]))
    $DocumentRoot = $result[key($result)]['DocumentRoot'];
else
    $DocumentRoot = $result['DocumentRoot'];

define( '_JEXEC', 1 );
define('JPATH_BASE', $DocumentRoot);
define( 'DS', DIRECTORY_SEPARATOR );

require_once ( JPATH_BASE .DS.'includes'.DS.'defines.php' );
require_once ( JPATH_BASE .DS.'includes'.DS.'framework.php' );

$mainframe =& JFactory::getApplication('site');
$mainframe->initialise();

$jconfig    =& JFactory::getConfig();

$hub_db = $jconfig->getValue('config.db');
$hub_dir = JPATH_BASE;
$db_host = $jconfig->getValue('config.host');
$db_user = $jconfig->getValue('config.user');
$db_pass = $jconfig->getValue('config.password');
$db_prefix = $jconfig->getValue('config.dbprefix');

$metrics_db = '`'.$hub_db.'_metrics`';
$hub_db = '`'.$hub_db.'`';

require_once ( JPATH_BASE .DS.'hubconfiguration.php');
$hconfig = new HUBConfig();

$hubzero_ipgeo_url = $hconfig->hubzero_ipgeo_url;
$hub_key = $hconfig->hubzero_ipgeo_key;

$db_net_host = $hconfig->ipDBHost;
$db_net_user = $hconfig->ipDBUsername;
$db_net_pass = $hconfig->ipDBPassword;
$net_db = $hconfig->ipDBDatabase;

?>
