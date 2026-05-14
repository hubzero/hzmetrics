-- Per-test fixture for gen-tool-stats A/B comparison.
-- Targets every SELECT predicate the legacy makes against sessionlog/joblog:
--   * j.event <> '[waiting]'  (count of sessions with at least one job)
--   * j.event <> 'application' AND j.superjob = 0  (count of "real" jobs)
--   * j.event = '[waiting]' AND j.job > 0  (separate wait-time SUM)
--   * j.job > 0 vs j.job = 0  (real jobs vs accounting rows)
--   * username <> 'gridstat' AND username NOT LIKE 'hctest%'
--   * tool with NO sessions (existing INSERT but 0 counts)
--   * appname in tool_version expansion (alias → instance set)
--   * cross-month rows for period 14 (all-time)
--   * UPSERT branch: existing jos_resource_stats_tools row gets UPDATEd

USE foo_test;

INSERT INTO jos_resources (id, title, type, alias, published, standalone, publish_up) VALUES
  (5001, 'Aspect Notebook',   7, 'aspectnotebook', 1, 1, '2024-01-01 00:00:00'),
  (5002, 'Burnman Notebook',  7, 'burnmannotebook', 1, 1, '2024-01-01 00:00:00'),
  -- Tool with NO sessions — still gets processed but produces zero counts
  (5003, 'Empty Tool',        7, 'emptytool',      1, 1, '2024-01-01 00:00:00'),
  -- Excluded controls:
  (5004, 'Not A Tool',        5, 'doc',            1, 1, '2024-01-01 00:00:00'),    -- type != 7
  (5005, 'Aliasless',         7, '',               1, 1, '2024-01-01 00:00:00');    -- alias = '' → excluded

INSERT INTO jos_tool_version (id, toolname, instance, title, state) VALUES
  (101, 'aspectnotebook',   'aspectnotebook',     'AN',    1),
  (102, 'aspectnotebook',   'aspectnotebook_r1',  'AN r1', 1),
  (103, 'aspectnotebook',   'aspectnotebook_dev', 'AN dev',1),  -- _dev EXCLUDED
  (104, 'burnmannotebook',  'burnmannotebook',    'BN',    1),
  (105, 'emptytool',        'emptytool',          'ET',    1);

-- Sessions for aspectnotebook (alias + r1 instance)
INSERT INTO sessionlog (sessnum, username, remoteip, exechost, start, appname, walltime, cputime, viewtime) VALUES
  -- 4 sessions, 3 distinct users — count(DISTINCT username) = 3 for aspect
  (30001, 'alice', '1.1.1.1', 'host1', '2025-07-05 08:00:00', 'aspectnotebook',     100, 50, 25),
  (30002, 'bob',   '2.2.2.1', 'host1', '2025-07-12 09:00:00', 'aspectnotebook_r1', 200, 80, 30),
  (30003, 'carol', '3.3.3.1', 'host1', '2025-07-18 14:00:00', 'aspectnotebook',     150, 60, 20),
  (30004, 'alice', '1.1.1.1', 'host1', '2025-07-22 16:00:00', 'aspectnotebook',      50, 20, 10),
  -- burnmannotebook: 1 session
  (30005, 'dave',  '4.4.4.1', 'host2', '2025-07-15 12:00:00', 'burnmannotebook',    300, 200, 40),
  -- aspectnotebook_dev: EXCLUDED (alias is _dev)
  (30006, 'frank', '6.6.6.1', 'host1', '2025-07-20 11:00:00', 'aspectnotebook_dev',  20, 10, 5),
  -- gridstat — EXCLUDED by exact-match user filter
  (30007, 'gridstat','7.7.7.1','host1','2025-07-21 11:00:00', 'aspectnotebook',     999, 999, 100),
  -- hctest123 — EXCLUDED by LIKE 'hctest%'
  (30008, 'hctest123','7.7.7.2','host1','2025-07-21 11:01:00', 'aspectnotebook',     999, 999, 100),
  -- gridstatx — NOT excluded (exact-match only on 'gridstat')
  (30009, 'gridstatx','7.7.7.3','host1','2025-07-21 11:02:00', 'aspectnotebook',     10, 5, 1),
  -- out-of-month (Dec 2024) — included in period=14 (all-time) only
  (30010, 'alice', '1.1.1.1', 'host1', '2024-12-10 08:00:00', 'aspectnotebook',     900, 400, 30),
  -- cross-month (June 2025) — included in period=12 (rolling-12)
  (30011, 'erin',  '8.8.8.1', 'host1', '2025-06-15 10:00:00', 'aspectnotebook',     500, 300, 50);

INSERT INTO joblog (sessnum, job, superjob, event, start, walltime, cputime, ncpus, venue, status) VALUES
  (30001, 1, 0, 'started',     '2025-07-05 08:00:00', 100, 50,  1, 'default', 0),
  -- second job on same sess — counted (j.job > 0) but only one [waiting]
  (30001, 2, 0, '[waiting]',   '2025-07-05 08:01:00',  10,  0,  1, 'default', 0),
  (30001, 3, 0, 'application', '2025-07-05 08:02:00',   5,  2,  1, 'default', 0),  -- event=application excluded from sim count
  (30002, 1, 0, 'started',     '2025-07-12 09:00:00', 200, 80,  2, 'default', 0),
  (30003, 1, 0, 'started',     '2025-07-18 14:00:00', 150, 60,  1, 'default', 0),
  (30004, 1, 0, 'started',     '2025-07-22 16:00:00',  50, 20,  1, 'default', 0),
  (30005, 1, 0, 'started',     '2025-07-15 12:00:00', 300, 200, 4, 'default', 0),
  (30005, 2, 0, '[waiting]',   '2025-07-15 12:01:00',  10,   0, 4, 'default', 0),
  -- superjob non-zero row — counted differently
  (30005, 3, 1, 'started',     '2025-07-15 12:02:00',   5,   3, 4, 'default', 0),
  (30009, 1, 0, 'started',     '2025-07-21 11:02:00',  10, 5, 1, 'default', 0),
  (30010, 1, 0, 'started',     '2024-12-10 08:00:00', 900, 400, 2, 'default', 0),
  (30011, 1, 0, 'started',     '2025-06-15 10:00:00', 500, 300, 3, 'default', 0);

-- Pre-existing jos_resource_stats_tools row → exercises UPDATE (UPSERT) branch
INSERT INTO jos_resource_stats_tools (resid, restype, users, sessions, simulations, jobs,
                                       avg_wall, tot_wall, avg_cpu, tot_cpu,
                                       avg_view, tot_view, avg_wait, tot_wait,
                                       avg_cpus, tot_cpus, datetime, period, processed_on)
VALUES (5001, 7, 999, 999, 999, 999, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '2025-07-00 00:00:00', 1, '2025-01-01 00:00:00');
