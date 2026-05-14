-- Per-test fixture for middleware-wall / middleware-cpu A/B comparison.
-- Targets every branch:
--   * INSERT new row vs UPDATE incomplete row
--   * Rounding edges: 200.5, 0.5, -0.5, 199.4, 199.6
--   * wall < 0 sentinel (-1)
--   * cpu == 0 boundary (UPDATE check is t.cputime <= 0)
--   * cpu == -0.5 (negative float — clamps to -1)
--   * [waiting] event (filtered for wall, included for cpu)
--   * username filters: gridstat (exact), hctest_x (LIKE 'hctest%'),
--                       hctest (LIKE 'hctest%' matches even bare),
--                       hctestlonger (LIKE wildcard), hctester (also matches)
--   * username NOT matching either (passes through)
--   * sessionlog row with no joblog (no INSERT)
--   * joblog with no matching sessionlog (no INSERT — INNER JOIN)
--   * duplicate sessionlog rows for same (datetime, user, ip) (legacy INSERTs all)

USE foo_test;

INSERT INTO sessionlog (sessnum, username, remoteip, exechost, start, appname, walltime, cputime, viewtime) VALUES
  (10001, 'alice',         '1.1.1.1', 'gilbreth', '2025-07-10 08:00:00', 'tool1', 0, 0, 0),
  (10002, 'bob',           '2.2.2.1', 'gilbreth', '2025-07-12 09:00:00', 'tool2', 0, 0, 0),
  (10003, 'carol',         '3.3.3.1', 'gilbreth', '2025-07-15 14:00:00', 'tool3', 0, 0, 0),
  (10004, 'dave',          '4.4.4.1', 'gilbreth', '2025-07-20 11:00:00', 'tool4', 0, 0, 0),
  (10005, 'frank',         '5.5.5.1', 'gilbreth', '2025-07-22 16:00:00', 'tool5', 0, 0, 0),
  (10006, 'gridstat',      '6.6.6.1', 'gilbreth', '2025-07-23 10:00:00', 'tool6', 0, 0, 0),
  (10007, 'hctest_x',      '7.7.7.1', 'gilbreth', '2025-07-23 11:00:00', 'tool7', 0, 0, 0),
  (10008, 'hctest',        '7.7.7.2', 'gilbreth', '2025-07-23 11:01:00', 'tool8', 0, 0, 0),
  (10009, 'hctestlonger',  '7.7.7.3', 'gilbreth', '2025-07-23 11:02:00', 'tool9', 0, 0, 0),
  (10010, 'hctester',      '7.7.7.4', 'gilbreth', '2025-07-23 11:03:00', 'toolA', 0, 0, 0),
  (10011, 'gridstatx',     '7.7.7.5', 'gilbreth', '2025-07-23 11:04:00', 'toolB', 0, 0, 0),  -- NOT excluded (exact match only)
  -- duplicate (datetime, user, ip) — legacy joins JOIN so both joblog entries can map
  (10012, 'helen',         '8.8.8.1', 'gilbreth', '2025-07-24 10:00:00', 'toolC', 0, 0, 0),
  (10013, 'helen',         '8.8.8.1', 'gilbreth', '2025-07-24 10:00:00', 'toolC', 0, 0, 0),
  -- joblog-less sessionlog row (no joblog rows reference 10014)
  (10014, 'iris',          '9.9.9.1', 'gilbreth', '2025-07-25 12:00:00', 'toolD', 0, 0, 0);

INSERT INTO joblog (sessnum, job, superjob, event, start, walltime, cputime, ncpus, venue) VALUES
  -- alice: positive walltime/cputime — INSERT new
  (10001, 1, 0, 'started', '2025-07-10 08:00:00', 120.4, 60.2,  1, 'default'),
  -- bob: walltime = -1, cputime = -1 — INSERT with both = -1
  (10002, 1, 0, 'started', '2025-07-12 09:00:00',  -1,   -1,    1, 'default'),
  -- carol: walltime = 200.5 (banker-rounding edge → must be 201), cputime = 199.6 (→ 200)
  (10003, 1, 0, 'started', '2025-07-15 14:00:00', 200.5, 199.6, 1, 'default'),
  -- dave: walltime = 199.4 (→ 199), cputime = 200.4 (→ 200)
  (10004, 1, 0, 'started', '2025-07-20 11:00:00', 199.4, 200.4, 1, 'default'),
  -- frank: [waiting] event — wall.pl SKIPS, cpu.pl includes
  (10005, 1, 0, '[waiting]', '2025-07-22 16:00:00', 50.5, 25.5, 1, 'default'),
  -- gridstat / hctest* — user filters
  (10006, 1, 0, 'started', '2025-07-23 10:00:00', 10.0, 5.0, 1, 'default'),
  (10007, 1, 0, 'started', '2025-07-23 11:00:00', 10.0, 5.0, 1, 'default'),
  (10008, 1, 0, 'started', '2025-07-23 11:01:00', 10.0, 5.0, 1, 'default'),
  (10009, 1, 0, 'started', '2025-07-23 11:02:00', 10.0, 5.0, 1, 'default'),
  (10010, 1, 0, 'started', '2025-07-23 11:03:00', 10.0, 5.0, 1, 'default'),
  -- gridstatx — NOT in exclude (exact match 'gridstat' only) — should INSERT
  (10011, 1, 0, 'started', '2025-07-23 11:04:00', 30.5, 20.5, 1, 'default'),
  -- helen: TWO joblog rows for the same (datetime, user, ip)
  (10012, 1, 0, 'started', '2025-07-24 10:00:00', 40.5, 30.5, 1, 'default'),
  (10013, 1, 0, 'started', '2025-07-24 10:00:00', 50.5, 40.5, 1, 'default'),
  -- orphan joblog (no matching sessionlog 99999) — INNER JOIN drops it
  (99999, 1, 0, 'started', '2025-07-26 09:00:00', 100, 50, 1, 'default');

-- Metrics side: pre-existing toolstart rows to verify the UPDATE branch.
USE foo_metrics_test;

INSERT INTO toolstart (datetime, success, user, ip, tool, execunit, walltime, cputime) VALUES
  -- carol: wall = -1 placeholder, cpu = 0 placeholder.  wall.pl updates wall to 201;
  -- cpu.pl (which uses <= 0) updates cpu to 200.
  ('2025-07-15 14:00:00', 1, 'carol', '3.3.3.1', 'tool3', 'gilbreth', -1, 0),
  -- dave: wall = -1, cpu = -1.  Both updates apply.
  ('2025-07-20 11:00:00', 1, 'dave', '4.4.4.1', 'tool4', 'gilbreth', -1, -1),
  -- existing row that's already complete — wall.pl and cpu.pl should leave it.
  ('2025-07-29 09:00:00', 1, 'jude', '12.0.0.1', 'toolE', 'gilbreth', 500, 400);
