#!/usr/bin/env python3
"""Generate random CMS auth-log lines for fuzzing import-auth.

Targets the two regex patterns in xlogimport_authlog.php:
  pattern_1: 'date time uid [user] ip action'   (bracketed user, with uid)
  pattern_2: 'date time user ip action'         (no brackets, no uid)
Plus garbage lines that should be unrecognized.

Also exercises the hubstatus/hubadmin user filter and the excluded-IP
filter (where applicable).

Usage: gen_auth_log.py <count> <seed>

Writes raw log lines to stdout.
"""
import random
import sys

USERS = ['alice', 'bob', 'carol', 'dave', 'eve',
         'hubstatus', 'hubadmin',  # filtered out
         'user1', 'guest']
ACTIONS = ['login', 'logout', 'sim_login', 'sim_logout', 'failed']
IPS = ['203.0.113.10', '203.0.113.20', '198.51.100.30', '192.0.2.40']


def rand_dt(rng):
    day = rng.randint(1, 28)
    h, m, s = rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)
    return f'2025-07-{day:02d}', f'{h:02d}:{m:02d}:{s:02d}'


def gen_pat1(rng):
    """pattern_1: date time uid [user] ip action"""
    d, t = rand_dt(rng)
    uid = rng.randint(1, 99999)
    user = rng.choice(USERS)
    ip = rng.choice(IPS)
    action = rng.choice(ACTIONS)
    return f'{d} {t} {uid} [{user}] {ip} {action}'


def gen_pat2(rng):
    """pattern_2: date time user ip action"""
    d, t = rand_dt(rng)
    user = rng.choice(USERS)
    ip = rng.choice(IPS)
    action = rng.choice(ACTIONS)
    return f'{d} {t} {user} {ip} {action}'


def gen_garbage(rng):
    return rng.choice([
        '',
        '# comment',
        '2025-07-01 broken',
        'totally invalid',
        '   ',
    ])


def main():
    if len(sys.argv) != 3:
        print("usage: gen_auth_log.py <count> <seed>", file=sys.stderr)
        sys.exit(2)
    count = int(sys.argv[1])
    seed = int(sys.argv[2])
    rng = random.Random(seed)
    for _ in range(count):
        r = rng.random()
        if   r < 0.50: print(gen_pat1(rng))
        elif r < 0.85: print(gen_pat2(rng))
        else:          print(gen_garbage(rng))


if __name__ == '__main__':
    main()
