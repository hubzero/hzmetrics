# hzmetrics docs

Documentation for `hzmetrics.py` — the Python rewrite of the HUBzero
metrics pipeline.

Read in roughly this order if new to the project:

1. **[summary.md](summary.md)** — one page on what this is, what it
   produces, and how it runs day-to-day.
2. **[motivations.md](motivations.md)** — why we rewrote the legacy
   PHP/Perl pipeline.  What was broken, what we kept, what we changed.
3. **[history.md](history.md)** — origins of the legacy code, the
   abandoned Python+Celery+Redis attempt that came in between, and how
   the current rewrite came about.
4. **[architecture.md](architecture.md)** — pipeline phases, the two
   databases, key tables, scheduling, locking, and the catch-up model.
5. **[data-flow.md](data-flow.md)** — concrete trace of a single
   Apache log line through every stage to its final summary cell.
6. **[usage-tables.md](usage-tables.md)** — cheat sheet for the
   `summary_*_vals` tables that drive the usage-overview UI.  Adapted
   from J.M. Sperhac's "Hub usage data overview and table translator."
7. **[deployment.md](deployment.md)** — install on a new hub: cron,
   logrotate, schema bootstrap, optional unbound.
8. **[operations.md](operations.md)** — runbook for ops tasks
   (catch-up, stuck lock, bot inflation, DNS issues).
9. **[testing.md](testing.md)** — A/B + golden test modes, 26 ports.
10. **[glossary.md](glossary.md)** — short definitions for everything
    above (hub, period code, dnload, domain class, rowid/colid, etc.).

For a quick CLI reference:

```
$ python3 hzmetrics.py --help
```

The legacy PHP/Perl/Bash reference implementation is preserved under
[`tests/legacy/`](../tests/legacy/) — it's the bug-for-bug parity
target the A/B test harness compares the new code against.  See
[testing.md](testing.md) for the test suite.

---

The reference HUBzero deployment for this code is the
[](https://) hub at Purdue.  Other hubs
running the same scripts include , , and
historically .

---

**Acknowledgment.**  The HUBzero metrics subsystem was originally
written in Perl by Swaroop Shivarajapura and later ported to PHP by Nicholas J. Kisseberth.
Long-term stewardship of the codebase has been carried by
J.M. Sperhac (SDSC), among others.  This Python rewrite builds
directly on their work.
