-- Per-test fixture for summarize-month A/B comparison.
-- summarize-month exercises 7 worker sections across 6 periods.  This seed
-- provides minimal but complete coverage of every input table.

USE foo_test;

-- jos_users + jos_xprofiles for residency/orgtype joins.
INSERT INTO jos_users (id, username, name, email, password) VALUES
  (1001, 'alice', 'Alice', 'alice@x', ''),
  (1002, 'bob',   'Bob',   'bob@x',   '');

INSERT INTO jos_user_profiles (user_id, profile_key, profile_value, ordering) VALUES
  (1001, 'orgtype',         'university', 0),
  (1001, 'countryresident', 'us',         0),
  (1001, 'countryorigin',   'us',         0),
  (1002, 'orgtype',         'industry',   0),
  (1002, 'countryresident', 'gb',         0),
  (1002, 'countryorigin',   'gb',         0);

INSERT INTO jos_xprofiles
  (uidNumber, name, username, email, registerDate, gidNumber, homeDirectory, loginShell, ftpShell, userPassword, gid, orgtype, organization, countryresident, countryorigin, gender, url, mailPreferenceOption, usageAgreement, modifiedDate, emailConfirmed, regIP, regHost)
VALUES
  (1001, 'Alice', 'alice', 'a@x', '2024-01-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'university', '', 'US', 'US', '', '', -1, 0, '2024-01-01 00:00:00', 1, '', ''),
  (1002, 'Bob',   'bob',   'b@x', '2024-01-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'industry',   '', 'GB', 'GB', '', '', -1, 0, '2024-01-01 00:00:00', 1, '', '');

-- Hub-side sessionlog / joblog (sim_usage needs these).
INSERT INTO sessionlog (sessnum, username, remoteip, exechost, start, appname, walltime, cputime, viewtime) VALUES
  (40001, 'alice', '1.1.1.1', 'host1', '2025-07-10 08:00:00', 'aspectnotebook', 1200, 600, 200),
  (40002, 'bob',   '2.2.2.1', 'host1', '2025-07-12 09:00:00', 'workspace',      800,  400, 100);

INSERT INTO joblog (sessnum, job, superjob, event, start, walltime, cputime, ncpus, venue, status) VALUES
  (40001, 1, 0, 'started', '2025-07-10 08:00:00', 1200, 600, 1, 'default', 0),
  (40002, 1, 0, 'started', '2025-07-12 09:00:00',  800, 400, 1, 'default', 0);

-- Metrics-side: userlogin (rebuilt to userlogin_lite at run start)
USE foo_metrics_test;

INSERT INTO userlogin (datetime, user, ip, action) VALUES
  ('2025-07-10 08:00:00', 'alice', '1.1.1.1', 'login'),
  ('2025-07-10 08:30:00', 'alice', '1.1.1.1', 'simulation'),
  ('2025-07-12 09:00:00', 'bob',   '2.2.2.1', 'login'),
  ('2025-07-15 11:00:00', 'alice', '1.1.1.1', 'login');

-- toolstart for sim_users + sim_usage
INSERT INTO toolstart (datetime, success, user, ip, tool, walltime, cputime,
                       countryresident, countrycitizen, orgtype) VALUES
  ('2025-07-10 08:00:00', 1, 'alice', '1.1.1.1', 'aspectnotebook', 1200, 600, 'US', 'US', 'university'),
  ('2025-07-12 09:00:00', 1, 'bob',   '2.2.2.1', 'workspace',      800,  400, 'GB', 'GB', 'industry');

-- web rows: a few dnload=1 (downloads), a few hits, mixed sessionids
INSERT INTO web (datetime, ip, content, host, domain, dnload, sessionid) VALUES
  ('2025-07-10 08:00:00', '3.3.3.1', '/resources/123/index.html', 'guest1.example.com', 'example.com', 0, 100),
  ('2025-07-12 11:00:00', '4.4.4.1', '/resources/123/download/a.pdf', 'guest2.example.org', 'example.org', 1, 101),
  ('2025-07-15 14:00:00', '5.5.5.1', '/resources/456/download/b.zip', 'guest3.example.com', 'example.com', 1, 102),
  ('2025-07-18 16:00:00', '6.6.6.1', '/page.html', 'guest4.example.com', 'example.com', 0, 103);

INSERT INTO websessions (id, datetime, ip, host, duration, domain, jobs, webevents, ipcountry) VALUES
  -- registered interactive (alice's IP appears in userlogin → ip filter excludes)
  (100, '2025-07-10 08:00:00', '1.1.1.1', 'alice.example.com', 1500, 'example.com', 0, 8, 'US'),
  -- unregistered interactive long (jobs=0, duration>=900) — int_users target
  (101, '2025-07-10 08:00:00', '3.3.3.1', 'guest1.example.com', 1500, 'example.com', 0, 6, 'US'),
  -- unregistered download (duration<900, jobs=0 + dnload web rows)
  (102, '2025-07-12 11:00:00', '4.4.4.1', 'guest2.example.org', 100,  'example.org', 0, 1, 'GB'),
  (103, '2025-07-15 14:00:00', '5.5.5.1', 'guest3.example.com', 50,   'example.com', 0, 1, 'US'),
  -- short visit no jobs — counts only in misc visitors/visits
  (104, '2025-07-18 16:00:00', '6.6.6.1', 'guest4.example.com', 200,  'example.com', 0, 1, 'FR');

-- webhits for misc_usage (rowid=8)
INSERT INTO webhits (datetime, hits) VALUES
  ('2025-07-10', 1000),
  ('2025-07-12', 1200),
  ('2025-07-15', 1500);

-- Custom domainclass entries (the reference set may already cover example.com etc.)
-- The reference fixture has ~1800 rows; we don't need to add more unless tests need
-- specific classifications.
