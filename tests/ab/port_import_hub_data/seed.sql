-- Per-test fixture for import-hub-data A/B comparison.
-- Legacy (xlogimport_tool_and_reg_user_data.php) and new
-- (hzmetrics.py import-hub-data) copy:
--   1. hub.sessionlog → metrics.sessionlog_metrics (INSERT IGNORE)
--   2. hub.jos_xprofiles (emailConfirmed > 0) → metrics.jos_xprofiles_metrics
--      (DROP + CREATE LIKE + INSERT — rebuild every run)

USE foo_test;

INSERT INTO sessionlog (sessnum, username, remoteip, exechost, start, appname, walltime, cputime, viewtime) VALUES
  (20001, 'alice', '1.1.1.1', 'host1', '2025-07-10 08:00:00', 'aspectnotebook', 120, 60, 30),
  (20002, 'bob',   '2.2.2.1', 'host2', '2025-07-12 09:00:00', 'workspace',      0,   0,  0),
  -- duplicate sessnum to test INSERT IGNORE idempotency
  (20003, 'carol', '3.3.3.1', 'host3', '2025-07-18 14:00:00', 'aspectdesktop',  300, 250, 50);

INSERT INTO jos_xprofiles
  (uidNumber, name, username, email, registerDate, gidNumber, homeDirectory, loginShell, ftpShell, userPassword, gid, orgtype, organization, countryresident, countryorigin, gender, url, mailPreferenceOption, usageAgreement, modifiedDate, emailConfirmed, regIP, regHost)
VALUES
  (1001, 'Alice', 'alice', 'alice@example.com', '2024-01-01 00:00:00', '100', '/home/alice', '/bin/bash', '/bin/false', '', '', 'university', 'Acme', 'US', 'US', '', '', -1, 0, '2024-01-01 00:00:00', 1, '', ''),
  (1002, 'Bob',   'bob',   'bob@example.com',   '2024-02-01 00:00:00', '100', '/home/bob',   '/bin/bash', '/bin/false', '', '', 'industry',   'Acme', 'GB', 'IN', '', '', -1, 0, '2024-02-01 00:00:00', 1, '', ''),
  -- emailConfirmed = 0 — must be EXCLUDED from copy
  (1003, 'Carol', 'carol', 'carol@example.com', '2024-03-01 00:00:00', '100', '/home/carol', '/bin/bash', '/bin/false', '', '', 'industry',   'Acme', 'FR', 'FR', '', '', -1, 0, '2024-03-01 00:00:00', 0, '', '');

-- Pre-existing metrics-side sessionlog_metrics row — INSERT IGNORE must
-- leave it as-is even if a matching sessnum row exists in hub.
USE foo_metrics_test;

INSERT INTO sessionlog_metrics (sessnum, user, ip, start, appname) VALUES
  (20001, 'pre-existing', 'X.X.X.X', '2024-01-01 00:00:00', 'PRE_EXISTING_TOOL');
