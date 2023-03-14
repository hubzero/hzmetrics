<?php
# @package      hubzero-metrics
# @file         func_andmore.php
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

function get_paths(&$db_hub, $resid_list) {

	global $hub_db, $db_prefix;

	$match_string = '';
	$sql = 'SELECT path, id FROM '.$hub_db.'.'.$db_prefix.'resources WHERE path <> "" AND id IN ('.$resid_list.') AND path NOT LIKE "http%"';
	$result = mysqli_query($db_hub, $sql);
	if($result) {
		if(mysqli_num_rows($result) > 0) {
			while($row = mysqli_fetch_assoc($result)) {
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
		echo mysqli_error($db_hub)." while executing ".$sql."\n";
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
	$result = mysqli_query($db_hub, $sql);
	if($result) {
		if(mysqli_num_rows($result) > 0) {
			while($row = mysqli_fetch_assoc($result)) {
				$count++;
				$child_resids .= $row['child_id'].',';
				array_push($child_resid_list, $row['child_id']);
			}
		}
	} else {
		echo mysqli_error($db_hub)." while executing ".$sql."\n";
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
	$result = mysqli_query($db_hub, $sql);
	if($result) {
		if(mysqli_num_rows($result) > 0) {
			while($row = mysqli_fetch_assoc($result)) {
				$list .= dbquote($row['id']).',';
			}
		}
	} else {
		$msg = mysqli_error($db_hub).' while executing '.$sql."\n";
		clean_exit($msg);
	}
	$elementid_list = rtrim($list,',');
	return $elementid_list;

}

?>
