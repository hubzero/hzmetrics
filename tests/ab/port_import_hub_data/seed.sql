-- Per-test fixture for import-hub-data A/B comparison.
-- Two operations under test:
--   1. INSERT IGNORE INTO metrics.sessionlog_metrics  SELECT FROM hub.sessionlog
--   2. DROP+CREATE LIKE+INSERT WHERE emailConfirmed > 0  → jos_xprofiles_metrics
--
-- Edge cases covered: duplicate sessnum (INSERT IGNORE preserves existing),
-- every emailConfirmed value (0/1/2/3/5/-1/NULL: legacy uses `> 0`), special
-- chars (single quotes, backslashes) in user fields, empty fields.

USE foo_test;

INSERT INTO sessionlog (sessnum, username, remoteip, exechost, start, appname, walltime, cputime, viewtime) VALUES
  (20001, 'alice',           '1.1.1.1', 'host1', '2025-07-10 08:00:00', 'aspectnotebook', 120, 60, 30),
  (20002, 'bob',              '2.2.2.1', 'host2', '2025-07-12 09:00:00', 'workspace',      0,   0,  0),
  (20003, 'carol',           '3.3.3.1', 'host3', '2025-07-18 14:00:00', 'aspectdesktop',  300, 250, 50),
  -- (NOTE: sessionlog.sessnum is the PK so duplicates can't exist on the hub
  -- side.  INSERT IGNORE is exercised by the pre-existing metrics row below.)
  -- special chars in username
  (20004, "o'reilly",         '4.4.4.1', 'host4', '2025-07-19 10:00:00', 'workspace',      0,  0,  0),
  -- empty username (legitimate "guest" sessions)
  (20005, '',                 '5.5.5.1', 'host5', '2025-07-20 11:00:00', 'guest',          0,  0,  0),
  -- gridstat user — sessionlog row exists but downstream filters drop it
  (20006, 'gridstat',         '6.6.6.1', 'host6', '2025-07-23 10:00:00', 'tool',           0,  0,  0);

INSERT INTO jos_xprofiles
  (uidNumber, name, username, email, registerDate, gidNumber, homeDirectory, loginShell, ftpShell, userPassword, gid, orgtype, organization, countryresident, countryorigin, gender, url, mailPreferenceOption, usageAgreement, modifiedDate, emailConfirmed, regIP, regHost)
VALUES
  -- emailConfirmed = 1 (the typical "confirmed" value) → INCLUDED
  (1001, 'Alice',   'alice',   'alice@example.com', '2024-01-01 00:00:00', '100', '/home/alice',   '/bin/bash', '/bin/false', '', '', 'university', 'Acme',   'US', 'US', '', '', -1, 0, '2024-01-01 00:00:00',  1, '', ''),
  -- emailConfirmed = 2 (some hubs use higher values) → INCLUDED (legacy: > 0)
  (1002, 'Bob',     'bob',     'bob@example.com',   '2024-02-01 00:00:00', '100', '/home/bob',     '/bin/bash', '/bin/false', '', '', 'industry',   'Acme',   'GB', 'IN', '', '', -1, 0, '2024-02-01 00:00:00',  2, '', ''),
  -- emailConfirmed = 3 → INCLUDED
  (1003, 'Carol',   'carol',   'carol@example.com', '2024-03-01 00:00:00', '100', '/home/carol',   '/bin/bash', '/bin/false', '', '', 'government', 'Acme',   'FR', 'FR', '', '', -1, 0, '2024-03-01 00:00:00',  3, '', ''),
  -- emailConfirmed = 5 → INCLUDED
  (1004, 'Dave',    'dave',    'dave@example.com',  '2024-04-01 00:00:00', '100', '/home/dave',    '/bin/bash', '/bin/false', '', '', 'industry',   'Acme',   'DE', 'DE', '', '', -1, 0, '2024-04-01 00:00:00',  5, '', ''),
  -- emailConfirmed = 0 — must be EXCLUDED
  (1005, 'Eve',     'eve',     'eve@example.com',   '2024-05-01 00:00:00', '100', '/home/eve',     '/bin/bash', '/bin/false', '', '', 'industry',   'Acme',   'JP', 'JP', '', '', -1, 0, '2024-05-01 00:00:00',  0, '', ''),
  -- emailConfirmed = -1 — also EXCLUDED (legacy: > 0)
  (1006, 'Frank',   'frank',   'frank@example.com', '2024-06-01 00:00:00', '100', '/home/frank',   '/bin/bash', '/bin/false', '', '', 'industry',   'Acme',   'CN', 'CN', '', '', -1, 0, '2024-06-01 00:00:00', -1, '', ''),
  -- special chars in profile fields (single quote, backslash, percent)
  (1007, "O'Brien", "o'brien", "ob@example.com",    '2024-07-01 00:00:00', '100', '/home/obrien',  '/bin/bash', '/bin/false', '', '', "industry",   "O'Acme", 'IE', 'IE', '', '', -1, 0, '2024-07-01 00:00:00',  1, '', ''),
  -- empty fields where allowed (orgtype, organization, country)
  (1008, 'Bare',    'bare',    'bare@example.com',  '2024-08-01 00:00:00', '100', '/home/bare',    '/bin/bash', '/bin/false', '', '', '',           '',       '',   '',   '', '', -1, 0, '2024-08-01 00:00:00',  1, '', '');

USE foo_metrics_test;

-- Pre-existing sessionlog_metrics row — INSERT IGNORE must NOT overwrite it.
INSERT INTO sessionlog_metrics (sessnum, user, ip, start, appname) VALUES
  (20001, 'pre-existing', 'X.X.X.X', '2024-01-01 00:00:00', 'PRE_EXISTING_TOOL');
