-- Per-test fixture for the whoisonline A/B comparison.
-- Targets every branch the legacy code touches:
--   * idle < 3600s → INCLUDED in jos_session_geo
--   * idle exactly 3600s → EXCLUDED (legacy uses `< 3600`)
--   * idle exactly 3599s → INCLUDED (boundary)
--   * idle > 3600s → EXCLUDED
--   * guest=1, userid=0 (guest session)
--   * guest=0, userid=<n>, username=<name> (signed-in user)
--   * multiple sessions with same (ip, username) — GROUP BY collapses
--   * Two different users on same IP (separate rows)
--   * DNS-stable IPs (9.9.9.9, 1.1.1.1, 8.8.8.8)

USE foo_test;

INSERT INTO jos_session (session_id, time, ip, username, guest, userid) VALUES
  -- ── ACTIVE sessions (idle < 3600s) ─────────────────────────────
  -- alice signed-in, idle 100s — INCLUDED
  ('s1', UNIX_TIMESTAMP() - 100,  '1.1.1.1', 'alice',    0, 1001),
  -- guest, idle 200s — INCLUDED
  ('s2', UNIX_TIMESTAMP() - 200,  '8.8.8.8', '',         1, 0),
  -- another guest, idle 500s, Quad9 (stable PTR) — INCLUDED
  ('s3', UNIX_TIMESTAMP() - 500,  '9.9.9.9', '',         1, 0),
  -- two sessions for SAME (ip, username) — GROUP BY collapses to one row
  ('s4', UNIX_TIMESTAMP() - 1000, '1.1.1.1', 'alice',    0, 1001),
  ('s5', UNIX_TIMESTAMP() - 1500, '1.1.1.1', 'alice',    0, 1001),
  -- different user on same IP — separate row
  ('s6', UNIX_TIMESTAMP() - 300,  '1.1.1.1', 'bob',      0, 1002),

  -- ── BOUNDARY: idle == 3599s — INCLUDED (legacy: `< 3600`) ───────
  ('s7', UNIX_TIMESTAMP() - 3599, '8.8.4.4', '',         1, 0),

  -- ── BOUNDARY: idle == 3600s — EXCLUDED ─────────────────────────
  ('s8', UNIX_TIMESTAMP() - 3600, '1.0.0.1', '',         1, 0),

  -- ── BOUNDARY: idle == 3601s — EXCLUDED ─────────────────────────
  ('s9', UNIX_TIMESTAMP() - 3601, '208.67.222.222', '',  1, 0),

  -- ── stale (5000s) — EXCLUDED ───────────────────────────────────
  ('s10', UNIX_TIMESTAMP() - 5000, '9.9.9.10',         '', 1, 0);
