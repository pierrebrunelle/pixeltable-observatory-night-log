"""Batch migration of a legacy observatory night log into Pixeltable (no server).

    python pipeline.py generate             # deterministic sample: data/night_log.csv (240 exposures, 6 nights)
    python pipeline.py archive              # keep the raw CSV rows as-is: pxt.io.import_rows -> observatory_raw/night_log
    python pipeline.py migrate              # CSV -> dicts -> Observations.insert(rows, on_error='ignore')
    python pipeline.py errors               # failed cells per computed column: errortype + errormsg
    python pipeline.py fix                  # apply data/corrections.csv with batch_update; only failed cells recompute
    python pipeline.py report               # per-night summary + one target's light curve
    python pipeline.py export               # Clean view -> output/clean_observations.csv

Every command takes --target (default 'observatory'); use pxt://<org>:<db> for a cloud database.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import random
from pathlib import Path

import pixeltable as pxt

HERE = Path(__file__).parent
CSV_PATH = HERE / 'data' / 'night_log.csv'
FIX_PATH = HERE / 'data' / 'corrections.csv'
FIELDS = ['obs_id', 'night', 'observer', 'target', 'ra_hms', 'dec_dms', 'band', 'exposure_s', 'airmass', 'mags_raw']
COMPUTED = ['ra_deg', 'dec_deg', 'mags', 'altitude', 'rate']

# target, RA (h, m, s), Dec (sign, d, m, s), base magnitude, variability amplitude
TARGETS = [
    ('M42 Theta1 Ori C', (5, 35, 16.5), (-1, 5, 23, 23.0), 5.1, 0.02),
    ('Algol', (3, 8, 10.1), (1, 40, 57, 20.3), 2.1, 1.20),
    ('Delta Cep', (22, 29, 10.3), (1, 58, 24, 54.7), 4.0, 0.80),
    ('RR Lyr', (19, 25, 27.9), (1, 42, 47, 3.7), 7.6, 0.90),
    ('SN 2026abc', (12, 21, 54.9), (1, 4, 28, 25.6), 15.8, 0.40),
    ('Vega', (18, 36, 56.3), (1, 38, 47, 1.3), 0.03, 0.01),
    ('Betelgeuse', (5, 55, 10.3), (1, 7, 24, 25.4), 0.5, 0.30),
    ('Mira', (2, 19, 20.8), (-1, 2, 58, 39.5), 6.5, 2.50),
]
OBSERVERS = ['ana', 'kofi', 'li', 'marta', 'raj']
BANDS = ['B', 'V', 'R', 'I']

# Typos the legacy logger accepted, one kind per bad row: (field, bad value, what a human would correct it to)
BAD_KINDS = [
    ('ra_hms', lambda r: r['ra_hms'].replace('0', 'O', 1) or 'O' + r['ra_hms']),  # letter O for zero
    ('ra_hms', lambda r: '25' + r['ra_hms'][2:]),                                 # hour 25
    ('dec_dms', lambda r: r['dec_dms'].replace(':', ' ', 1)),                      # space instead of colon
    ('mags_raw', lambda r: r['mags_raw'].split(';')[0] + ';n/a;' + r['mags_raw'].split(';')[-1]),
    ('mags_raw', lambda r: ''),                                                    # photometry never saved
    ('airmass', lambda r: 0.95),                                                   # below 1.0: impossible
    ('exposure_s', lambda r: 0.0),                                                 # shutter never opened
]


def sexa(h_or_d, m, s, sign=None) -> str:
    txt = f'{h_or_d:02d}:{m:02d}:{s:04.1f}'
    return txt if sign is None else ('-' if sign < 0 else '+') + txt


def gen_rows(n: int, bad_every: int, seed: int = 42) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    nights = [(dt.date(2026, 10, 14) + dt.timedelta(days=i)).isoformat() for i in range(6)]
    rows, fixes = [], []
    for i in range(n):
        name, ra, (sg, d, m, s), base, amp = TARGETS[i % len(TARGETS)]
        night = nights[(i * len(nights)) // n]
        n_sub = rng.randint(3, 6)
        level = base + amp * rng.uniform(-1, 1)
        mags = ';'.join(f'{level + rng.gauss(0, 0.015):.3f}' for _ in range(n_sub))
        row = {
            'obs_id': f'N{night[5:7]}{night[8:10]}-{i:05d}', 'night': night, 'observer': rng.choice(OBSERVERS),
            'target': name, 'ra_hms': sexa(*ra), 'dec_dms': sexa(d, m, s, sg), 'band': rng.choice(BANDS),
            'exposure_s': float(rng.choice([5, 10, 30, 60, 120])), 'airmass': round(rng.uniform(1.0, 2.4), 3),
            'mags_raw': mags,
        }
        if bad_every and i % bad_every == bad_every - 1:
            field, breaker = BAD_KINDS[(i // bad_every) % len(BAD_KINDS)]
            fixes.append({'obs_id': row['obs_id'], 'field': field, 'value': row[field]})
            row[field] = breaker(row)
        rows.append(row)
    return rows, fixes


def read_csv(path: Path) -> list[dict]:
    with open(path, newline='') as f:
        rows = list(csv.DictReader(f))
    for r in rows:  # the CSV is all strings; the numeric columns are clean, so cast them here
        r['exposure_s'] = float(r['exposure_s'])
        r['airmass'] = float(r['airmass'])
    return rows


def cmd_generate(a) -> None:
    rows, fixes = gen_rows(a.rows, a.bad_every)
    CSV_PATH.parent.mkdir(exist_ok=True)
    with open(CSV_PATH, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(FIX_PATH, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['obs_id', 'field', 'value'])
        w.writeheader()
        w.writerows(fixes)
    print(f'wrote {len(rows)} rows to {CSV_PATH.relative_to(HERE)} ({len(fixes)} with a legacy typo) '
          f'and {len(fixes)} corrections to {FIX_PATH.relative_to(HERE)}')


def sibling_dir(target: str, suffix: str) -> str:
    """`name` -> `name_<suffix>` next to the schema dir; a cloud database root `pxt://<org>:<db>` -> `pxt://<org>:<db>/<suffix>`."""
    _, sep, rest = target.partition('://')
    if sep and '/' not in rest:  # a cloud database root: the tables sit there, so use a subdirectory
        return f'{target}/{suffix}'
    return f'{target}_{suffix}'


def cmd_archive(a) -> None:
    rows = read_csv(Path(a.csv))
    raw_dir = sibling_dir(a.target, 'raw')
    pxt.create_dir(raw_dir, if_exists='ignore')
    path = f'{raw_dir}/night_log'
    pxt.drop_table(path, if_not_exists='ignore')
    t = pxt.io.import_rows(path, rows, primary_key='obs_id', comment='legacy night log, verbatim')
    print(f'archived {t.count()} raw rows to {path}')
    print(t.get_metadata()['schema'] if 'schema' in t.get_metadata() else t.columns())


def cmd_migrate(a) -> None:
    t = pxt.get_table(f'{a.target}/observations')
    rows = read_csv(Path(a.csv))
    status = t.insert(rows, on_error='ignore')
    print(f'inserted {status.num_rows} rows; {status.num_excs} cell errors (rows kept, errors stored per column)')
    print(f'observations: {t.count()} rows; clean view: {pxt.get_table(f"{a.target}/clean").count()} rows')


def cmd_errors(a) -> None:
    t = pxt.get_table(f'{a.target}/observations')
    total = 0
    for col in COMPUTED:
        c = t[col]
        res = (t.where(c.errortype != None)
               .select(t.obs_id, err=c.errortype, msg=c.errormsg)
               .order_by(t.obs_id).collect())
        total += len(res)
        print(f'{col}: {len(res)} failed')
        for r in list(res)[: a.limit]:
            print(f'  {r["obs_id"]}  {r["err"]}: {str(r["msg"])[:90]}')
    print(f'total failed cells: {total}')


def cmd_fix(a) -> None:
    t = pxt.get_table(f'{a.target}/observations')
    with open(FIX_PATH, newline='') as f:
        fixes = list(csv.DictReader(f))
    rows = []
    for fx in fixes:
        val = float(fx['value']) if fx['field'] in ('exposure_s', 'airmass') else fx['value']
        rows.append({'obs_id': fx['obs_id'], fx['field']: val})
    by_field: dict[str, list[dict]] = {}
    for r in rows:
        by_field.setdefault(next(k for k in r if k != 'obs_id'), []).append(r)
    for field, batch in by_field.items():
        st = t.batch_update(batch)
        print(f'batch_update {field}: {st.num_rows} rows updated, {st.num_excs} errors')
    left = sum(t.where(t[c].errortype != None).count() for c in COMPUTED)
    print(f'failed cells left: {left}; clean view: {pxt.get_table(f"{a.target}/clean").count()} rows')


def cmd_report(a) -> None:
    t = pxt.get_table(f'{a.target}/observations')
    res = (t.where(t.rate != None)
           .group_by(t.night)
           .select(t.night, n=pxt.functions.count(t.obs_id), brightest=pxt.functions.min(t.mean_mag),
                   mean_alt=pxt.functions.mean(t.altitude))
           .order_by(t.night).collect())
    print('night       n   brightest  mean_alt')
    for r in res:
        print(f'{r["night"]}  {r["n"]:>3}  {r["brightest"]:>8.3f}  {r["mean_alt"]:>7.1f}')
    lc = (t.where((t.target == a.light_curve) & (t.mean_mag != None))
          .select(t.obs_id, t.band, t.mean_mag, t.first_mag, n=t.stats['n']).order_by(t.obs_id).limit(8).collect())
    print(f'\nlight curve: {a.light_curve} (first 8)')
    for r in lc:
        print(f'  {r["obs_id"]}  {r["band"]}  mean={r["mean_mag"]:.3f}  first={r["first_mag"]:.3f}  n={r["n"]}')


def cmd_export(a) -> None:
    v = pxt.get_table(f'{a.target}/clean')
    out = HERE / 'output' / 'clean_observations.csv'
    out.parent.mkdir(exist_ok=True)
    q = v.select(v.obs_id, v.night, v.target, v.band, v.ra_deg, v.dec_deg, v.mean_mag, v.scatter, v.altitude, v.rate)
    pxt.io.export_csv(q.order_by(v.obs_id), out)
    print(f'exported {v.count()} clean rows to {out.relative_to(HERE)}')


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--target', default='observatory', help="catalog dir, or pxt://<org>:<db>")
    sub = p.add_subparsers(dest='cmd', required=True)
    g = sub.add_parser('generate')
    g.add_argument('--rows', type=int, default=240)
    g.add_argument('--bad-every', type=int, default=12, help='every Nth row gets a legacy typo (0 = none)')
    for name in ('archive', 'migrate'):
        sp = sub.add_parser(name)
        sp.add_argument('--csv', default=str(CSV_PATH))
    e = sub.add_parser('errors')
    e.add_argument('--limit', type=int, default=3)
    sub.add_parser('fix')
    r = sub.add_parser('report')
    r.add_argument('--light-curve', default='Algol')
    sub.add_parser('export')
    a = p.parse_args()
    globals()[f'cmd_{a.cmd}'](a)


if __name__ == '__main__':
    main()
