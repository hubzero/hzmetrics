-- Per-test fixture for logfix-session A/B comparison.
-- Both legacy (logfix_session.pl) and new (hzmetrics.py logfix-session) coalesce
-- web hits per (ip, host) with a 1800s inactivity timeout, write to
-- websessions, and stamp web.sessionid + toolstart.sessionid.

USE foo_metrics_test;

-- Sessions to coalesce.  Each block of consecutive same-ip rows within
-- 1800s = one session; an >1800s gap = end of session.
INSERT INTO web (datetime, ip, content, host, domain) VALUES
  -- IP 1.1.1.1 — 4 events spanning 30 min (under 1800s gaps): one session
  ('2025-07-10 08:00:00', '1.1.1.1', '/a', 'alice.example.com', 'example.com'),
  ('2025-07-10 08:05:00', '1.1.1.1', '/b', 'alice.example.com', 'example.com'),
  ('2025-07-10 08:10:00', '1.1.1.1', '/c', 'alice.example.com', 'example.com'),
  ('2025-07-10 08:30:00', '1.1.1.1', '/d', 'alice.example.com', 'example.com'),

  -- IP 2.2.2.1 — 2 events with a 2-hour gap: TWO sessions
  ('2025-07-12 09:00:00', '2.2.2.1', '/x', 'bob.example.com',   'example.com'),
  ('2025-07-12 11:00:00', '2.2.2.1', '/y', 'bob.example.com',   'example.com'),

  -- IP 3.3.3.1 — single event: one session, duration = 0
  ('2025-07-15 14:00:00', '3.3.3.1', '/z', 'carol.example.com', 'example.com'),

  -- IP 4.4.4.1 — empty host, must still emit a session by IP
  ('2025-07-18 16:00:00', '4.4.4.1', '/no-host-1', '', ''),
  ('2025-07-18 16:10:00', '4.4.4.1', '/no-host-2', '', ''),

  -- host='?' must be skipped per the SELECT WHERE (no IP at all, '?' host)
  ('2025-07-20 10:00:00', '',         '/skip', '?', ''),

  -- already has sessionid — must be skipped
  ('2025-07-22 12:00:00', '5.5.5.1', '/done', 'dave.example.com', 'example.com');

UPDATE web SET sessionid = 999 WHERE ip = '5.5.5.1';

-- toolstart rows for the same time windows — logfix-session's iphost_jobs
-- helper should stamp these with the new sessionid where dates align.
INSERT INTO toolstart (datetime, ip, host, success, tool, execunit) VALUES
  -- matches alice's session (08:00–08:30) — should get the alice sessionid
  ('2025-07-10 08:15:00', '1.1.1.1', 'alice.example.com', 1, 'aspectnotebook', 'host1'),
  -- ip-only match for bob's first session
  ('2025-07-12 09:05:00', '2.2.2.1', '', 1, 'workspace', 'host1'),
  -- outside the session windows — should NOT be stamped
  ('2025-07-13 18:00:00', '9.9.9.9', '', 1, 'workspace', 'host1');
