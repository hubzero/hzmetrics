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
5. **[usage-tables.md](usage-tables.md)** — cheat sheet for the
   `summary_*_vals` tables that drive the usage-overview UI.  Adapted
   from J.M. Sperhac's "Hub usage data overview and table translator."

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
