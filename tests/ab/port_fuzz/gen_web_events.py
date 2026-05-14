#!/usr/bin/env python3
"""Generate random web events for fuzzing logfix-session.

Targets the session-coalescing branches in logfix_session.pl:
  - IP/host transition (closes session)
  - 1800s timeout gap (closes session)
  - 1800s threshold boundaries (1798, 1799, 1800, 1801, 1802)
  - Same IP back-to-back (extends session)
  - Empty host vs filled host
  - Multi-session days

Usage: gen_web_events.py <count> <seed>

Emits SQL INSERTs into the web table.  All events fall within a single
week of 2025-07 so all four week-windows of legacy logfix_session.pl
see the data (it picks week 0 here).
"""
import random
import sys

WEEK_START = "2025-07-07 00:00:00"  # Monday
WEEK_SECONDS = 7 * 86400

# IP pool — small so events cluster onto shared sessions.  Use TEST-NET
# ranges so no real network is implicated.
IP_POOL = [
    "203.0.113.1", "203.0.113.2", "203.0.113.3", "203.0.113.4",
    "198.51.100.5", "198.51.100.6",
    "192.0.2.7", "192.0.2.8",
]

# Hosts — some real-looking, some blank.  Coalescing uses host only when
# ip is empty, so this exercises the (!$s_ip && $s_host && ...) branch.
HOST_POOL = [
    "h1.example.com", "h2.example.com",
    "h3.example.org",
    "",  # blank — exercise default
]

CONTENT_POOL = ["/p1", "/p2", "/resources/1/abc", "/topics/intro",
                "/view", "/p3", "/p4", "/img.png", "/style.css"]


def main():
    if len(sys.argv) != 3:
        print("usage: gen_web_events.py <count> <seed>", file=sys.stderr)
        sys.exit(2)
    count = int(sys.argv[1])
    seed = int(sys.argv[2])
    rng = random.Random(seed)

    # Pre-compute a sequence of (ip, host, gap_seconds) tuples that biases
    # toward branch-boundary gaps (1798–1802 inclusive).  In half the
    # cases we pick a uniform random gap; in the other half we pick from
    # the boundary set.
    gap_boundary = [0, 1, 60, 600, 1798, 1799, 1800, 1801, 1802,
                    3600, 7200, 86400]

    from datetime import datetime, timedelta
    base = datetime.strptime(WEEK_START, "%Y-%m-%d %H:%M:%S")

    # Generate as a stream: each row's time = prev + gap (so the stream
    # builds session boundaries naturally).  Each row picks an IP and host
    # independently, which means some rows share IP with the previous
    # (extending a session) and some don't.
    rows = []
    cur = base + timedelta(seconds=rng.randint(0, 3600))
    for i in range(count):
        # Periodically reset to a random week-time so we exercise multiple
        # disjoint session clusters in the same IP.
        if rng.random() < 0.1:
            cur = base + timedelta(seconds=rng.randint(0, WEEK_SECONDS - 1))
        else:
            if rng.random() < 0.5:
                gap = rng.choice(gap_boundary)
            else:
                gap = rng.randint(0, 7200)
            cur = cur + timedelta(seconds=gap)
            # Clamp so we stay inside the week.
            if cur >= base + timedelta(seconds=WEEK_SECONDS):
                cur = base + timedelta(seconds=rng.randint(0, WEEK_SECONDS - 1))

        ip = rng.choice(IP_POOL)
        host = rng.choice(HOST_POOL)
        content = rng.choice(CONTENT_POOL)
        domain = "example.com" if host.endswith(".com") else (
            "example.org" if host.endswith(".org") else "")
        dt = cur.strftime("%Y-%m-%d %H:%M:%S")
        rows.append(f"('{dt}', '{ip}', '{content}', '{host}', '{domain}')")

    print("USE foo_metrics_test;")
    print("INSERT INTO web (datetime, ip, content, host, domain) VALUES")
    print(",\n".join(rows) + ";")


if __name__ == "__main__":
    main()
