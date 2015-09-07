<?php
# @package      hubzero-metrics
# @file         hub_parameters.php
# @author       Swaroop Shivarajapura Samek <swaroop@purdue.edu>
# @copyright    Copyright (c) 2011-2015 HUBzero Foundation, LLC.
# @license      http://opensource.org/licenses/MIT MIT
#
# Copyright (c) 2011-2015 HUBzero Foundation, LLC.
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
# HUBzero is a registered trademark of HUBzero Foundation, LLC.
#

error_reporting(E_ALL & ~E_NOTICE);
@ini_set('display_errors','1');

define('n', "\n");
define('t', "\t");

$inicontents = file_get_contents('/etc/hubzero.conf');
$inicontents = preg_replace('/\[DEFAULT]/m','[default]', $inicontents);
$inicontents = preg_replace('/^\s*BaseDN\s*=\s*(.*)$/m','BaseDN="$1"', $inicontents);
$inicontents = preg_replace('/^\s*Org\s*=\s*(.*)$/m','Org="$1"', $inicontents);
$result = parse_ini_string($inicontents, true);

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

$mainframe = JFactory::getApplication('site');
$mainframe->initialise();

$jconfig    = JFactory::getConfig();

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
