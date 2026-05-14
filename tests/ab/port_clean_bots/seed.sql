-- Per-test fixture for the clean-bots A/B comparison.
-- Targets every filter shape and date-boundary the legacy SQL touches.

USE foo_metrics_test;

-- Layered test filters on top of the production-derived reference set.
-- (Reference already includes %.rcac.purdue.edu as type=host.)
INSERT INTO exclude_list (filter, type, notes) VALUES
  -- domain (exact match)
  ('test-bot.example.com', 'domain', 'ab-test fixture'),
  ('lone.example.org',     'domain', 'second domain filter'),
  -- host (SQL LIKE pattern)
  ('%.test-crawler.org',   'host',   'wildcard ab fixture'),
  ('exact-host.example.io','host',   'no-wildcard host filter — exact LIKE match'),
  ('%scanner%',            'host',   'mid-string LIKE pattern');

-- web rows exercising every branch:
INSERT INTO web (datetime, ip, content, host, domain) VALUES
  -- 1) domain exact match — DELETE
  ('2025-07-05 08:00:00', '1.1.1.1', '/a',  'foo.test-bot.example.com', 'test-bot.example.com'),
  ('2025-07-05 08:01:00', '1.1.1.2', '/b',  'bar.test-bot.example.com', 'test-bot.example.com'),
  -- 2) different domain that's NOT an exact match — KEEP
  ('2025-07-05 08:02:00', '1.1.1.3', '/c',  'sub.test-bot.example.com', 'sub.test-bot.example.com'),
  -- 3) lone domain filter (single instance)
  ('2025-07-10 09:00:00', '1.2.1.1', '/d',  'mail.lone.example.org', 'lone.example.org'),
  -- 4) wildcard host LIKE — DELETE (prefix.crawler.org)
  ('2025-07-12 10:00:00', '2.2.2.1', '/e',  'node01.test-crawler.org', 'test-crawler.org'),
  ('2025-07-12 10:01:00', '2.2.2.2', '/f',  'node02.test-crawler.org', 'test-crawler.org'),
  -- 5) exact-host filter (LIKE with no wildcards) — DELETE only if host EQUALS
  ('2025-07-14 11:00:00', '2.3.1.1', '/g',  'exact-host.example.io', 'example.io'),
  ('2025-07-14 11:01:00', '2.3.1.2', '/h',  'other.exact-host.example.io', 'exact-host.example.io'),  -- doesn't equal — KEEP
  -- 6) mid-string LIKE %scanner% — DELETE rows where host contains 'scanner'
  ('2025-07-16 12:00:00', '2.4.1.1', '/i',  'mybigscannerhost.net', 'mybigscannerhost.net'),
  ('2025-07-16 12:01:00', '2.4.1.2', '/j',  'no-bots-here.net',    'no-bots-here.net'),  -- KEEP
  -- 7) production rcac filter — DELETE
  ('2025-07-22 14:00:00', '3.3.3.1', '/k',  'login.rcac.purdue.edu', 'purdue.edu'),
  -- 8) host NULL — should be left alone (no host to match)
  ('2025-07-23 15:00:00', '4.4.4.1', '/l',  NULL,                   ''),
  -- 9) host = '' — left alone
  ('2025-07-23 15:01:00', '4.4.4.2', '/m',  '',                     ''),
  -- 10) date boundary (Jun 30 23:59:00 — captured by day-before-month chunk start)
  ('2025-06-30 23:59:00', '5.5.5.1', '/n',  'foo.test-bot.example.com', 'test-bot.example.com'),
  -- 11) date boundary (Jun 30 00:00:00 — < day-before-month, KEEP)
  ('2025-06-30 00:00:00', '5.5.5.2', '/o',  'foo.test-bot.example.com', 'test-bot.example.com'),
  -- 12) date boundary (Aug 1 00:00:00 — exactly at month-end + 1 day, > so KEEP per <= end)
  ('2025-08-01 00:00:00', '5.5.5.3', '/p',  'foo.test-bot.example.com', 'test-bot.example.com'),
  -- 13) date boundary (Aug 1 00:00:01 — KEEP)
  ('2025-08-01 00:00:01', '5.5.5.4', '/q',  'foo.test-bot.example.com', 'test-bot.example.com'),
  -- 14) Week-chunk seam: 2025-07-07 23:59:59 — last second of week 0
  ('2025-07-07 23:59:59', '6.1.1.1', '/r',  'foo.test-bot.example.com', 'test-bot.example.com'),
  -- 15) Week-chunk seam: 2025-07-08 00:00:01 — first second of week 1
  ('2025-07-08 00:00:01', '6.1.1.2', '/s',  'foo.test-bot.example.com', 'test-bot.example.com'),
  -- 16) Same row but domain field is '?' — domain-exact match uses literal '?'
  ('2025-07-19 13:00:00', '7.1.1.1', '/t',  'whatever',                '?');

INSERT INTO websessions (id, datetime, ip, host, duration, domain, jobs, webevents) VALUES
  (101, '2025-07-05 08:00:00', '1.1.1.1', 'foo.test-bot.example.com',     1200, 'test-bot.example.com', 0, 5),
  (102, '2025-07-12 10:00:00', '2.2.2.1', 'node01.test-crawler.org',       900, 'test-crawler.org',     0, 4),
  (103, '2025-07-14 11:00:00', '2.3.1.1', 'exact-host.example.io',         400, 'example.io',           0, 1),
  (104, '2025-07-16 12:00:00', '2.4.1.1', 'mybigscannerhost.net',          800, 'mybigscannerhost.net', 0, 3),
  (105, '2025-07-22 14:00:00', '3.3.3.1', 'login.rcac.purdue.edu',         400, 'purdue.edu',           0, 1),
  (106, '2025-07-25 16:00:00', '4.4.4.2', 'host.user.net',                 300, 'user.net',             0, 2),
  (107, '2025-06-30 23:59:00', '5.5.5.1', 'foo.test-bot.example.com',      600, 'test-bot.example.com', 0, 1),
  (108, '2025-08-01 00:00:01', '5.5.5.4', 'foo.test-bot.example.com',      400, 'test-bot.example.com', 0, 1);
