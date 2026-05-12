<?php
# @package      hubzero-metrics
# @file         hub_parameters.php
# @copyright    Copyright (c) 2011-2020 The Regents of the University of California.
# @license      http://opensource.org/licenses/MIT MIT
#
# Copyright (c) 2011-2020 The Regents of the University of California.
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
# HUBzero is a registered trademark of The Regents of the University of California.
#

error_reporting(E_ALL & ~E_NOTICE);
@ini_set('display_errors','1');

if(!ini_get('date.timezone'))
{
    exec("date +%Z",$output);
    date_default_timezone_set($output[0]);
}

# ---------------------------------------------------------------------------
# A/B test harness override.  When HZMETRICS_ACCESS_CFG is set we load the
# DB connection variables directly from that file (bare $var = 'value' lines,
# no <?php tag — same format the Perl scripts and hzmetrics.py parse) instead
# of going through /etc/hubzero.conf → configuration.php → hubconfiguration.php.
# Production behaviour is unchanged when the env var is unset.
# ---------------------------------------------------------------------------
$_harness_cfg = getenv('HZMETRICS_ACCESS_CFG');
if ($_harness_cfg) {
    $_text = file_get_contents($_harness_cfg);
    preg_match_all("/\\\$([A-Za-z_]\\w*)\\s*=\\s*'([^']*)'/", $_text, $_m, PREG_SET_ORDER);
    foreach ($_m as $_pair) { $GLOBALS[$_pair[1]] = $_pair[2]; }
    $metrics_db = '`'.$metrics_db.'`';
    $hub_db     = '`'.$hub_db.'`';
    $mw_db      = $hub_db;
    if (!isset($hubzero_ipgeo_url)) $hubzero_ipgeo_url = 'https://help.hubzero.org/ipinfo/v1';
    if (!isset($hub_key))           $hub_key           = '_HUBZERO_OPNSRC_V1_';
    return;
}

$DocumentRootKey = NULL;
$inicontents = file_get_contents('/etc/hubzero.conf');
$inicontents = preg_replace('/\[DEFAULT]/m','[default]', $inicontents);
$inicontents = preg_replace('/^\s*basedn\s*=\s*(.*)$/mi','basedn="$1"', $inicontents);
$inicontents = preg_replace('/^\s*syncuserdn\s*=\s*(.*)$/mi','syncuserdn="$1"', $inicontents);
$inicontents = preg_replace('/^\s*searchuserdn\s*=\s*(.*)$/mi','searchuserdn="$1"', $inicontents);
$inicontents = preg_replace('/^\s*adminuserdn\s*=\s*(.*)$/mi','adminuserdn="$1"', $inicontents);
$inicontents = preg_replace('/^\s*Org\s*=\s*(.*)$/m','Org="$1"', $inicontents);
$inicontents = preg_replace('/^\s*ldap.basedn\s*=\s*(.*)$/mi','ldap.basedn="$1"', $inicontents);
$inicontents = preg_replace('/^\s*ldap.syncuserdn\s*=\s*(.*)$/mi','ldap.syncuserdn="$1"', $inicontents);
$inicontents = preg_replace('/^\s*ldap.searchuserdn\s*=\s*(.*)$/mi','ldap.searchuserdn="$1"', $inicontents);
$inicontents = preg_replace('/^\s*ldap.adminuserdn\s*=\s*(.*)$/mi','ldap.adminuserdn="$1"', $inicontents);
$inicontents = preg_replace('/^\s*ldap.Org\s*=\s*(.*)$/m','ldap.Org="$1"', $inicontents);
$result = parse_ini_string($inicontents, true);

if (!is_array($result))
{
    print date('Y-m-d H:is:s T').' '.$_SERVER['argv'][0].': '.'Hubzero Configuration file /etc/hubzero.conf missing or invalid'."\n";
    die;
}

foreach ($result as $key=>$value) {
    if (!is_array($value)) {
        continue;
    }
    if (array_key_exists('documentroot', $value)) {
        $DocumentRootKey = 'documentroot';
    }
    if (array_key_exists('DocumentRoot', $value)) {
        $DocumentRootKey = 'DocumentRoot';  
    }
}

if (is_array($result['default']))
    $DocumentRoot = $result[$result['default']['site']][$DocumentRootKey];
else if (is_array($result[key($result)]))
    $DocumentRoot = $result[key($result)]['documentroot'];
else
    $DocumentRoot = $result['documentroot'];

// check for existence of additional config files:
if (!file_exists($DocumentRoot . '/configuration.php'))
{
    print date('Y-m-d H:is:s T').' '.$_SERVER['argv'][0].': '.'Hubzero Configuration file configuration.php missing from '.$DocumentRoot."\n";
    die;
}

# ---- configuration.php ----

require_once ( $DocumentRoot . '/configuration.php');
$jconfig = new JConfig();

$hub_db = $jconfig->db;
$hub_dir = $DocumentRoot;
$db_host = $jconfig->host;
$db_user = $jconfig->user;
$db_pass = $jconfig->password;
$db_prefix = $jconfig->dbprefix;

$metrics_db = '`'.$hub_db.'_metrics`';
$report_db = '`'.$hub_db.'_annualreport`';
$hub_db = '`'.$hub_db.'`';
$mw_db = $hub_db; // This should be read dynamically from database

# ---- hubconfiguration.php ----

// check for existence of additional config files:
if (!file_exists($DocumentRoot . '/hubconfiguration.php'))
{
    print date('Y-m-d H:is:s T').' '.$_SERVER['argv'][0].': '.'Hubzero Configuration file hubconfiguration.php missing from '.$DocumentRoot."\n";
    die;
}
require_once ( $DocumentRoot . '/hubconfiguration.php');
$hconfig = new HUBConfig();

if (isset($hconfig->hubzero_ipgeo_url)) {
    $hubzero_ipgeo_url = $hconfig->hubzero_ipgeo_url;
}
if (isset($hconfig->hubzero_ipgeo_key)) {
    $hub_key = $hconfig->hubzero_ipgeo_key;
}
if (isset($hconfig->ipDBHost)) {
    $db_net_host = $hconfig->ipDBHost;
}
if (isset($hconfig->ipDBUsername)) {
    $db_net_user = $hconfig->ipDBUsername;
}
if (isset($hconfig->ipDBPassword)) {
    $db_net_pass = $hconfig->ipDBPassword;
}
if (isset($hconfig->ipDBDatabase)) {
    $net_db = $hconfig->ipDBDatabase;
}

if (false) {
    echo "hub_db = $hub_db\n";
    echo "hub_dir = $hub_dir\n";
    echo "db_host = $db_host\n";
    echo "db_user = $db_user\n";
    echo "db_pass = $db_pass\n";
    echo "db_prefix = $db_prefix\n";
    echo "metrics_db = $metrics_db\n";
    echo "report_db = $report_db\n";
    echo "mw_db = $mw_db\n";
    echo "db_net_host = $db_net_host\n";
    echo "db_net_user = $db_net_user\n";
    echo "db_net_pass = $db_net_pass\n";
    echo "net_db = $net_db\n";
    echo "hubzero_ipgeo_url = $hubzero_ipgeo_url\n";
    echo "hub_key = $hub_key\n";
}
?>
