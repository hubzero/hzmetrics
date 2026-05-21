# Future work

Items deliberately deferred during the 2026-05 geodynamics catch-up.
None of these block the pipeline — it runs correctly today.  These are
shape-improving / cost-reducing changes for when the operator has
bandwidth to take them on.

## Web table retention

Today every rebuild tick re-derives counts from raw `web` rows.  The
table at geodynamics grows ~250 k–500 k rows/month at steady state
post-filter, so it will reach ~25 M rows by 2028 and ~50 M by 2032.
Nothing breaks at that scale, but scans get slower in linear
proportion.  Three complementary moves let us bound the growth.

### Routine: trim irrelevant rows after summarize

A `web` row is structurally counted by:

- `do_andmore_usage` iff its `content` matches one of the resource
  URL patterns (built from `jos_resources` aliases).
- `_summary_build_download_*` iff `dnload = 1`.

A row that matches NEITHER is dead weight — `logfix-session` already
extracted its session contribution into `websessions`, and summarize
will never query it again.  After a month is fully summarized, those
rows can be deleted with no downstream effect.

Expected reach on geodynamics: most `/resources/browse?…`,
`/citations/…`, `/groups/…`, `/support/…`, `/login`, `/register`,
`/about`, plus any imported bot-like noise the import-time filters
miss.  Measure the set size before designing the cron — could be
30–50 % of current `web`.

**Implementation sketch**

- Build a pattern table from `jos_resources` once per maintenance run
  (or query it live).
- Chunked DELETE of `web` rows where `dnload = 0` AND `content` matches
  NO pattern, only for months that are fully summarized AND not
  flagged dirty.
- Run as a separate command (`prune-web --strategy=irrelevant`), not a
  pipeline phase — keep the orchestrator's per-tick work predictable.

### Deep prune: snapshot tables + year-by-year delete

When the routine trim isn't enough (or when an operator wants to bound
the table by retention rather than content), the analytics paths that
read `web` need to be served from per-month aggregate snapshots
instead.

**Snapshot tables**

```
web_resource_users(month, resid, ip, host)
  -- one row per distinct (ip, host) that hit resource `resid` in `month`
  -- PK (resid, month, ip, host); INDEX (month, resid)

web_dnload_users(month, ip, host, ipcountry)
  -- one row per distinct downloader per month

web_dnload_sessions(month, sessionid)
  -- one row per distinct download session per month
```

Estimated size on geodynamics: ~20–30 MB / year for
`web_resource_users` (700 resources × 12 months × ~50 avg unique
users); the dnload tables are an order of magnitude smaller.

**Read-path change** (per resource):

```sql
SELECT COUNT(*) FROM (
  SELECT ip, host FROM web_resource_users
    WHERE resid = X AND month IN <pruned months in window>
  UNION   -- de-dups across the gap
  SELECT DISTINCT ip, host FROM web
    WHERE (<resource X patterns>) AND datetime in <live months in window>
) u;
```

`UNION` (not `UNION ALL`) handles the across-month dedup: an IP that
hit a resource in pruned 2022 AND live 2024 still counts once.

**Sequence per year being pruned**

1. **Backfill snapshots** while raw `web` is still present — one-shot
   read job per month, no risk.
2. **Verify** snapshot-derived count = raw-derived count to ±0 % per
   (resource, month).
3. **Switch read path** to the hybrid query above.
4. **`prune-year YYYY`** — chunked DELETE of `web` rows in the year,
   by PK range, gated on snapshots-exist + fully-summarized + not-dirty.
5. **Mark the year as pruned** in `pipeline_state` so summarize uses
   snapshots for those months and rebuild refuses to touch them.

**Scope**: 1 migration, ~200 lines write-path additions to
`do_andmore_usage` and the dnload helpers, ~50 lines read-path
hybrid, two new CLI commands (`backfill-snapshots`, `prune-year`),
tests for parity and the hybrid query.  Probably a focused week.

### File-size reclaim after prune

InnoDB doesn't return deleted page space to the OS — the .ibd file
size stays the same and scans still walk every page even if half are
empty.  After the May-2026 cleanup `web` had ~3.6 GB of internal free
space (27 % wasted) and `websessions` ~600 MB (45 %).

