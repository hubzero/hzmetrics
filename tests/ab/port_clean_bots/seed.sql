-- Per-test fixture for the clean-bots A/B comparison.
-- Both legacy (xlogfix_clean.php) and new (hzmetrics.py clean-bots) walk the
-- exclude_list and DELETE matching rows.  This seed covers:
--   * domain-type match (exact)
--   * host-type match (LIKE wildcard)
--   * date-in-range vs date-out-of-range (verifies the per-week chunking)
--   * non-matching control rows that must survive

USE foo_metrics_test;

-- Two test exclude_list filters layered on top of the production-derived set.
-- (The reference set already contains '%.rcac.purdue.edu' as type=host.)
INSERT INTO exclude_list (filter, type, notes) VALUES
  ('test-bot.example.com',  'domain', 'ab-test fixture'),
  ('%.test-crawler.org',    'host',   'ab-test fixture');

INSERT INTO web (datetime, ip, content, host, domain) VALUES
  -- match 'test-bot.example.com' domain — both should DELETE
  ('2025-07-15 10:00:00', '1.1.1.1', '/x',  'foo.test-bot.example.com', 'test-bot.example.com'),
  ('2025-07-20 11:00:00', '1.1.1.2', '/y',  'bar.test-bot.example.com', 'test-bot.example.com'),
  -- match '%.test-crawler.org' host LIKE — both should DELETE
  ('2025-07-10 09:00:00', '2.2.2.1', '/a',  'node01.test-crawler.org',  'test-crawler.org'),
  ('2025-07-12 09:30:00', '2.2.2.2', '/b',  'node02.test-crawler.org',  'test-crawler.org'),
  -- match '%.rcac.purdue.edu' (production-supplied host filter) — should DELETE
  ('2025-07-22 14:00:00', '3.3.3.1', '/c',  'login.rcac.purdue.edu',    'purdue.edu'),
  -- no match, in range — should SURVIVE
  ('2025-07-05 08:00:00', '4.4.4.1', '/k1', 'web.example.com',          'example.com'),
  ('2025-07-25 16:00:00', '4.4.4.2', '/k2', 'host.user.net',            'user.net'),
  -- out of range (June), would match if processed — should SURVIVE
  ('2025-06-30 23:59:00', '5.5.5.1', '/o1', 'foo.test-bot.example.com', 'test-bot.example.com'),
  -- out of range (August), would match — should SURVIVE
  ('2025-08-01 00:01:00', '5.5.5.2', '/o2', 'foo.test-bot.example.com', 'test-bot.example.com');

INSERT INTO websessions (id, datetime, ip, host, duration, domain, jobs, webevents) VALUES
  -- match domain — DELETE
  (101, '2025-07-15 10:00:00', '1.1.1.1', 'foo.test-bot.example.com', 1200, 'test-bot.example.com', 0, 5),
  (102, '2025-07-20 11:00:00', '1.1.1.2', 'bar.test-bot.example.com', 800,  'test-bot.example.com', 0, 3),
  -- match host LIKE — DELETE
  (103, '2025-07-10 09:00:00', '2.2.2.1', 'node01.test-crawler.org',  900,  'test-crawler.org',     0, 4),
  (104, '2025-07-12 09:30:00', '2.2.2.2', 'node02.test-crawler.org',  600,  'test-crawler.org',     0, 2),
  -- match rcac.purdue.edu — DELETE
  (105, '2025-07-22 14:00:00', '3.3.3.1', 'login.rcac.purdue.edu',    400,  'purdue.edu',           0, 1),
  -- no match — SURVIVE
  (106, '2025-07-05 08:00:00', '4.4.4.1', 'web.example.com',          1500, 'example.com',          1, 8),
  (107, '2025-07-25 16:00:00', '4.4.4.2', 'host.user.net',            300,  'user.net',             0, 2),
  -- out of range — SURVIVE
  (108, '2025-06-30 23:59:00', '5.5.5.1', 'foo.test-bot.example.com', 600,  'test-bot.example.com', 0, 1),
  (109, '2025-08-01 00:01:00', '5.5.5.2', 'foo.test-bot.example.com', 400,  'test-bot.example.com', 0, 1);
