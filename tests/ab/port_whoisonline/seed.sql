-- Per-test fixture for whoisonline A/B comparison.
-- whoisonline:
--   1. Copies recent jos_session rows (idle < 3600s) into jos_session_geo
--   2. Resolves DNS for new IPs
--   3. Resolves GeoIP for new IPs
--   4. Writes whoisonline.xml to <hub_dir>/app/site/stats/maps/

USE foo_test;

-- jos_session — only rows with (UNIX_TIMESTAMP() - time) < 3600 are copied,
-- so seed with `time` = now() - small offset.
INSERT INTO jos_session (session_id, time, ip, username, guest, userid) VALUES
  ('s1', UNIX_TIMESTAMP() - 100,  '8.8.8.8',        '',         1, 0),
  ('s2', UNIX_TIMESTAMP() - 200,  '1.1.1.1',        'alice',    0, 1001),
  ('s3', UNIX_TIMESTAMP() - 500,  '9.9.9.9',        '',         1, 0),
  -- stale (> 3600s) — must be EXCLUDED
  ('s4', UNIX_TIMESTAMP() - 5000, '208.67.222.222', '',         1, 0);
