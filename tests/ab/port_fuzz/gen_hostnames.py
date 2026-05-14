#!/usr/bin/env python3
"""Generate random hostnames covering every get_domain() branch.

Usage:
  gen_hostnames.py <count> <seed>

Emits SQL INSERT statements on stdout (one big multi-row INSERT).
Each hostname is paired with a synthetic date in 2025-07 and a unique IP.
"""
import random
import string
import sys


# TLD pools that exercise the different promotion branches.
INT_3LEVEL = ['com', 'net', 'org', 'edu', 'gov', 'mil',
              'ac', 'co', 'ne', 'or', 'ed']
MIL_3LEVEL = ['af', 'army', 'navy']
US_4LEVEL  = ['k12', 'lib', 'cc', 'tec']
TWO_LETTER_CCTLD = ['us', 'uk', 'jp', 'cn', 'br', 'fr', 'de', 'au', 'in',
                    'ca', 'mx', 'kr', 'cl', 'ar', 'it', 'es', 'pl', 'se']
THREE_PLUS_TLD = ['info', 'name', 'biz', 'tech', 'travel', 'museum']


def rand_label(rng, min_len=1, max_len=15):
    """A DNS label: lowercase letters/digits/hyphens (no leading/trailing hyphen)."""
    n = rng.randint(min_len, max_len)
    chars = string.ascii_lowercase + string.digits + '-'
    while True:
        s = ''.join(rng.choices(chars, k=n))
        if not s.startswith('-') and not s.endswith('-'):
            return s


def rand_hostname(rng):
    """Generate a hostname targeting one of the get_domain() shape buckets."""
    shape = rng.choice([
        'normal_2',         # foo.com
        'int_3',            # foo.bar.com (int_3level promotion)
        '2x2',              # foo.aa.bb (2-letter / 2-letter)
        'no2_ub',           # foo.ub.cc (no2_3level exception)
        'mil_3',            # host.army.mil
        'us_4',             # school.k12.va.us
        'us_4_not',         # something.foo.bar.us (NOT us_4level)
        'hyphen_tail',      # a-b-c-d-foo.com (4+ hyphens)
        'underscore_tail',  # a_b_c_d_foo.com (3+ underscores)
        'deep_chain',       # a.b.c.d.e.f.com
        'single_label',     # localhost
        'qmark',            # '?'
        'numeric_label',    # 192.168.1.1 (looks like IP)
        'leading_dot',      # .example.com
        'trailing_dot',     # example.com.
    ])

    if shape == 'normal_2':
        return f"{rand_label(rng)}.{rand_label(rng)}.{rng.choice(INT_3LEVEL[:5])}"
    elif shape == 'int_3':
        return f"{rand_label(rng)}.{rand_label(rng)}.{rng.choice(INT_3LEVEL)}.{rng.choice(TWO_LETTER_CCTLD)}"
    elif shape == '2x2':
        return f"{rand_label(rng)}.{rand_label(rng)}.{rng.choice(TWO_LETTER_CCTLD)}.{rng.choice(TWO_LETTER_CCTLD)}"
    elif shape == 'no2_ub':
        return f"{rand_label(rng)}.{rand_label(rng)}.ub.{rng.choice(TWO_LETTER_CCTLD)}"
    elif shape == 'mil_3':
        return f"{rand_label(rng)}.{rng.choice(MIL_3LEVEL)}.mil"
    elif shape == 'us_4':
        return f"{rand_label(rng)}.{rand_label(rng)}.{rng.choice(US_4LEVEL)}.{rand_label(rng, 2, 2)}.us"
    elif shape == 'us_4_not':
        return f"{rand_label(rng)}.{rand_label(rng)}.{rand_label(rng, 2, 4)}.{rand_label(rng, 2, 2)}.us"
    elif shape == 'hyphen_tail':
        # SLD shape like a-b-c-d-foo (4 hyphens)
        parts = [rand_label(rng, 1, 5) for _ in range(5)]
        sld = '-'.join(parts)
        return f"{rand_label(rng)}.{sld}.{rng.choice(INT_3LEVEL[:5])}"
    elif shape == 'underscore_tail':
        parts = [rand_label(rng, 1, 5) for _ in range(4)]
        sld = '_'.join(parts)
        # Random hyphen/underscore separator before the tail
        sep = rng.choice(['-', '_'])
        tail = rand_label(rng, 1, 5)
        return f"{rand_label(rng)}.{sld}{sep}{tail}.{rng.choice(INT_3LEVEL[:5])}"
    elif shape == 'deep_chain':
        depth = rng.randint(5, 8)
        return '.'.join([rand_label(rng) for _ in range(depth)])
    elif shape == 'single_label':
        return rand_label(rng)
    elif shape == 'qmark':
        return '?'
    elif shape == 'numeric_label':
        return f"{rng.randint(1,255)}.{rng.randint(0,255)}.{rng.randint(0,255)}.{rng.randint(0,255)}"
    elif shape == 'leading_dot':
        return f".{rand_label(rng)}.{rng.choice(INT_3LEVEL[:5])}"
    elif shape == 'trailing_dot':
        return f"{rand_label(rng)}.{rng.choice(INT_3LEVEL[:5])}."


def main():
    if len(sys.argv) != 3:
        print("usage: gen_hostnames.py <count> <seed>", file=sys.stderr)
        sys.exit(2)
    count = int(sys.argv[1])
    seed = int(sys.argv[2])
    rng = random.Random(seed)

    print("USE foo_metrics_test;")
    rows = []
    for i in range(count):
        host = rand_hostname(rng)
        # Random datetime within July 2025 (one second granularity is fine).
        day = rng.randint(1, 30)
        hour = rng.randint(0, 23)
        minute = rng.randint(0, 59)
        second = rng.randint(0, 59)
        dt = f"2025-07-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
        # Unique IP per row
        ip = f"10.{i // 65536 % 256}.{i // 256 % 256}.{i % 256}"
        # SQL-escape: replace single quotes; PHP/Python both accept
        host_esc = host.replace("'", "''")
        rows.append(f"('{dt}', '{ip}', '/p{i}', '{host_esc}', '')")
    print("INSERT INTO web (datetime, ip, content, host, domain) VALUES")
    print(',\n'.join(rows) + ';')


if __name__ == '__main__':
    main()
