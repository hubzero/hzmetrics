-- Per-test fixture for the logfix-session A/B comparison.
-- Targets every coalescer / boundary / state-machine branch:
--   * single-event session
--   * multi-event session under 1800s gap
--   * gap EXACTLY 1800s (Perl uses ">" so 1800 is "not yet timed out")
--   * gap EXACTLY 1801s (boundary)
--   * IP change ends session immediately
--   * empty IP + non-empty host fallback grouping
--   * '?' host rows (excluded by SELECT WHERE)
--   * already-sessioned rows (sessionid != 0/NULL, excluded)
--   * cross-week-boundary in-flight session (state carries across weeks)
--   * iphost_jobs matching toolstart by IP only, host only, both
--   * toolstart success=0 rows must NOT be stamped
--   * toolstart row OUTSIDE the IP/host but in time range (don't stamp)

USE foo_metrics_test;

INSERT INTO web (datetime, ip, content, host, domain) VALUES
  -- ── IP A: 3 events, gaps under 1800s → one session ──────────────
  ('2025-07-10 08:00:00', '1.1.1.1', '/a1', 'alice.example.com', 'example.com'),
  ('2025-07-10 08:05:00', '1.1.1.1', '/a2', 'alice.example.com', 'example.com'),
  ('2025-07-10 08:30:00', '1.1.1.1', '/a3', 'alice.example.com', 'example.com'),

  -- ── IP B: 2 events, gap EXACTLY 1800s (not timed out — Perl uses ">") ──
  ('2025-07-10 09:00:00', '1.1.1.2', '/b1', 'beta.example.com', 'example.com'),
  ('2025-07-10 09:30:00', '1.1.1.2', '/b2', 'beta.example.com', 'example.com'),  -- 1800s gap exactly

  -- ── IP C: 2 events, gap 1801s → session ends ──────────────────
  ('2025-07-10 10:00:00', '1.1.1.3', '/c1', 'gamma.example.com', 'example.com'),
  ('2025-07-10 10:30:01', '1.1.1.3', '/c2', 'gamma.example.com', 'example.com'),  -- 1801s

  -- ── IP D: 4 events, multiple gaps just under/over 1800 ──────────
  ('2025-07-10 11:00:00', '1.1.1.4', '/d1', 'delta.example.com', 'example.com'),
  ('2025-07-10 11:29:59', '1.1.1.4', '/d2', 'delta.example.com', 'example.com'),  -- 1799s
  ('2025-07-10 12:00:01', '1.1.1.4', '/d3', 'delta.example.com', 'example.com'),  -- 1802s → end
  ('2025-07-10 12:30:00', '1.1.1.4', '/d4', 'delta.example.com', 'example.com'),

  -- ── IP E: empty IP, host-only — should still session by host ────
  ('2025-07-11 14:00:00', '',        '/e1', 'eccentric.example.com', 'example.com'),
  ('2025-07-11 14:10:00', '',        '/e2', 'eccentric.example.com', 'example.com'),

  -- ── '?' host should be SKIPPED by SELECT WHERE ──────────────────
  ('2025-07-11 15:00:00', '',        '/?1', '?',                     ''),
  ('2025-07-11 15:01:00', '',        '/?2', '?',                     ''),

  -- ── pre-sessioned rows — already sessionid'd; skipped ──────────
  ('2025-07-12 16:00:00', '1.1.1.99', '/pre1', 'pre.example.com', 'example.com'),
  ('2025-07-12 16:01:00', '1.1.1.99', '/pre2', 'pre.example.com', 'example.com'),

  -- ── Week-boundary: events on July 7 23:59 vs July 8 00:01.
  --    Same IP; the gap is small (2 min) BUT they're in different week chunks.
  --    Legacy state-carry must flush the July-7 session at the start of week 1.
  ('2025-07-07 23:59:00', '1.1.7.1', '/w-end',  'weekend.example.com', 'example.com'),
  ('2025-07-08 00:01:00', '1.1.7.1', '/w-next', 'weekend.example.com', 'example.com'),

  -- ── Single-event lone session at end of run (the unflushed-last quirk!) ──
  ('2025-07-31 23:50:00', '1.1.31.1', '/lone', 'last.example.com', 'example.com');

UPDATE web SET sessionid = 999 WHERE ip = '1.1.1.99';

-- toolstart rows matched by iphost_jobs():
INSERT INTO toolstart (datetime, ip, host, success, tool, execunit) VALUES
  -- matches alice's session by IP only (host doesn't match — empty)
  ('2025-07-10 08:15:00', '1.1.1.1', '',                 1, 'aspectnotebook', 'host1'),
  -- matches alice by host only (empty IP)
  ('2025-07-10 08:20:00', '',        'alice.example.com', 1, 'aspectnotebook', 'host1'),
  -- matches alice by both (should stamp once — DISTINCT-on-PK)
  ('2025-07-10 08:25:00', '1.1.1.1', 'alice.example.com', 1, 'aspectnotebook', 'host1'),
  -- matches alice's session WINDOW but success=0 — must NOT be stamped
  ('2025-07-10 08:18:00', '1.1.1.1', 'alice.example.com', 0, 'aspectnotebook', 'host1'),
  -- different IP/host in same time window — NOT stamped
  ('2025-07-10 08:19:00', '9.9.9.9', 'unrelated.com',     1, 'aspectnotebook', 'host1'),
  -- matches eccentric session by host only
  ('2025-07-11 14:05:00', '',        'eccentric.example.com', 1, 'workspace',  'host1'),
  -- matches eccentric session WITHIN the +1799s window past session end (14:10)
  ('2025-07-11 14:30:00', '',        'eccentric.example.com', 1, 'workspace',  'host1');
