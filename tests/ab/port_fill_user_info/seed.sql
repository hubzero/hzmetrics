-- Per-test fixture for the fill-user-info A/B comparison.
-- Legacy (xlogfix_user_info.php) and new (hzmetrics.py fill-user-info)
-- enrich orgtype / countryresident / countrycitizen on a target table
-- by JOINing usernames against hub.jos_users + jos_user_profiles.

-- Hub side: a few users with profile_value rows for each of the three
-- profile_keys used by the script.
USE foo_test;

INSERT INTO jos_users (id, username, name, email, password) VALUES
  (1001, 'alice', 'Alice Researcher', 'alice@example.com', ''),
  (1002, 'bob',   'Bob Researcher',   'bob@example.com',   ''),
  (1003, 'carol', 'Carol Researcher', 'carol@example.com', '');

INSERT INTO jos_user_profiles (user_id, profile_key, profile_value, ordering) VALUES
  -- alice: full profile
  (1001, 'orgtype',         'university', 0),
  (1001, 'countryresident', 'us',         0),
  (1001, 'countryorigin',   'us',         0),
  -- bob: orgtype only (other params should stay empty)
  (1002, 'orgtype',         'industry',   0),
  -- carol: ALL fields, but lowercase — script must uppercase
  (1003, 'orgtype',         'government', 0),
  (1003, 'countryresident', 'de',         0),
  (1003, 'countryorigin',   'fr',         0);

-- Metrics side: rows in toolstart that need enrichment.
USE foo_metrics_test;

INSERT INTO toolstart (datetime, success, user, ip, tool) VALUES
  -- alice — should get orgtype=UNIVERSITY, countryresident=US, countrycitizen=US
  ('2025-07-10 08:00:00', 1, 'alice', '1.1.1.1', 'aspectnotebook'),
  ('2025-07-15 10:00:00', 1, 'alice', '1.1.1.2', 'aspectnotebook'),
  -- bob — orgtype only
  ('2025-07-12 09:00:00', 1, 'bob',   '2.2.2.1', 'workspace'),
  -- carol — full enrichment, lowercase profile values
  ('2025-07-18 14:00:00', 1, 'carol', '3.3.3.1', 'aspectdesktop'),
  -- unknown user — should be left unchanged
  ('2025-07-20 11:00:00', 1, 'dave',  '4.4.4.1', 'workspace'),
  -- row with ALREADY-populated params — should NOT be overwritten
  ('2025-07-22 16:00:00', 1, 'alice', '1.1.1.3', 'aspectnotebook');

UPDATE toolstart SET orgtype = 'ALREADYSET', countryresident = 'CA', countrycitizen = 'CA'
WHERE user = 'alice' AND ip = '1.1.1.3';
