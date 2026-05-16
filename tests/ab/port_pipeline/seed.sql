-- Per-test fixture for the full-pipeline A/B comparison.
-- Loads RAW (un-enriched) data and runs the COMPLETE __process_*.sh chain
-- in both implementations — resolve-dns + fill-domain + fill-ipcountry +
-- middleware-wall/cpu + logfix-session + clean-bots + fill-user-info +
-- gen-tool-stats/tops/toplists + summarize-month + andmore-usage.
--
-- Network-dependent: every IP here is a known stable-PTR public IP so
-- both implementations resolve to the same hostname.  fill-ipcountry hits
-- the same upstream service for both.
--
-- Stable-PTR IPs used:
--   1.1.1.1 / 1.0.0.1   → one.one.one.one  (Cloudflare, AU geo)
--   8.8.8.8 / 8.8.4.4   → dns.google       (Google, US geo)
--   9.9.9.9             → dns9.quad9.net   (Quad9, US geo)
-- Plus 198.51.100.x (TEST-NET-2 reserved) — these always resolve to '?'

USE foo_test;

-- Hub-side: users + xprofiles + tool resources + sessionlog + joblog.
INSERT INTO jos_users (id, username, name, email, password) VALUES
  (1001, 'alice', 'Alice US-Univ',  'a@x', ''),
  (1002, 'bob',   'Bob GB-Industry','b@x', ''),
  (1003, 'carol', 'Carol JP-Univ',  'c@x', ''),
  (1004, 'dave',  'Dave FR-Univ',   'd@x', '');

INSERT INTO jos_user_profiles (user_id, profile_key, profile_value, ordering) VALUES
  (1001, 'orgtype', 'university', 0), (1001, 'countryresident', 'us', 0), (1001, 'countryorigin', 'us', 0),
  (1002, 'orgtype', 'industry',   0), (1002, 'countryresident', 'gb', 0), (1002, 'countryorigin', 'gb', 0),
  (1003, 'orgtype', 'university', 0), (1003, 'countryresident', 'jp', 0), (1003, 'countryorigin', 'jp', 0),
  (1004, 'orgtype', 'university', 0), (1004, 'countryresident', 'fr', 0), (1004, 'countryorigin', 'fr', 0);

INSERT INTO jos_xprofiles
  (uidNumber, name, username, email, registerDate, gidNumber, homeDirectory, loginShell, ftpShell, userPassword, gid, orgtype, organization, countryresident, countryorigin, gender, url, mailPreferenceOption, usageAgreement, modifiedDate, emailConfirmed, regIP, regHost)
