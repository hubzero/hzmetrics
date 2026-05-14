-- Per-test fixture for the fill-domain A/B comparison.
-- Both legacy (xlogfix_domain.php) and new (hzmetrics.py fill-domain) derive
-- a `domain` column from `host` using TLD-promotion rules.  The test seeds
-- a variety of hostname shapes covering all promotion branches and lets the
-- diff verify the two implementations agree.

USE foo_metrics_test;

INSERT INTO web (datetime, ip, content, host, domain) VALUES
  -- standard 2-level                 expect: example.com / google.com
  ('2025-07-05 08:00:00', '10.0.0.1', '/a', 'www.example.com',          ''),
  ('2025-07-05 08:00:01', '10.0.0.2', '/b', 'mail.google.com',          ''),
  -- 3-level under com / edu          expect: purdue.edu / mit.edu
  ('2025-07-10 09:00:00', '10.0.0.3', '/c', 'node.cs.purdue.edu',       ''),
  ('2025-07-10 09:00:01', '10.0.0.4', '/d', 'login.csail.mit.edu',      ''),
  -- 2-letter ccTLD with 2-letter SLD (.ac.uk promotion etc.)
  ('2025-07-12 10:00:00', '10.0.0.5', '/e', 'user.cs.ox.ac.uk',         ''),
  ('2025-07-12 10:00:01', '10.0.0.6', '/f', 'host.physics.ac.cn',       ''),
  -- 2-letter ccTLD with non-2-letter SLD: keep last 3 parts
  ('2025-07-13 11:00:00', '10.0.0.7', '/g', 'login.kth.se',             ''),
  ('2025-07-13 11:00:01', '10.0.0.8', '/h', 'web.uni-koeln.de',         ''),
  -- 4-level under .us (k12/lib/cc/tec promotion)
  ('2025-07-15 12:00:00', '10.0.0.9', '/i', 'school.fairfax.k12.va.us', ''),
  ('2025-07-15 12:00:01', '10.0.0.10','/j', 'lib.boston.lib.ma.us',     ''),
  -- single-label / bare TLD / unusual
  ('2025-07-16 13:00:00', '10.0.0.11','/k', 'localhost',                ''),
  ('2025-07-16 13:00:01', '10.0.0.12','/l', '?',                        ''),
  -- already-populated domain — should be left alone
  ('2025-07-17 14:00:00', '10.0.0.13','/m', 'foo.skip-me.com',          'skip-me.com'),
  -- out of range (June) — should not be touched
  ('2025-06-30 23:59:00', '10.0.0.14','/n', 'web.out-of-range.com',     ''),
  -- out of range (August) — should not be touched
  ('2025-08-01 00:01:00', '10.0.0.15','/o', 'web.out-of-range.com',     '');
