-- Per-test fixture for andmore-usage A/B comparison.
-- get_paths() in legacy/includes/func_andmore.php has 8+ branches —
-- each path shape produces a different LIKE / equality match string.
-- This seed targets each branch with at least one resource + matching
-- web row so a divergence shows up immediately.

USE foo_test;

-- Non-tool resources (type != 7), published=1, standalone=1.
INSERT INTO jos_resources (id, title, type, alias, path, published, standalone, publish_up) VALUES
  -- Branch 1: numeric prefix path, ending viewer.swf — special case
  (7100, 'SWF Viewer',  5, 'swf',  '2010/07/05478/viewer.swf', 1, 1, '2024-01-01 00:00:00'),
  -- Branch 2: numeric prefix, regular file extension
  (7101, 'PDF Doc',     5, 'pdf',  '2010/07/09423/lecture.pdf', 1, 1, '2024-01-01 00:00:00'),
  -- Branch 3: /resources/ prefix
  (7102, 'ResPrefix',   5, 'rp',   '/resources/some-page', 1, 1, '2024-01-01 00:00:00'),
  -- Branch 4: /site/resources/ prefix
  (7103, 'SiteRes',     5, 'sr',   '/site/resources/page2', 1, 1, '2024-01-01 00:00:00'),
  -- Branch 5: /local/ prefix
  (7104, 'Local',       5, 'lc',   '/local/intranet/page', 1, 1, '2024-01-01 00:00:00'),
  -- Branch 6: /site/ prefix
  (7105, 'SitePage',    5, 'sp',   '/site/about/people', 1, 1, '2024-01-01 00:00:00'),
  -- Branch 7: /topics/ prefix → uses LIKE wildcard append
  (7106, 'TopicA',      5, 'topica', '/topics/intro', 1, 1, '2024-01-01 00:00:00'),
  -- Branch 8: lm/ prefix with file extension → strip filename, append %
  (7107, 'LMFile',      5, 'lmf',  'lm/course/chapter.html', 1, 1, '2024-01-01 00:00:00'),
  -- Branch 9: lm/ prefix WITHOUT file extension → /site/resources/lm/...
  (7108, 'LMDir',       5, 'lmd',  'lm/course', 1, 1, '2024-01-01 00:00:00'),
  -- Branch 10: bare path (default catch-all) → /site/resources/<path>
  (7109, 'Bare',        5, 'br',   'misc/page', 1, 1, '2024-01-01 00:00:00'),
  -- Branch 11: path starts with "http" → excluded by get_paths SELECT WHERE
  (7110, 'HttpExcluded',5, 'he',   'http://example.com/page', 1, 1, '2024-01-01 00:00:00'),
  -- Branch 12: path is empty — excluded
  (7111, 'EmptyPath',   5, 'ep',   '', 1, 1, '2024-01-01 00:00:00'),
  -- Branch 13: parent resource with one child (resource_assoc) — child path is included
  (7200, 'Parent',      5, 'par',  'parent/page', 1, 1, '2024-01-01 00:00:00'),
  (7201, 'Child',       5, 'chl',  'child/page',  1, 0, '2024-01-01 00:00:00'),
  -- Excluded controls:
  (7300, 'AsTool',      7, 't',    'tool.xml', 1, 1, '2024-01-01 00:00:00'),    -- type=7 EXCLUDED
  (7301, 'Unpub',       5, 'up',   'unused/path', 0, 1, '2024-01-01 00:00:00'), -- published=0 EXCLUDED
  (7302, 'NotStand',    5, 'ns',   'sub/page', 1, 0, '2024-01-01 00:00:00');    -- standalone=0 EXCLUDED

-- Parent/child resource_assoc (Parent has Child as descendant)
INSERT INTO jos_resource_assoc (parent_id, child_id, ordering, grouping) VALUES
  (7200, 7201, 0, 0);

-- Metrics side: web rows matching each path shape.
USE foo_metrics_test;

-- NB: host must be non-NULL — COUNT(DISTINCT ip, host) skips rows with any NULL.
INSERT INTO web (datetime, ip, content, host) VALUES
  -- Branch 1: viewer.swf at /site/resources/<path-without-viewer.swf>%
  ('2025-07-01 08:00:00', '1.1.1.1', '/site/resources/2010/07/05478/play', ''),
  ('2025-07-01 08:00:01', '1.1.1.2', '/site/resources/2010/07/05478/file.json', ''),
  ('2025-07-01 08:00:02', '1.1.1.3', '/some/other/path', ''),  -- non-match
  -- Branch 2: PDF — both /site/resources/2010/07/09423/lecture.pdf
  --                  and /resources/9423/download/lecture.pdf (legacy adds both)
  ('2025-07-02 09:00:00', '2.1.1.1', '/site/resources/2010/07/09423/lecture.pdf', ''),
  ('2025-07-02 09:00:01', '2.1.1.2', '/resources/9423/download/lecture.pdf', ''),
  -- Branch 3: /resources/some-page — exact equality
  ('2025-07-03 10:00:00', '3.1.1.1', '/resources/some-page', ''),
  ('2025-07-03 10:00:01', '3.1.1.2', '/resources/some-page/other', ''),  -- non-match (no trailing wildcard)
  -- Branch 4: /site/resources/page2 — exact
  ('2025-07-04 11:00:00', '4.1.1.1', '/site/resources/page2', ''),
  -- Branch 5: /local/intranet/page — exact
  ('2025-07-05 12:00:00', '5.1.1.1', '/local/intranet/page', ''),
  -- Branch 6: /site/about/people — exact
  ('2025-07-06 13:00:00', '6.1.1.1', '/site/about/people', ''),
  -- Branch 7: /topics/intro% — LIKE wildcard
  ('2025-07-07 14:00:00', '7.1.1.1', '/topics/intro', ''),
  ('2025-07-07 14:00:01', '7.1.1.2', '/topics/intro/page1', ''),
  ('2025-07-07 14:00:02', '7.1.1.3', '/topics/intro-deep/page2', ''),  -- LIKE 'intro%' matches "intro-deep"
  ('2025-07-07 14:00:03', '7.1.1.4', '/topics/other', ''),  -- non-match
  -- Branch 8: lm/course/% (strip chapter.html, append %)
  ('2025-07-08 15:00:00', '8.1.1.1', 'lm/course/intro.html', ''),
  ('2025-07-08 15:00:01', '8.1.1.2', 'lm/course/quiz.html', ''),
  ('2025-07-08 15:00:02', '8.1.1.3', 'lm/different/x.html', ''),  -- non-match
  -- Branch 9: lm/course (no extension) → /site/resources/lm/course
  ('2025-07-09 16:00:00', '9.1.1.1', '/site/resources/lm/course', ''),
  -- Branch 10: misc/page — /site/resources/misc/page
  ('2025-07-10 17:00:00', '10.1.1.1', '/site/resources/misc/page', ''),
  -- Parent + child paths
  ('2025-07-11 18:00:00', '11.1.1.1', '/site/resources/parent/page', ''),
  ('2025-07-11 18:00:01', '11.1.1.2', '/site/resources/child/page', ''),  -- via resource_assoc → counts for parent
  -- non-matching control
  ('2025-07-12 19:00:00', '12.1.1.1', '/elsewhere/not-tracked', '');
