-- Per-test fixture for andmore-usage A/B comparison.
-- andmore-usage counts DISTINCT (ip, host) in metrics.web matching path
-- patterns derived from non-tool resources, per 3 periods (12, 14, 1), and
-- UPSERTs into hub.jos_resource_stats.

USE foo_test;

-- Three non-tool resources with three different path shapes that exercise
-- the get_paths branches.
INSERT INTO jos_resources (id, title, type, alias, path, published, standalone, publish_up)
VALUES
  -- topics path → matched via LIKE prefix
  (7001, 'Topic A',        5, 'topica',  'topics/intro',                1, 1, '2024-01-01 00:00:00'),
  -- bare path → matched via /site/resources/<path>
  (7002, 'Resource B',     5, 'resb',    'misc/page',                    1, 1, '2024-01-01 00:00:00'),
  -- type=7 (tool) → must be EXCLUDED
  (7003, 'A Tool',         7, 'tool1',   'tool1.xml',                    1, 1, '2024-01-01 00:00:00'),
  -- published=0 → must be EXCLUDED
  (7004, 'Unpublished',    5, 'unpub',   'unused/path',                  0, 1, '2024-01-01 00:00:00'),
  -- standalone=0 → must be EXCLUDED
  (7005, 'Sub Resource',   5, 'sub',     'child/sub',                    1, 0, '2024-01-01 00:00:00');

-- Metrics-side: web rows matching the various paths.
USE foo_metrics_test;

INSERT INTO web (datetime, ip, content) VALUES
  -- match Topic A (topics/intro%)
  ('2025-07-05 08:00:00', '1.1.1.1', 'topics/intro'),
  ('2025-07-05 08:01:00', '1.1.1.2', 'topics/intro/page1'),
  ('2025-07-06 09:00:00', '1.1.1.1', 'topics/intro/page2'),   -- duplicate ip
  -- match Resource B (/site/resources/misc/page)
  ('2025-07-10 10:00:00', '2.2.2.1', '/site/resources/misc/page'),
  ('2025-07-15 11:00:00', '2.2.2.2', '/site/resources/misc/page'),
  -- non-matching
  ('2025-07-12 12:00:00', '3.3.3.1', '/other/page'),
  -- out-of-window (Dec 2024 — counts for period 12 (rolling-12) and 14 (all-time))
  ('2024-12-10 14:00:00', '4.4.4.1', 'topics/intro/older');
