#!/usr/bin/env python3
"""Generate random apache log lines for fuzzing import-apache.

Mixes:
  - new-format lines (23 capture groups)
  - old-format lines (14 capture groups)
  - garbage lines (should be unrecognized — neither pattern matches)

Targets the regex dispatch, URL filter, method filter, status/bytes
filter, and excluded-suffix branches in xlogimport_apache.php.

Usage: gen_apache_log.py <count> <seed>

Writes raw log lines (one per line) to stdout.  Pipe directly to a file
and pass that file path to both legacy and new importers.
"""
import random
import sys


METHODS_OK    = ['GET', 'POST']
METHODS_BAD   = ['HEAD', 'PUT', 'DELETE', 'OPTIONS', 'PATCH']
STATUS_POOL   = [200, 200, 200, 200, 301, 304, 403, 404, 500]
PROTOCOLS     = ['HTTP/1.1', 'HTTP/1.0', 'HTTP/2.0']

# URL pool exercising every branch of the filter chain in xlogimport_apache.php.
URL_POOL_OK = [
    '/page', '/p1', '/page2', '/topics/intro',
    '/groups/abc/wiki', '/profile/123',
    # download — gets dnload via backfill
    '/resources/123/download/file.zip',
    '/resources/456/download/data.csv',
    # resource paths — pass even if extension-excluded
    '/resources/789/index.html',
    '/resources/12/img.png',     # would be excluded by suffix BUT under /resources/
]
URL_POOL_FILTERED = [
    '/styles/main.css',           # css excluded
    '/scripts/app.js',            # js excluded
    '/images/logo.png',           # png excluded
    '/templates/foo',             # /templates/ excluded
    '/administrator/index.php',   # /administrator/ excluded
    '/webdav/file',               # /webdav/ excluded
    '/api/v1/things',             # /api/ excluded
    '/cron/tick/now',             # /cron/tick/ excluded
]

# User agent pool — vary length/shape, but never anything that looks like
# an exact match for bot_useragents (which the importer filters out).
UA_POOL = [
    'Mozilla/5.0 (X11; Linux x86_64) Chrome/120',
    'Mozilla/5.0 (Windows NT 10.0; Win64) Firefox/130',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Safari/17',
    'curl/8.4.0',
    'PostmanRuntime/7.32.0',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0) Mobile Safari',
    '-',
]

# IPs from documentation ranges only.
IP_POOL = ['203.0.113.10', '203.0.113.20', '198.51.100.30', '192.0.2.40']

TIMEZONES = ['EDT', 'EST', 'UTC', 'PDT', 'CDT']
SSLPORTS  = ['TLSv1.3', 'TLSv1.2', '-', 'none', 'sslv3']


def rand_date(rng):
    day  = rng.randint(1, 28)
    hour = rng.randint(0, 23)
    return f'2025-07-{day:02d}', f'{hour:02d}:{rng.randint(0,59):02d}:{rng.randint(0,59):02d}'


def gen_new_line(rng):
    d, t = rand_date(rng)
    tz   = rng.choice(TIMEZONES)
    pid  = rng.randint(1000, 99999)
    user = rng.choice(['-', '-', '-', 'alice', 'bob'])
    method = rng.choice(METHODS_OK + METHODS_BAD)
    url    = rng.choice(URL_POOL_OK + URL_POOL_FILTERED)
    proto  = rng.choice(PROTOCOLS)
    status = rng.choice(STATUS_POOL)
    nbytes = rng.choice([0, 0, rng.randint(100, 100000)])
    ip     = rng.choice(IP_POOL)
    ref    = rng.choice(['-', 'https://example.com/', '-'])
    ua     = rng.choice(UA_POOL)
    ssl    = rng.choice(SSLPORTS)
    ts     = rng.randint(0, 5000)
    tms    = rng.randint(0, 200000)
    # Trailing 8 fields: each must start with a non-underscore.  Use '-'
    # most of the time, but occasionally vary.
    trailing = [rng.choice(['-', '-', '-', 'auth', 'comp', 'view'])
                for _ in range(8)]
    return (f'{d} {t} {tz} {pid} {user} "{method} {url} {proto}" '
            f'{status} {nbytes} {ip} "{ref}" "{ua}" {ssl} {ts} {tms} '
            + ' '.join(trailing))


def gen_old_line(rng):
    d, t = rand_date(rng)
    tz   = rng.choice(TIMEZONES)
    user = rng.choice(['-', '-', 'alice', 'bob'])
    method = rng.choice(METHODS_OK + METHODS_BAD)
    url    = rng.choice(URL_POOL_OK + URL_POOL_FILTERED)
    proto  = rng.choice(PROTOCOLS)
    status = rng.choice(STATUS_POOL)
    nbytes = rng.choice([0, 0, rng.randint(100, 100000)])
    ip     = rng.choice(IP_POOL)
    ref    = rng.choice(['-', 'https://example.com/'])
    ua     = rng.choice(UA_POOL)
    ssl    = rng.choice(SSLPORTS)
    ts     = rng.randint(0, 5000)
    tms    = rng.randint(0, 200000)
    cookie = rng.choice(['-', 'sid=abc123', 'token=xyz'])
    return (f'{d} {t} {tz} {user} "{method} {url} {proto}" '
            f'{status} {nbytes} {ip} "{ref}" "{ua}" {ssl} {ts} {tms} {cookie}')


def gen_garbage_line(rng):
    # Should match neither pattern.
    shapes = [
        '',  # empty
        '#comment line',
        'totally not a log line',
        '2025-07-10 missing fields here',
        '   ',  # whitespace only
    ]
    return rng.choice(shapes)


def main():
    if len(sys.argv) != 3:
        print("usage: gen_apache_log.py <count> <seed>", file=sys.stderr)
        sys.exit(2)
    count = int(sys.argv[1])
    seed = int(sys.argv[2])
    rng = random.Random(seed)

    for _ in range(count):
        r = rng.random()
        if   r < 0.50: print(gen_new_line(rng))
        elif r < 0.85: print(gen_old_line(rng))
        else:          print(gen_garbage_line(rng))


if __name__ == '__main__':
    main()
