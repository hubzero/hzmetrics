-- Per-test fixture for fill-ipcountry A/B comparison.
-- Both legacy (xlogfix_ipcountry.php) and new (hzmetrics.py fill-ipcountry)
-- look up unresolved IPs via the HUBzero ipinfo HTTP service and fill the
-- target table's ipcountry column.

USE foo_metrics_test;

-- Web rows with unresolved ipcountry.  Pick a handful of well-known
-- stable public IPs (Cloudflare DNS, Google DNS, etc.) — both runs hit
-- the same upstream so the results should match.
INSERT INTO web (datetime, ip, content, ipcountry) VALUES
  ('2025-07-10 08:00:00', '1.1.1.1', '/a',  NULL),  -- Cloudflare DNS
  ('2025-07-12 09:00:00', '8.8.8.8', '/b',  NULL),  -- Google DNS
  ('2025-07-15 12:00:00', '208.67.222.222', '/c', NULL),  -- OpenDNS
  -- private/RFC1918 — should NOT be resolved
  ('2025-07-18 16:00:00', '10.0.0.1',  '/x', NULL),
  ('2025-07-20 10:00:00', '127.0.0.1', '/y', NULL),
  -- already resolved — must be skipped
  ('2025-07-22 14:00:00', '1.1.1.1', '/d', 'US'),
  -- out of range (June) — must be skipped
  ('2025-06-30 23:59:00', '1.0.0.1', '/o', NULL);
