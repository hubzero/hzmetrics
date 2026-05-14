-- Per-test fixture for the fill-user-info A/B comparison.
-- Targets every condition the legacy SQL touches:
--   * full profile (all 3 keys)
--   * partial profile (some keys missing)
--   * lowercase profile_value → must be UPPERCASED on copy
--   * empty profile_value (skipped)
--   * already-populated target column (must NOT overwrite)
--   * unknown user (no profile match)
--   * profile present but user not in jos_users (no match through JOIN)
--   * profile_key with multiple values (different `ordering`)
--   * special characters in profile values

USE foo_test;

INSERT INTO jos_users (id, username, name, email, password) VALUES
  (1001, 'alice',   'Alice Researcher',  'alice@example.com',   ''),
  (1002, 'bob',     'Bob Researcher',    'bob@example.com',     ''),
  (1003, 'carol',   'Carol Researcher',  'carol@example.com',   ''),
  (1004, 'eve',     'Eve Empty',         'eve@example.com',     ''),
  (1005, 'frank',   'Frank Special',     'frank@example.com',   ''),
  (1006, 'gabe',    'Gabe Multi',        'gabe@example.com',    '');

INSERT INTO jos_user_profiles (user_id, profile_key, profile_value, ordering) VALUES
  -- alice: full lowercase profile → UPPERCASED on copy
  (1001, 'orgtype',         'university', 0),
  (1001, 'countryresident', 'us',         0),
  (1001, 'countryorigin',   'us',         0),
  -- bob: orgtype only (no countries)
  (1002, 'orgtype',         'industry',   0),
  -- carol: ALREADY-UPPERCASE input — legacy passes through strtoupper which keeps as-is
  (1003, 'orgtype',         'GOVERNMENT', 0),
  (1003, 'countryresident', 'DE',         0),
  (1003, 'countryorigin',   'FR',         0),
  -- eve: empty profile_value — legacy checks `if ($row['profile_value'])` and skips empties
  (1004, 'orgtype',         '',           0),
  (1004, 'countryresident', '',           0),
  -- frank: special chars (single-quote, percent sign, etc.) — must round-trip via dbquote
  (1005, 'orgtype',         "o'reilly",   0),
  (1005, 'countryresident', '%',          0),
  -- gabe: multiple profile_value rows for same key with different `ordering`
  --   legacy does no ORDER BY — the last row read wins (DB order)
  (1006, 'orgtype',         'industry',   0),
  (1006, 'orgtype',         'university', 1),
  (1006, 'countryresident', 'us',         0),
  (1006, 'countryresident', 'ca',         1);

-- A profile entry whose user isn't in jos_users — JOIN drops it
INSERT INTO jos_user_profiles (user_id, profile_key, profile_value, ordering) VALUES
  (9999, 'orgtype', 'orphan', 0);

USE foo_metrics_test;

INSERT INTO toolstart (datetime, success, user, ip, tool) VALUES
  -- alice — should get UNIVERSITY, US, US on every row
  ('2025-07-10 08:00:00', 1, 'alice', '1.1.1.1', 'aspectnotebook'),
  ('2025-07-15 10:00:00', 1, 'alice', '1.1.1.2', 'aspectnotebook'),
  -- bob — should get orgtype=INDUSTRY only
  ('2025-07-12 09:00:00', 1, 'bob',   '2.2.2.1', 'workspace'),
  -- carol — should get GOVERNMENT/DE/FR (already uppercase, no change)
  ('2025-07-18 14:00:00', 1, 'carol', '3.3.3.1', 'aspectdesktop'),
  -- eve — empty profile_value, no update
  ('2025-07-19 14:30:00', 1, 'eve',   '5.5.5.1', 'workspace'),
  -- frank — special chars round-trip
  ('2025-07-20 11:00:00', 1, 'frank', '4.4.4.1', 'workspace'),
  -- gabe — multi-value: last-row-wins via DB scan order
  ('2025-07-21 12:00:00', 1, 'gabe',  '6.6.6.1', 'workspace'),
  -- dave — not in jos_users at all → no update
  ('2025-07-22 13:00:00', 1, 'dave',  '7.7.7.1', 'workspace'),
  -- row with ALREADY-populated columns — must NOT be overwritten
  ('2025-07-23 16:00:00', 1, 'alice', '1.1.1.3', 'aspectnotebook'),
  -- row with one populated column, others empty — only empties get filled
  ('2025-07-24 17:00:00', 1, 'alice', '1.1.1.4', 'aspectnotebook'),
  -- success=0 row — fill-user-info has NO success filter, so it's still updated
  ('2025-07-25 18:00:00', 0, 'alice', '1.1.1.5', 'aspectnotebook');

UPDATE toolstart SET orgtype = 'ALREADYSET', countryresident = 'CA', countrycitizen = 'CA'
WHERE user = 'alice' AND ip = '1.1.1.3';

UPDATE toolstart SET orgtype = 'PARTIAL'
WHERE user = 'alice' AND ip = '1.1.1.4';