`ALTER TABLE web ENGINE=InnoDB` (== `OPTIMIZE TABLE`) rebuilds the
file and reclaims the holes.  Cost: ~15–30 min, needs ~1× table size
of temp space (the disk is tight on geodynamics — 16 GB free vs a 13
GB table).  Schedule alongside a maintenance window when the table
has accumulated > ~30 % waste.  `pt-online-schema-change` would do
it lock-free but isn't installed.

## Bot detection improvements

Current `identify-bots` is a hardcoded substring list (~20 tokens).
That catches obvious bots (`bot`, `crawl`, `spider`, `scrapy`, etc.)
but misses a whole category that doesn't include any of those tokens
— measured 2026-05 on geodynamics: 35,556 unique UAs in one day's
log, only 57 flagged.

### High-leverage substrings to add

The single most useful addition is `+http` / `+https` — bots commonly
self-identify with `(... +https://example.com/bot)` in their UA;
no legitimate browser uses that convention.

Other narrow but useful tokens (each catches a real family of bots
that's currently slipping through):

- `curl/`, `wget`, `python-`, `python/` — HTTP-library bots
- `httpclient`, `go-http`, `aiohttp`, `httpx`, `node-fetch`, `axios`
- `scanner`, `checker`, `verifier`, `monitor`, `inspect`,
  `measurement`, `preview`, `linkcheck`
- Specific UAs by name: `siteimprove`, `censys`, `grammarly`,
  `iframely`, `claude-user`, `heritrix`, `bubing`

Add to `BOT_UA_FILTERS` in `hzmetrics.py`.  Volume: hundreds to low
thousands of rows / family / month — smaller wins than the referer-
spam URL filter, but cleaner signal because UA-based filtering is
exact-match and reusable across months.

### Heuristic detection (bigger change)

Substring matching will always lose to evasive bots impersonating
browsers (Chrome/137 etc).  A more durable approach:

- **Frequency**: UA hitting > N req/hour from the same /24 → bot.
- **Referer-empty rate**: UA with > 95 % empty Referer across many
  requests → bot.
- **UA shape**: very old Chrome versions (< latest minus N) are
  almost always bots, since real users upgrade.

Worth the work only if substring-based detection plateaus.

## Single-pass import

The current import phase reads each daily access log three times —
`import-webhits` (per-day counts), `identify-bots` (UA harvest),
`import-apache` (rows into `web`).  Faithful port of the legacy PHP
chain, but redundant: one pass through the file could do all three.

Deferred during the catch-up because changing the parser mid-catch-up
would have invalidated the in-flight months and broken the A/B golden
snapshots.  Pick this up after the catch-up settles.

**Scope**: one new combined import command, retire the three
individual ones from the orchestrator (keep them as CLI subcommands
for one-off backfills), update the A/B fixtures.  Probably a few
days, mostly testing.

## Cron install

`/etc/cron.d/hubzero-metrics` (or `/var/spool/cron/apache`) installs
the every-5-min `hzmetrics.py tick` cron entry via `make install`,
but on geodynamics this has been deliberately left disabled — manual
ticks during catch-up.  Once the rebuild is complete and steady-state
is verified, install the cron and let it drive.

No code work; just `sudo make install CRON_STYLE=spool` (or the
cron.d form) and then `sudo systemctl restart crond`.

## Check nanoHUB's metrics fork for resource-merge ideas

nanoHUB is the longest-running HUBzero deployment and has had ongoing
operational pressure to handle metrics cases the upstream code never
addressed.  Worth surveying their metrics fork (if accessible) for:

- Hand-curated "merge resource B into A" logic when B is published as
  effectively a version 2 of A — upstream only supports this via
  `jos_resource_assoc` parent→child + `standalone=0` on the child,
  which requires CMS-side data changes and doesn't carry semantics
  for "A and B are the same thing for analytics purposes."
- Any other patches they've made to `xlogfix_*` / `func_*` that
  fixed bugs the geodynamics snapshot still has.

Compare their tree against `tests/legacy/` and decide what's worth
porting.  No code work here until that survey happens.

## Periodic ANALYZE TABLE

InnoDB statistics drift after heavy churn (DELETEs, ALTERs, large
imports), and the query planner sometimes picks worse indexes — we
saw this on `web` after the May 2026 cleanup where the `ip` single-
column index was picked over the new `web_dt_ip` covering index.

A monthly `ANALYZE TABLE web; ANALYZE TABLE websessions; ANALYZE
TABLE userlogin; ANALYZE TABLE toolstart;` cron job costs seconds and
keeps the planner honest.  Add to the install Makefile target.
