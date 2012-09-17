<?php

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
