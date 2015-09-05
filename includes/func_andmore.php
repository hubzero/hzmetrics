<?php
# @package      hubzero-metrics
# @file         func_andmore.php
# @author       Swaroop Shivarajapura Samek <swaroop@purdue.edu>
# @copyright    Copyright (c) 2011-2015 HUBzero Foundation, LLC.
# @license      http://www.gnu.org/licenses/lgpl-3.0.html LGPLv3
#
# Copyright (c) 2011-2015 HUBzero Foundation, LLC.
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

function get_paths(&$db_hub, $resid_list) {

    global $hub_db, $db_prefix;

	$match_string = '';
	$sql = 'SELECT path, id FROM '.$hub_db.'.'.$db_prefix.'resources WHERE path <> "" AND id IN ('.$resid_list.') AND path NOT LIKE "http%"';
	$result = mysql_query($sql, $db_hub);
	if($result) {
   		if(mysql_num_rows($result) > 0) {
       		while($row = mysql_fetch_assoc($result)) {
				$path = $row['path'];
				$path = str_replace(' ','%20', $path);
				# print $path."\n";
				$resid = $row['id'];
	            if (preg_match('/^([0-9]+)(.+)$/', $path)) {
					$path_ = explode("/",$path);
					if ($path_[sizeof($path_)-1] == "viewer.swf") {

						// content = "2008/09/05478/viewer.swf"
						// translates to /site/resources/2008/09/05478/% 
						# $path4 = '/site/resources/'.$path;
						$path4 = '/site/resources/'.substr($path, 0, strrpos($path, 'viewer.swf')).'%';

						# todo - This will not work as play is on the child resource. not parent
						// "%/play?resid=7288%"
						$path5 = '%/play?resid='.ltrim($path_[sizeof($path_)-2],0)."%";

						$match_string .= 'content LIKE "'.$path4.'" OR ';
					} else {
						// content = "/site/resources/2010/07/09423/2010.07.21-Lundstrom-NT101.pdf"
						$path1 = '/site/resources/'.$path;

						// content = "/resources/9423/download/2010.07.21-Lundstrom-NT101.pdf"
						$path2 = '/resources/'.ltrim($path_[sizeof($path_)-2],0).'/download/'.$path_[sizeof($path_)-1];

						# todo - Never seen this pattern
						// content = "/resources/09423/download/2010.07.21-Lundstrom-NT101.pdf"
						$path3 = '%/'.$path_[sizeof($path_)-2].'/download/'.$path_[sizeof($path_)-1];

						$match_string .= 'content = "'.$path1.'" OR content = "'.$path2.'" OR ';
					}
			    } else {
	            	 if ( (preg_match('/^\/resources\/(.+)$/', $path)) || (preg_match('/^\/site\/resources\/(.+)$/', $path)) || (preg_match('/^\/local\/(.+)$/', $path)) || (preg_match('/^\/site\/(.+)$/', $path)) ) {
						$match_string .= 'content = "'.$path.'" OR ';

			    	} else if ( preg_match('/^\/topics\/(.+)$/', $path) ) {

						$match_string .= 'content LIKE "'.$path.'%" OR ';

			    	} else if ( preg_match('/^lm\/(.+)$/', $path) ) {

						if ( preg_match('/^lm\/(.+)\/(.+)\.(.+)$/', $path) ) {
						    $match_string .= 'content LIKE "'.substr($path, 0, strrpos($path, '/')).'/%" OR ';
						} else {
						    $match_string .= 'content = "/site/resources/'.$path.'" OR ';
						}

					} else {

						$match_string .= 'content = "/site/resources/'.$path.'" OR ';

					}
				}
			}
		}
	} else {
		echo mysql_error($db_hub)." while executing ".$sql."\n";
		die;
	}

	$match_string = rtrim($match_string, " OR ");
	#print $match_string."\n";
	return $match_string;
}

function get_child_resources(&$db_hub, $resid, &$child_resid_list) {

    global $hub_db, $db_prefix;

    $count = 0;
    $child_resids = '';
	
	// todo remove this below line outside of this method
	#array_push($child_resid_list, $resid);

	$already_a_child = '';
	foreach (array_unique($child_resid_list) as $tmp_id) {
		$already_a_child .= $tmp_id.',';
	}
	$already_a_child = rtrim($already_a_child,',');

    # $sql = 'SELECT DISTINCT child_id FROM '.$hub_db.'.'.$db_prefix.'resource_assoc WHERE parent_id IN ('.$resid.')';
	# Fix for segmentation fault problem
    $sql = 'SELECT DISTINCT child_id FROM '.$hub_db.'.'.$db_prefix.'resource_assoc WHERE parent_id IN ('.$resid.') AND child_id NOT IN ('.$already_a_child.')';
    $result = mysql_query($sql, $db_hub);
    if($result) {
        if(mysql_num_rows($result) > 0) {
            while($row = mysql_fetch_assoc($result)) {
                $count++;
                $child_resids .= $row['child_id'].',';
                array_push($child_resid_list, $row['child_id']);
            }
        }
    } else {
        echo mysql_error($db_hub)." while executing ".$sql."\n";
        die;
    }
    if ($count) {
        $child_resids = rtrim($child_resids,',');
        get_child_resources($db_hub, $child_resids, $child_resid_list);
    }
}

function get_elementids(&$db_hub, $resid_list) {

	global $metrics_db;

    $list = '';
    $sql = 'SELECT id FROM '.$metrics_db.'.elements WHERE resourceid IN ('.$resid_list.')';
    $result = mysql_query($sql, $db_hub);
    if($result) {
        if(mysql_num_rows($result) > 0) {
            while($row = mysql_fetch_assoc($result)) {
                $list .= dbquote($row['id']).',';
            }
        }
    } else {
		$msg = mysql_error($db_hub).' while executing '.$sql.n;
		clean_exit($msg);
    }
    $elementid_list = rtrim($list,',');
    return $elementid_list;

}

?>
