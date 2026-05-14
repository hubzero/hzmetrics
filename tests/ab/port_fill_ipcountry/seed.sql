-- Per-test fixture for the fill-ipcountry A/B comparison.
-- Network-dependent: both implementations hit the same HUBzero ipinfo HTTP
-- service so resolved values agree.  Targets:
--   * SELECT predicate: ipcountry IS NULL OR ipcountry = '' OR ipcountry = '-'
--   * Already-resolved row (ipcountry already set) — must be skipped
--   * Public IPs — resolve via HTTP
--   * RFC1918 private IPs — ipgeo returns '-' (legacy stores '-')
--   * Loopback 127.0.0.1 — same as private
--   * Invalid IP shape — legacy ip2long returns false; skipped
--   * Date boundaries (Jun 30 23:59 day-before-month captured)
--   * Cache hit: pre-existing jos_metrics_ipgeo_cache row → short-circuit HTTP
--   * Cache TTL: row > 90 days old should NOT short-circuit

USE foo_test;

-- Pre-populate the geoip cache with one IP that has a fresh entry and
-- one that's stale (>90 days old).
INSERT INTO jos_metrics_ipgeo_cache
  (ip, countrySHORT, countryLONG, ipREGION, ipCITY, ipLATITUDE, ipLONGITUDE, lookup_datetime)
VALUES
  -- 1.1.1.1 = 16843009 (32-bit), cached today as 'AU' / Sydney (fake values to
  -- catch a cache-hit path: if cache is used, we get THESE values, not what the
  -- HTTP service returns)
  (16843009, 'AU', 'Australia',         'NSW',     'Sydney',     -33.87, 151.21, NOW()),
  -- 8.8.4.4 = 134743044, cached >90 days ago — must miss cache, refetch
  (134743044, 'XX','StaleCountry',     'StaleR', 'StaleCity',     0.00,   0.00, DATE_SUB(NOW(), INTERVAL 95 DAY));

USE foo_metrics_test;

INSERT INTO web (datetime, ip, content, ipcountry) VALUES
  -- Cached, fresh: 1.1.1.1 — should get AU/Sydney from CACHE (not network)
  ('2025-07-10 08:00:00', '1.1.1.1',          '/cached-fresh', NULL),
  -- Cached but stale: 8.8.4.4 — should re-fetch from HTTP
  ('2025-07-10 08:00:01', '8.8.4.4',          '/cached-stale', NULL),
  -- Uncached public IPs
  ('2025-07-12 09:00:00', '8.8.8.8',          '/uncached',     NULL),  -- Google DNS
  ('2025-07-15 12:00:00', '9.9.9.9',          '/quad9',        NULL),  -- Quad9 (stable PTR)
  -- RFC1918 private IPs → ipgeo returns '-'
  ('2025-07-18 16:00:00', '10.0.0.1',         '/priv-10',      NULL),
  ('2025-07-18 16:00:01', '172.16.0.1',       '/priv-172',     NULL),
  ('2025-07-18 16:00:02', '192.168.1.1',      '/priv-192',     NULL),
  -- Loopback
  ('2025-07-20 10:00:00', '127.0.0.1',        '/loopback',     NULL),
  -- Already resolved as 'US' — must be SKIPPED
  ('2025-07-21 11:00:00', '1.1.1.1',          '/already-us',   'US'),
  -- ipcountry = '-' — legacy SELECT considers this "unresolved" → re-fetched
  ('2025-07-22 12:00:00', '8.8.8.8',          '/dash',         '-'),
  -- ipcountry = '' — also "unresolved"
  ('2025-07-23 13:00:00', '9.9.9.9',          '/empty',        ''),
  -- Date boundary (Jun 30 23:59 — day-before-month captured)
  ('2025-06-30 23:59:00', '1.0.0.1',          '/jun30',        NULL),
  -- Out of range (Aug 1 00:01) — must NOT be resolved
  ('2025-08-01 00:01:00', '1.0.0.1',          '/aug1',         NULL);
