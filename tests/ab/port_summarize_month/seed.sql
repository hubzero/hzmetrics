-- Per-test fixture for summarize-month A/B comparison.
-- Targets every cell across 7 workers × 11 cols × 6 periods.
-- Coverage plan:
--   * each continent bucket (US / AS / EU / OTHER)
--   * each orgtype bucket (educational / industry / government / other)
--   * registered (in userlogin) vs unregistered (only in websessions)
--   * download users (web.dnload=1) vs interactive (>=900s, jobs=0)
--   * cross-month rows (Dec 2024 for period 12/14)
--   * webhits aggregation across multiple days
--   * sim_usage: cputime, walltime, viewtime sums; >=10-min cpu users;
--     repeat-10-jobs; cross-month "average time between first and last sim"
--   * misc: domain count, session/visit count, max-logins-on-date

USE foo_test;

-- jos_users + jos_user_profiles + jos_xprofiles for residency/orgtype joins.
INSERT INTO jos_users (id, username, name, email, password) VALUES
  (1001, 'alice',   'Alice US-Univ',  'a@x', ''),
  (1002, 'bob',     'Bob GB-Industry','b@x', ''),
  (1003, 'carol',   'Carol JP-Univ',  'c@x', ''),
  (1004, 'dave',    'Dave FR-Univ',   'd@x', ''),
  (1005, 'eve',     'Eve BR-Other',   'e@x', ''),
  (1006, 'frank',   'Frank US-Gov',   'f@x', ''),
  (1007, 'grace',   'Grace CN-Univ',  'g@x', ''),
  (1008, 'henry',   'Henry DE-Other', 'h@x', '');

INSERT INTO jos_user_profiles (user_id, profile_key, profile_value, ordering) VALUES
  (1001, 'orgtype', 'university', 0), (1001, 'countryresident', 'us', 0), (1001, 'countryorigin', 'us', 0),
  (1002, 'orgtype', 'industry',   0), (1002, 'countryresident', 'gb', 0), (1002, 'countryorigin', 'gb', 0),
  (1003, 'orgtype', 'university', 0), (1003, 'countryresident', 'jp', 0), (1003, 'countryorigin', 'jp', 0),
  (1004, 'orgtype', 'university', 0), (1004, 'countryresident', 'fr', 0), (1004, 'countryorigin', 'fr', 0),
  (1005, 'orgtype', 'foundation', 0), (1005, 'countryresident', 'br', 0), (1005, 'countryorigin', 'br', 0),
  (1006, 'orgtype', 'government', 0), (1006, 'countryresident', 'us', 0), (1006, 'countryorigin', 'us', 0),
  (1007, 'orgtype', 'university', 0), (1007, 'countryresident', 'cn', 0), (1007, 'countryorigin', 'cn', 0),
  (1008, 'orgtype', 'foundation', 0), (1008, 'countryresident', 'de', 0), (1008, 'countryorigin', 'de', 0);

INSERT INTO jos_xprofiles
  (uidNumber, name, username, email, registerDate, gidNumber, homeDirectory, loginShell, ftpShell, userPassword, gid, orgtype, organization, countryresident, countryorigin, gender, url, mailPreferenceOption, usageAgreement, modifiedDate, emailConfirmed, regIP, regHost)