VALUES
  (1001, 'Alice', 'alice', 'a@x', '2025-07-05 08:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'university', '', 'US', 'US', '', '', -1, 0, '2025-07-05 08:00:00', 1, '', ''),
  (1002, 'Bob',   'bob',   'b@x', '2025-07-06 09:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'industry',   '', 'GB', 'GB', '', '', -1, 0, '2025-07-06 09:00:00', 1, '', ''),
  (1003, 'Carol', 'carol', 'c@x', '2024-06-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'university', '', 'JP', 'JP', '', '', -1, 0, '2024-06-01 00:00:00', 1, '', ''),
  (1004, 'Dave',  'dave',  'd@x', '2024-06-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'university', '', 'FR', 'FR', '', '', -1, 0, '2024-06-01 00:00:00', 1, '', '');

INSERT INTO jos_resources (id, title, type, alias, path, published, standalone, publish_up) VALUES
  (5001, 'Aspect Notebook', 7, 'aspectnotebook',  '', 1, 1, '2024-01-01 00:00:00'),
  (5002, 'Workspace',       7, 'workspace',        '', 1, 1, '2024-01-01 00:00:00'),
  (7100, 'Topic A',         5, 'topica',  'topics/intro', 1, 1, '2024-01-01 00:00:00');

INSERT INTO jos_tool_version (id, toolname, instance, title, state) VALUES
  (101, 'aspectnotebook', 'aspectnotebook', 'AN', 1),
  (102, 'workspace',      'workspace',      'WS', 1);

-- sessionlog rows tied to stable-PTR IPs.  middleware-wall/cpu copies
-- these into toolstart.
INSERT INTO sessionlog (sessnum, username, remoteip, exechost, start, appname, walltime, cputime, viewtime) VALUES
  (40001, 'alice', '1.1.1.1', 'h1', '2025-07-10 08:00:00', 'aspectnotebook', 1200, 600, 200),
  (40002, 'bob',   '8.8.8.8', 'h1', '2025-07-12 09:00:00', 'workspace',       800, 400, 100),
  (40003, 'carol', '9.9.9.9', 'h1', '2025-07-13 14:00:00', 'aspectnotebook',  900, 650,  30),
  (40004, 'dave',  '1.0.0.1', 'h1', '2025-07-14 10:00:00', 'aspectnotebook', 1500, 700, 100);

INSERT INTO joblog (sessnum, job, superjob, event, start, walltime, cputime, ncpus, venue, status) VALUES
  (40001, 1, 0, 'started', '2025-07-10 08:00:00', 1200, 600, 1, 'default', 0),
  (40002, 1, 0, 'started', '2025-07-12 09:00:00',  800, 400, 1, 'default', 0),
  (40003, 1, 0, 'started', '2025-07-13 14:00:00',  900, 650, 1, 'default', 0),
  (40004, 1, 0, 'started', '2025-07-14 10:00:00', 1500, 700, 1, 'default', 0);

-- ──────────────────────────────────────────────────────────────────────
-- Metrics side — RAW (host/domain/ipcountry/sessionid all NULL)
-- ──────────────────────────────────────────────────────────────────────
USE foo_metrics_test;

-- userlogin: arrange so 2025-07-14 has 3 distinct users (the unambiguous max).
-- The SQL has no tie-breaker on `max-logins-on-day` (GROUP BY day ORDER BY
-- logins DESC LIMIT 1) — equal-count days return implementation-defined
-- order, which differs between legacy and new runs.
INSERT INTO userlogin (datetime, user, ip, action) VALUES
  ('2025-07-10 08:00:00', 'alice', '1.1.1.1', 'login'),
  ('2025-07-12 09:00:00', 'bob',   '8.8.8.8', 'login'),
  ('2025-07-13 14:00:00', 'carol', '9.9.9.9', 'login'),
  ('2025-07-14 10:00:00', 'dave',  '1.0.0.1', 'login'),
  -- 2025-07-14 also gets alice + bob, so that day has 3 distinct users
  -- (vs 1 user on other days).  Unambiguous max → both implementations
  -- return '2025-07-14'.
  ('2025-07-14 11:00:00', 'alice', '1.1.1.1', 'login'),
  ('2025-07-14 12:00:00', 'bob',   '8.8.8.8', 'login');

-- Pre-existing toolstart with 0 placeholder walltime/cputime.  The legacy
-- Perl wrote "-1" as a sentinel here but toolstart.walltime/cputime are
-- FLOAT UNSIGNED — -1 was always silently coerced to 0 in lenient mode
-- (and rejected in strict mode).  middleware-wall's `t.walltime < 0` UPDATE
-- branch therefore never matched in practice; middleware-cpu's
-- `t.cputime <= 0 AND j.cputime > 0` does.
INSERT INTO toolstart (datetime, success, user, ip, tool, execunit, walltime, cputime) VALUES
  ('2025-07-10 08:00:00', 1, 'alice', '1.1.1.1', 'aspectnotebook', 'h1', 0, 0),
  ('2025-07-12 09:00:00', 1, 'bob',   '8.8.8.8', 'workspace',       'h1', 0, 0);
-- sessionlog 40003 + 40004 have no matching toolstart — middleware-wall
-- INSERTs them.

-- web rows with NO host / domain / ipcountry / sessionid.  resolve-dns
-- + fill-domain + fill-ipcountry + logfix-session populate them in order.
INSERT INTO web (datetime, ip, content) VALUES
  -- alice registered user (logs in from 1.1.1.1)
  ('2025-07-10 08:00:00', '1.1.1.1', '/page'),
  ('2025-07-10 08:05:00', '1.1.1.1', '/page2'),
  -- unregistered Cloudflare guest (long session)
  ('2025-07-10 12:00:00', '1.0.0.1', '/page'),
  ('2025-07-10 12:30:00', '1.0.0.1', '/page2'),
  -- unregistered Google guest
  ('2025-07-12 11:00:00', '8.8.4.4', '/p1'),
  -- unregistered download
  ('2025-07-12 11:01:00', '8.8.4.4', '/resources/123/download/a.pdf'),
  -- unregistered Quad9 guest (single hit)
  ('2025-07-15 09:00:00', '9.9.9.9', '/p2'),
  -- bot row — host will resolve to '%.rcac.purdue.edu' if it actually
  -- resolved.  Use TEST-NET-2 IP (198.51.100.x) — resolves to '?'.
  -- clean-bots won't filter it because exclude_list pattern matches host
  -- field, not IP.  Add a host filter test by using a known bot domain
  -- via the synthesized exclude_list entry below.
  ('2025-07-16 10:00:00', '198.51.100.1', '/scrape'),
  -- andmore-usage match
  ('2025-07-17 14:00:00', '198.51.100.2', 'topics/intro'),
  ('2025-07-17 14:05:00', '198.51.100.2', 'topics/intro/page1');

-- Add a domain-type exclude_list entry that will match a future-resolved
-- domain.  After resolve-dns turns 1.0.0.1's host into 'one.one.one.one'
-- and fill-domain extracts the domain, clean-bots will delete rows where
-- domain matches.  We don't actually expect to delete the 1.0.0.1 rows
-- (Cloudflare isn't a bot) — this just verifies clean-bots queries the
-- exclude_list correctly.
-- (The reference exclude_list already has %.rcac.purdue.edu as type=host.)

INSERT INTO webhits (datetime, hits) VALUES
  ('2025-07-10', 1000),
  ('2025-07-15', 1500);
