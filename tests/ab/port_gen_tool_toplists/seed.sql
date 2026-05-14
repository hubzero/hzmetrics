-- Per-test fixture for gen-tool-tops A/B comparison.
-- Same hub-side seed as gen-tool-stats (tools, tool_version, sessionlog,
-- joblog) PLUS metrics.sessionlog_metrics + jos_xprofiles_metrics that
-- gen-tool-tops joins through for domain / orgtype / country breakdowns.

USE foo_test;

INSERT INTO jos_resources (id, title, type, alias, published, standalone, publish_up)
VALUES
  (5001, 'Aspect Notebook',  7, 'aspectnotebook', 1, 1, '2024-01-01 00:00:00'),
  (5002, 'Burnman Notebook', 7, 'burnmannotebook',1, 1, '2024-01-01 00:00:00');

INSERT INTO jos_tool_version (id, toolname, instance, title, state) VALUES
  (101, 'aspectnotebook',   'aspectnotebook',     'AN',  1),
  (102, 'aspectnotebook',   'aspectnotebook_r1',  'AN1', 1),
  (104, 'burnmannotebook',  'burnmannotebook',    'BN',  1);

INSERT INTO sessionlog (sessnum, username, remoteip, exechost, start, appname, walltime, cputime, viewtime) VALUES
  (30001, 'alice', '1.1.1.1', 'host1', '2025-07-05 08:00:00', 'aspectnotebook',     100, 50, 25),
  (30002, 'bob',   '2.2.2.1', 'host1', '2025-07-12 09:00:00', 'aspectnotebook_r1', 200, 80, 30),
  (30003, 'carol', '3.3.3.1', 'host1', '2025-07-18 14:00:00', 'aspectnotebook',     150, 60, 20),
  (30005, 'dave',  '4.4.4.1', 'host2', '2025-07-15 12:00:00', 'burnmannotebook',    300, 200, 40);

INSERT INTO joblog (sessnum, job, superjob, event, start, walltime, cputime, ncpus, venue, status) VALUES
  (30001, 1, 0, 'started', '2025-07-05 08:00:00', 100, 50, 1, 'default', 0),
  (30002, 1, 0, 'started', '2025-07-12 09:00:00', 200, 80, 2, 'default', 0),
  (30003, 1, 0, 'started', '2025-07-18 14:00:00', 150, 60, 1, 'default', 0),
  (30005, 1, 0, 'started', '2025-07-15 12:00:00', 300, 200, 4, 'default', 0);

-- Metrics-side enrichment that gen-tool-tops joins through.
USE foo_metrics_test;

-- sessionlog_metrics is the enriched tool-session table.
INSERT INTO sessionlog_metrics (sessnum, user, ip, start, appname, domain, countryresident, orgtype) VALUES
  (30001, 'alice', '1.1.1.1', '2025-07-05 08:00:00', 'aspectnotebook',    'example.com',    'US', 'university'),
  (30002, 'bob',   '2.2.2.1', '2025-07-12 09:00:00', 'aspectnotebook_r1', 'foo.org',        'GB', 'industry'),
  (30003, 'carol', '3.3.3.1', '2025-07-18 14:00:00', 'aspectnotebook',    'example.com',    'FR', 'university'),
  (30005, 'dave',  '4.4.4.1', '2025-07-15 12:00:00', 'burnmannotebook',   'bar.edu',        'US', 'university');

-- jos_xprofiles_metrics mirrors the production schema (rebuilt by
-- import-hub-data).  gen-tool-tops needs it for the orgtype JOIN.
INSERT INTO jos_xprofiles_metrics
  (uidNumber, name, username, email, registerDate, gidNumber, homeDirectory, loginShell, ftpShell, userPassword, gid, orgtype, organization, countryresident, countryorigin, gender, url, mailPreferenceOption, usageAgreement, modifiedDate, emailConfirmed, regIP, regHost)
VALUES
  (1001, 'Alice', 'alice', 'a@x', '2024-01-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'university', '', 'US', '', '', '', -1, 0, '2024-01-01 00:00:00', 1, '', ''),
  (1002, 'Bob',   'bob',   'b@x', '2024-01-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'industry',   '', 'GB', '', '', '', -1, 0, '2024-01-01 00:00:00', 1, '', ''),
  (1003, 'Carol', 'carol', 'c@x', '2024-01-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'university', '', 'FR', '', '', '', -1, 0, '2024-01-01 00:00:00', 1, '', ''),
  (1004, 'Dave',  'dave',  'd@x', '2024-01-01 00:00:00', '100', '/', '/bin/bash', '/bin/false', '', '', 'university', '', 'US', '', '', '', -1, 0, '2024-01-01 00:00:00', 1, '', '');
