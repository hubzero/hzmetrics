-- Per-test fixture for gen-tool-stats A/B comparison.
-- Reads jos_resources, jos_tool_version, sessionlog, joblog;
-- writes jos_resource_stats_tools + jos_resource_stats (per tool × per period).

USE foo_test;

-- Tools (type=7 = tool resources).  Two with non-empty aliases.
INSERT INTO jos_resources (id, title, type, alias, published, standalone, publish_up)
VALUES
  (5001, 'Aspect Notebook',    7, 'aspectnotebook', 1, 1, '2024-01-01 00:00:00'),
  (5002, 'Burnman Notebook',   7, 'burnmannotebook',1, 1, '2024-01-01 00:00:00'),
  -- type != 7 — must be excluded by gen-tool-stats
  (5003, 'Not A Tool',         5, 'doc',            1, 1, '2024-01-01 00:00:00'),
  -- type 7 but no alias — must be excluded
  (5004, 'Aliasless',           7, '',              1, 1, '2024-01-01 00:00:00');

-- Tool version aliases (used to expand the appname IN clause).
INSERT INTO jos_tool_version (id, toolname, instance, title, state)
VALUES
  (101, 'aspectnotebook',   'aspectnotebook', 'AN current',  1),
  (102, 'aspectnotebook',   'aspectnotebook_r1', 'AN r1',    1),
  (103, 'aspectnotebook',   'aspectnotebook_dev', 'AN dev',  1),  -- _dev: excluded
  (104, 'burnmannotebook',  'burnmannotebook', 'BN current', 1);

-- Sessions for the in-window month (2025-07).
INSERT INTO sessionlog (sessnum, username, remoteip, exechost, start, appname, walltime, cputime, viewtime) VALUES
  -- aspectnotebook: 3 distinct users, 4 sessions, 100/200/300 walltime sums
  (30001, 'alice', '1.1.1.1', 'host1', '2025-07-05 08:00:00', 'aspectnotebook',     100, 50, 25),
  (30002, 'bob',   '2.2.2.1', 'host1', '2025-07-12 09:00:00', 'aspectnotebook_r1', 200, 80, 30),
  (30003, 'carol', '3.3.3.1', 'host1', '2025-07-18 14:00:00', 'aspectnotebook',     150, 60, 20),
  (30004, 'alice', '1.1.1.1', 'host1', '2025-07-22 16:00:00', 'aspectnotebook',      50, 20, 10),
  -- burnmannotebook: 1 session
  (30005, 'dave',  '4.4.4.1', 'host2', '2025-07-15 12:00:00', 'burnmannotebook',    300, 200, 40),
  -- aspectnotebook_dev: must be EXCLUDED (alias is _dev)
  (30006, 'frank', '6.6.6.1', 'host1', '2025-07-20 11:00:00', 'aspectnotebook_dev',  20, 10, 5),
  -- gridstat — must be excluded
  (30007, 'gridstat','7.7.7.1','host1','2025-07-21 11:00:00', 'aspectnotebook',     999, 999, 100),
  -- out-of-month — for period=1 it must be excluded, but period=14 (all-time) counts it
  (30008, 'alice', '1.1.1.1', 'host1', '2024-12-10 08:00:00', 'aspectnotebook',     900, 400, 30);

INSERT INTO joblog (sessnum, job, superjob, event, start, walltime, cputime, ncpus, venue, status) VALUES
  (30001, 1, 0, 'started',   '2025-07-05 08:00:00', 100, 50,  1, 'default', 0),
  (30002, 1, 0, 'started',   '2025-07-12 09:00:00', 200, 80,  2, 'default', 0),
  (30003, 1, 0, 'started',   '2025-07-18 14:00:00', 150, 60,  1, 'default', 0),
  (30004, 1, 0, 'started',   '2025-07-22 16:00:00',  50, 20,  1, 'default', 0),
  (30005, 1, 0, 'started',   '2025-07-15 12:00:00', 300, 200, 4, 'default', 0),
  (30005, 2, 0, '[waiting]', '2025-07-15 12:00:00',  10,   0, 4, 'default', 0),
  (30007, 1, 0, 'started',   '2025-07-21 11:00:00', 999, 999, 1, 'default', 0),
  (30008, 1, 0, 'started',   '2024-12-10 08:00:00', 900, 400, 2, 'default', 0);

-- sessionlog_metrics is read by some queries — but in gen-tool-stats specifically
-- it's not used.  No metrics-side seeding needed.
