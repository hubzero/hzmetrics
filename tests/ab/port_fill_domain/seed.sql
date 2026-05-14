-- Per-test fixture for the fill-domain A/B comparison.
-- The legacy get_domain() exercises 8+ promotion branches and several special
-- TLD lists.  This seed targets each branch with at least one row so a
-- divergence shows up in the diff immediately.

USE foo_metrics_test;

INSERT INTO web (datetime, ip, content, host, domain) VALUES
  -- ─── 2-level baseline (default) ───────────────────────────────
  ('2025-07-05 08:00:00', '10.0.0.1',  '/2lvl1', 'www.example.com',          ''),  -- → example.com
  ('2025-07-05 08:00:01', '10.0.0.2',  '/2lvl2', 'mail.google.com',          ''),  -- → google.com
  ('2025-07-05 08:00:02', '10.0.0.3',  '/2lvl3', 'site.example.org',         ''),  -- → example.org
  ('2025-07-05 08:00:03', '10.0.0.4',  '/2lvl4', 'host.example.net',         ''),  -- → example.net

  -- ─── 3-level promotion via int_3level (com/net/org/edu/gov/mil/ac/co/ne/or/ed) ───
  ('2025-07-10 09:00:00', '10.0.0.5',  '/i3-1', 'cs.purdue.edu',             ''),  -- → cs.purdue.edu (3-level)
  ('2025-07-10 09:00:01', '10.0.0.6',  '/i3-2', 'login.csail.mit.edu',       ''),  -- → csail.mit.edu
  ('2025-07-10 09:00:02', '10.0.0.7',  '/i3-3', 'host.foo.com.au',           ''),  -- field[1]=com, ccTLD=au → foo.com.au
  ('2025-07-10 09:00:03', '10.0.0.8',  '/i3-4', 'srv.foo.org.uk',            ''),  -- field[1]=org, ccTLD=uk → foo.org.uk
  ('2025-07-10 09:00:04', '10.0.0.9',  '/i3-5', 'web.foo.gov.uk',            ''),  -- field[1]=gov → foo.gov.uk
  ('2025-07-10 09:00:05', '10.0.0.10', '/i3-6', 'a.b.ac.cn',                 ''),  -- field[1]=ac in int_3level → b.ac.cn
  ('2025-07-10 09:00:06', '10.0.0.11', '/i3-7', 'host.co.jp',                ''),  -- 2-letter / 2-letter promotion (general rule)
  ('2025-07-10 09:00:07', '10.0.0.12', '/i3-8', 'site.physics.ed.au',        ''),  -- field[1]=ed in int_3level → physics.ed.au

  -- ─── 3-level promotion via 2-letter / 2-letter (general) ──────
  ('2025-07-11 10:00:00', '10.0.0.13', '/2x2-1', 'user.cs.ox.ac.uk',         ''),  -- field[1]=ac (int_3level) → cs.ox.ac.uk (note: 4-level chain)
  ('2025-07-11 10:00:01', '10.0.0.14', '/2x2-2', 'host.aa.bb',               ''),  -- 2-letter / 2-letter → aa.bb
  ('2025-07-11 10:00:02', '10.0.0.15', '/2x2-3', 'login.kth.se',             ''),  -- 3-letter SLD on 2-letter ccTLD → kth.se (no promotion)

  -- ─── 3-level: no2_3level exception ─ "ub" is excluded ──────────
  ('2025-07-11 10:00:03', '10.0.0.16', '/ub-1', 'site.ub.cc',                ''),  -- field[1]="ub" → STAYS at ub.cc (no promotion)
  ('2025-07-11 10:00:04', '10.0.0.17', '/ub-2', 'a.b.ub.cc',                 ''),  -- 4 parts but ub → still ub.cc

  -- ─── 3-level: mil_3level (af, army, navy) under .mil ──────────
  ('2025-07-11 10:00:05', '10.0.0.18', '/mil1', 'host.army.mil',             ''),  -- → host.army.mil
  ('2025-07-11 10:00:06', '10.0.0.19', '/mil2', 'server.navy.mil',           ''),  -- → server.navy.mil
  ('2025-07-11 10:00:07', '10.0.0.20', '/mil3', 'foo.af.mil',                ''),  -- → foo.af.mil
  ('2025-07-11 10:00:08', '10.0.0.21', '/mil4', 'a.b.army.mil',              ''),  -- 4 parts, → b.army.mil

  -- ─── 4-level promotion: us_4level (k12, lib, cc, tec) under .us ──
  ('2025-07-12 11:00:00', '10.0.0.22', '/4us1', 'school.fairfax.k12.va.us',  ''),  -- → fairfax.k12.va.us
  ('2025-07-12 11:00:01', '10.0.0.23', '/4us2', 'lib.boston.lib.ma.us',      ''),  -- → boston.lib.ma.us
  ('2025-07-12 11:00:02', '10.0.0.24', '/4us3', 'site.middle.cc.va.us',      ''),  -- → middle.cc.va.us
  ('2025-07-12 11:00:03', '10.0.0.25', '/4us4', 'host.inst.tec.fl.us',       ''),  -- → inst.tec.fl.us

  -- ─── 4-level: NOT us_4level under .us — stays 3-level ─────────
  ('2025-07-12 11:00:04', '10.0.0.26', '/4nu1', 'site.foo.bar.va.us',        ''),  -- field[2]=foo NOT in us_4level → bar.va.us
  ('2025-07-12 11:00:05', '10.0.0.27', '/4nu2', 'site.foo.k12.va.de',        ''),  -- field[0]=de NOT "us" → k12.va.de

  -- ─── 2-level hyphen-tail pattern ^(.+-.+-.+-.+)-(.+)$ ─────────
  ('2025-07-13 12:00:00', '10.0.0.28', '/h-1', 'host-a-b-c-d-foo.com',       ''),  -- 4+ hyphens in SLD → d-foo.com
  ('2025-07-13 12:00:01', '10.0.0.29', '/h-2', 'a-b-c-d-test.org',           ''),  -- 4+ hyphens → d-test.org
  -- Underscore-tail variants
  ('2025-07-13 12:00:02', '10.0.0.30', '/u-1', 'a_b_c_d-foo.com',            ''),  -- 3 underscores + hyphen → d-foo.com
  ('2025-07-13 12:00:03', '10.0.0.31', '/u-2', 'a_b_c_d_foo.com',            ''),  -- 3 underscores + underscore → d_foo.com (via _ tail)
  ('2025-07-13 12:00:04', '10.0.0.32', '/u-3', 'a-b-c-d_foo.com',            ''),  -- 3 hyphens + underscore → d_foo.com

  -- ─── single-label, sentinels, NULL ────────────────────────────
  ('2025-07-14 13:00:00', '10.0.0.33', '/s-1', 'localhost',                  ''),  -- no '.' → 'localhost' or empty (legacy stays empty)
  ('2025-07-14 13:00:01', '10.0.0.34', '/s-2', '?',                          ''),  -- '?' sentinel — should be left ''
  ('2025-07-14 13:00:02', '10.0.0.35', '/s-3', NULL,                         ''),  -- NULL host
  ('2025-07-14 13:00:03', '10.0.0.36', '/s-4', '',                           ''),  -- empty host
  ('2025-07-14 13:00:04', '10.0.0.37', '/s-5', '.example.com',               ''),  -- leading dot weird edge

  -- ─── already-populated domain ─ legacy should leave alone ─────
  ('2025-07-15 14:00:00', '10.0.0.38', '/p-1', 'foo.skip-me.com',            'skip-me.com'),
  ('2025-07-15 14:00:01', '10.0.0.39', '/p-2', 'foo.also-skip.com',          '?'),
  -- domain = '?' is also "unfilled" per the SELECT predicate; should be recomputed → also-skip.com

  -- ─── out-of-range (Jun 30 23:59 / Aug 01 00:01) ───────────────
  ('2025-06-30 23:59:00', '10.0.0.40', '/o-1', 'web.out-of-range.com',       ''),  -- in-range via day-before-month
  ('2025-08-01 00:01:00', '10.0.0.41', '/o-2', 'web.out-of-range.com',       '');  -- truly out