VALUES
  -- New accounts registered during July 2025 — feeds misc_usage rowid=6
  (1001, 'Alice', 'alice', 'a@x', '2025-07-05 08:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'university', '', 'US', 'US', '', '', -1, 0, '2025-07-05 08:00:00', 1, '', ''),
  (1002, 'Bob',   'bob',   'b@x', '2025-07-06 09:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'industry',   '', 'GB', 'GB', '', '', -1, 0, '2025-07-06 09:00:00', 1, '', ''),
  (1003, 'Carol', 'carol', 'c@x', '2024-06-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'university', '', 'JP', 'JP', '', '', -1, 0, '2024-06-01 00:00:00', 1, '', ''),
  (1004, 'Dave',  'dave',  'd@x', '2024-06-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'university', '', 'FR', 'FR', '', '', -1, 0, '2024-06-01 00:00:00', 1, '', ''),
  (1005, 'Eve',   'eve',   'e@x', '2024-06-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'foundation', '', 'BR', 'BR', '', '', -1, 0, '2024-06-01 00:00:00', 1, '', ''),
  (1006, 'Frank', 'frank', 'f@x', '2024-06-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'government', '', 'US', 'US', '', '', -1, 0, '2024-06-01 00:00:00', 1, '', ''),
  (1007, 'Grace', 'grace', 'g@x', '2024-06-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'university', '', 'CN', 'CN', '', '', -1, 0, '2024-06-01 00:00:00', 1, '', ''),
  (1008, 'Henry', 'henry', 'h@x', '2024-06-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'foundation', '', 'DE', 'DE', '', '', -1, 0, '2024-06-01 00:00:00', 1, '', '');

-- Hub-side sessionlog / joblog: sim_usage needs lots of variation
INSERT INTO sessionlog (sessnum, username, remoteip, exechost, start, appname, walltime, cputime, viewtime) VALUES
  (40001, 'alice', '1.1.1.1', 'h1', '2025-07-10 08:00:00', 'aspectnotebook', 1200, 600, 200),
  (40002, 'bob',   '2.2.2.1', 'h1', '2025-07-12 09:00:00', 'workspace',      800,  400, 100),
  (40003, 'carol', '3.3.3.1', 'h1', '2025-07-13 14:00:00', 'aspectdesktop',  900,  650, 30),
  (40004, 'dave',  '4.4.4.1', 'h1', '2025-07-14 10:00:00', 'aspectnotebook', 1500, 700, 100),
  -- alice has multiple sessions across the month — feeds avg-time-between
  (40005, 'alice', '1.1.1.1', 'h1', '2025-07-20 08:00:00', 'aspectnotebook',  300, 200, 50),
  -- cross-month for period-12/14 average
  (40010, 'alice', '1.1.1.1', 'h1', '2024-12-15 08:00:00', 'aspectnotebook', 1000, 500, 100),
  -- gridstat / hctest excluded
  (40020, 'gridstat',   '7.0.0.1', 'h1', '2025-07-25 10:00:00', 'tool',   999, 999, 0),
  (40021, 'hctest_x',   '7.0.0.2', 'h1', '2025-07-25 10:01:00', 'tool',   999, 999, 0);

INSERT INTO joblog (sessnum, job, superjob, event, start, walltime, cputime, ncpus, venue, status) VALUES
  (40001, 1, 0, 'started', '2025-07-10 08:00:00', 1200, 600, 1, 'default', 0),
  (40002, 1, 0, 'started', '2025-07-12 09:00:00',  800, 400, 1, 'default', 0),
  (40003, 1, 0, 'started', '2025-07-13 14:00:00',  900, 650, 1, 'default', 0),
  (40004, 1, 0, 'started', '2025-07-14 10:00:00', 1500, 700, 1, 'default', 0),
  (40005, 1, 0, 'started', '2025-07-20 08:00:00',  300, 200, 1, 'default', 0),
  (40005, 2, 0, '[waiting]', '2025-07-20 08:01:00', 5,  0, 1, 'default', 0),
  (40010, 1, 0, 'started', '2024-12-15 08:00:00', 1000, 500, 1, 'default', 0);

-- Metrics-side: userlogin (rebuilt to userlogin_lite at run start)
USE foo_metrics_test;

INSERT INTO userlogin (datetime, user, ip, action) VALUES
  ('2025-07-10 08:00:00', 'alice', '1.1.1.1', 'login'),
  ('2025-07-10 08:30:00', 'alice', '1.1.1.1', 'simulation'),
  ('2025-07-12 09:00:00', 'bob',   '2.2.2.1', 'login'),
  ('2025-07-13 14:00:00', 'carol', '3.3.3.1', 'login'),
  ('2025-07-14 10:00:00', 'dave',  '4.4.4.1', 'login'),
  ('2025-07-15 11:00:00', 'alice', '1.1.1.1', 'login'),
  -- frank/grace/henry login to populate org buckets in reg_users
  ('2025-07-15 12:00:00', 'frank', '8.0.0.1', 'login'),
  ('2025-07-15 13:00:00', 'grace', '8.0.0.2', 'login'),
  ('2025-07-15 14:00:00', 'henry', '8.0.0.3', 'login'),
  -- Max-logins-on-day: 2025-07-15 has 4 distinct users — feeds misc_usage rowid=7
  ('2025-07-15 15:00:00', 'eve',   '8.0.0.4', 'login'),
  -- "logout" / "detect" — included by legacy (pre-1018cc2 no filter)
  ('2025-07-16 08:00:00', 'alice', '1.1.1.1', 'logout'),
  ('2025-07-16 08:00:01', 'bob',   '2.2.2.1', 'detect');

-- toolstart for sim_users + sim_usage
INSERT INTO toolstart (datetime, success, user, ip, tool, walltime, cputime,
                       countryresident, countrycitizen, orgtype) VALUES
  ('2025-07-10 08:00:00', 1, 'alice', '1.1.1.1', 'aspectnotebook', 1200, 600,  'US', 'US', 'university'),
  ('2025-07-12 09:00:00', 1, 'bob',   '2.2.2.1', 'workspace',       800, 400,  'GB', 'GB', 'industry'),
  ('2025-07-13 14:00:00', 1, 'carol', '3.3.3.1', 'aspectdesktop',   900, 650,  'JP', 'JP', 'university'),
  ('2025-07-14 10:00:00', 1, 'dave',  '4.4.4.1', 'aspectnotebook', 1500, 700,  'FR', 'FR', 'university'),
  -- alice 2nd sim — repeat user
  ('2025-07-20 08:00:00', 1, 'alice', '1.1.1.1', 'aspectnotebook',  300, 200,  'US', 'US', 'university'),
  -- cross-month rows for period 12/14
  ('2024-12-15 08:00:00', 1, 'alice', '1.1.1.1', 'aspectnotebook', 1000, 500,  'US', 'US', 'university'),
  -- Power user: 12 sims in July — exercises the "Repeat Users with >= 10
  -- Simulation Jobs" branch (sim_usage rowid=9).
  ('2025-07-02 08:00:00', 1, 'iris',  '20.0.0.1', 'aspectnotebook', 50, 25, 'IN', 'IN', 'university'),
  ('2025-07-03 08:00:00', 1, 'iris',  '20.0.0.1', 'aspectnotebook', 50, 25, 'IN', 'IN', 'university'),
  ('2025-07-04 08:00:00', 1, 'iris',  '20.0.0.1', 'aspectnotebook', 50, 25, 'IN', 'IN', 'university'),
  ('2025-07-05 08:00:00', 1, 'iris',  '20.0.0.1', 'aspectnotebook', 50, 25, 'IN', 'IN', 'university'),
  ('2025-07-06 08:00:00', 1, 'iris',  '20.0.0.1', 'aspectnotebook', 50, 25, 'IN', 'IN', 'university'),
  ('2025-07-07 08:00:00', 1, 'iris',  '20.0.0.1', 'aspectnotebook', 50, 25, 'IN', 'IN', 'university'),
  ('2025-07-08 08:00:00', 1, 'iris',  '20.0.0.1', 'aspectnotebook', 50, 25, 'IN', 'IN', 'university'),
  ('2025-07-09 08:00:00', 1, 'iris',  '20.0.0.1', 'aspectnotebook', 50, 25, 'IN', 'IN', 'university'),
  ('2025-07-10 08:00:00', 1, 'iris',  '20.0.0.1', 'aspectnotebook', 50, 25, 'IN', 'IN', 'university'),
  ('2025-07-11 08:00:00', 1, 'iris',  '20.0.0.1', 'aspectnotebook', 50, 25, 'IN', 'IN', 'university'),
  ('2025-07-12 08:00:00', 1, 'iris',  '20.0.0.1', 'aspectnotebook', 50, 25, 'IN', 'IN', 'university'),
  ('2025-07-13 08:00:00', 1, 'iris',  '20.0.0.1', 'aspectnotebook', 50, 25, 'IN', 'IN', 'university');

-- web rows: dnload=1 (downloads) for dl_users path
INSERT INTO web (datetime, ip, content, host, domain, dnload, sessionid) VALUES
  ('2025-07-10 08:00:00', '3.3.3.1', '/resources/123/index.html',     'guest1.example.com', 'example.com', 0, 100),
  ('2025-07-12 11:00:00', '4.4.4.1', '/resources/123/download/a.pdf', 'guest2.example.org', 'example.org', 1, 101),
  ('2025-07-15 14:00:00', '5.5.5.1', '/resources/456/download/b.zip', 'guest3.example.com', 'example.com', 1, 102),
  ('2025-07-18 16:00:00', '6.6.6.1', '/page.html',                    'guest4.example.com', 'example.com', 0, 103),
  -- another downloader for variety across countries
  ('2025-07-19 10:00:00', '7.7.7.1', '/resources/789/download/c.nc',  'guest5.example.cn',  'example.cn',  1, 104);

-- websessions use domains drawn from the reference domainclass table so
-- the int_users / dl_users GROUP BY produces non-zero counts for each of
-- the org-class buckets (Edu/Industry/Government/Other):
--   class=1 (Edu)      aamu.edu
--   class=2 (Industry) ericsson.ca
--   class=3 (Gov)      cnea.gov.ar
--   class=6 (Other)    hhpublications.com
INSERT INTO websessions (id, datetime, ip, host, duration, domain, jobs, webevents, ipcountry) VALUES
  -- registered alice interactive — IP in login_ips, excluded from rowid=7
  (100, '2025-07-10 08:00:00', '1.1.1.1', 'alice.aamu.edu',     1500, 'aamu.edu',           0, 8, 'US'),
  -- unregistered interactive long, EDU domain → class=1
  (101, '2025-07-10 08:00:00', '3.3.3.1', 'guest1.aamu.edu',    1500, 'aamu.edu',           0, 6, 'US'),
  -- unregistered download, INDUSTRY → class=2
  (102, '2025-07-12 11:00:00', '4.4.4.1', 'guest2.ericsson.ca', 100,  'ericsson.ca',        0, 1, 'GB'),
  -- unregistered download, US edu (different inst from 101 — separate count) → class=1
  (103, '2025-07-15 14:00:00', '5.5.5.1', 'guest3.a-star.edu.sg', 50, 'a-star.edu.sg',      0, 1, 'US'),
  -- short visit, GOV → class=3
  (104, '2025-07-18 16:00:00', '6.6.6.1', 'guest4.cnea.gov.ar',  200, 'cnea.gov.ar',        0, 1, 'FR'),
  -- unregistered download, EDU CN → class=1
  (105, '2025-07-19 10:00:00', '7.7.7.1', 'guest5.aamu.edu',     80,  'aamu.edu',           0, 1, 'CN'),
  -- BR session, OTHER → class=6
  (106, '2025-07-21 12:00:00', '9.0.0.1', 'guest6.hhpublications.com', 1200, 'hhpublications.com', 0, 4, 'BR'),
  -- session with jobs > 0, INDUSTRY → class=2
  (107, '2025-07-22 14:00:00', '1.0.0.1', 'guest7.ericsson.ca', 2000, 'ericsson.ca',        3, 5, 'AU'),
  -- Additional unregistered LONG-duration interactive sessions (jobs=0,
  -- duration>=900) to populate every Edu/Industry/Gov bucket in rowid=7
  -- cols 8/9/10.  These IPs are NOT in login_ips_tmp (no userlogin rows).
  (108, '2025-07-23 08:00:00', '30.0.0.1', 'guest8.aamu.edu',         1100, 'aamu.edu',     0, 4, 'US'),  -- class=1 Edu (in addition to 101)
  (109, '2025-07-23 09:00:00', '30.0.0.2', 'guest9.ericsson.ca',      1500, 'ericsson.ca',  0, 4, 'GB'),  -- class=2 Industry
  (110, '2025-07-23 10:00:00', '30.0.0.3', 'guest10.cnea.gov.ar',     1300, 'cnea.gov.ar',  0, 5, 'AR'),  -- class=3 Government
  (111, '2025-07-23 11:00:00', '30.0.0.4', 'guest11.hhpublications.com', 950, 'hhpublications.com', 0, 3, 'IT'); -- class=6 Other

-- webhits across multiple days — misc_usage rowid=8 SUM
INSERT INTO webhits (datetime, hits) VALUES
  -- 1st-of-month row: period_dates returns an INCLUSIVE first-of-month
  -- start, so rowid=8 must use `datetime >= dstart` to count this day in
  -- period 1 (month) and period 3 (quarter).  Guards the >/>= hits-window
  -- boundary fix — a regression to strict `>` drops this row, and the
  -- period-1/3 hits cells diff against legacy + golden.
  ('2025-07-01', 700),
  ('2025-07-10', 1000),
  ('2025-07-12', 1200),
  ('2025-07-15', 1500),
  ('2025-07-20', 2500),
  -- June 2025 row — part of period=12 (rolling-12) and 14 (all-time)
  ('2025-06-30', 500),
  -- Dec 2024 row — only counted in period 14
  ('2024-12-01', 800);
