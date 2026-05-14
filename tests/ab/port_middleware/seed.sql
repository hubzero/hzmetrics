-- Per-test fixture for the middleware-wall / middleware-cpu A/B comparison.
-- Both copy joblog → toolstart with the JOIN through sessionlog.

USE foo_test;

INSERT INTO sessionlog (sessnum, username, remoteip, exechost, start, appname, walltime, cputime, viewtime) VALUES
  -- alice — joblog will have a positive walltime, no existing toolstart
  (10001, 'alice', '1.1.1.1', 'gilbreth', '2025-07-10 08:00:00', 'aspectnotebook', 0, 0, 0),
  -- bob — joblog will have walltime=-1 (running, no end), should be stored as -1
  (10002, 'bob',   '2.2.2.1', 'gilbreth', '2025-07-12 09:00:00', 'workspace',      0, 0, 0),
  -- carol — TWO joblog rows for the same session (only first matters per the legacy join)
  (10003, 'carol', '3.3.3.1', 'gilbreth', '2025-07-18 14:00:00', 'aspectdesktop',  0, 0, 0),
  -- dave — exists in toolstart with walltime=-1 already; legacy should UPDATE
  (10004, 'dave',  '4.4.4.1', 'gilbreth', '2025-07-20 11:00:00', 'workspace',      0, 0, 0),
  -- frank — sessionlog row excluded ([waiting] event in joblog)
  (10005, 'frank', '5.5.5.1', 'gilbreth', '2025-07-22 16:00:00', 'workspace',      0, 0, 0),
  -- gridstat — must be excluded by name filter
  (10006, 'gridstat', '6.6.6.1','gilbreth','2025-07-23 10:00:00','tool',           0, 0, 0),
  -- hctest_x — must be excluded by username LIKE 'hctest%'
  (10007, 'hctest_x', '7.7.7.1','gilbreth','2025-07-23 11:00:00','tool',           0, 0, 0);

INSERT INTO joblog (sessnum, job, superjob, event, start, walltime, cputime, ncpus, venue) VALUES
  (10001, 1, 0, 'started',     '2025-07-10 08:00:00', 120.4,  60.2,  1, 'default'),
  (10002, 1, 0, 'started',     '2025-07-12 09:00:00',  -1,   -1,    1, 'default'),
  (10003, 1, 0, 'started',     '2025-07-18 14:00:00', 200.5, 180.7, 1, 'default'),
  (10003, 2, 0, 'continued',   '2025-07-18 14:00:00', 200.5, 180.7, 1, 'default'),
  (10004, 1, 0, 'started',     '2025-07-20 11:00:00', 300.9, 250.1, 1, 'default'),
  (10005, 1, 0, '[waiting]',   '2025-07-22 16:00:00',  50.0,  10.0, 1, 'default'),
  (10006, 1, 0, 'started',     '2025-07-23 10:00:00',  10.0,   5.0, 1, 'default'),
  (10007, 1, 0, 'started',     '2025-07-23 11:00:00',  10.0,   5.0, 1, 'default');

-- Metrics-side: dave already has a row but with walltime=-1.
-- Legacy must UPDATE it to 301 (rounded from 300.9).
USE foo_metrics_test;

INSERT INTO toolstart (datetime, success, user, ip, tool, execunit, walltime, cputime) VALUES
  ('2025-07-20 11:00:00', 1, 'dave', '4.4.4.1', 'workspace', 'gilbreth', -1, -1);
