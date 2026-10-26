"""UDFs for the observatory night-log migration. Pure Python, no table imports.

Each parser raises on input the legacy logging system let through (a sexagesimal coordinate with a
typo, a photometry field with a non-number, a zero exposure). Pixeltable stores the exception per row
and per column (`<col>.errortype` / `<col>.errormsg`) when the insert runs with `on_error='ignore'`,
so a bad row is kept, visible and fixable instead of aborting the whole migration.
"""

from __future__ import annotations

import math

import numpy as np
import pixeltable as pxt

FILTERS = ('U', 'B', 'V', 'R', 'I')


@pxt.udf
def parse_ra(ra_hms: str) -> float:
    """'05:35:17.3' (hours:minutes:seconds) -> right ascension in degrees."""
    h, m, s = (float(p) for p in ra_hms.strip().split(':'))
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
        raise ValueError(f'RA out of range: {ra_hms!r}')
    return round(15.0 * (h + m / 60 + s / 3600), 5)


@pxt.udf
def parse_dec(dec_dms: str) -> float:
    """'-05:23:28' (degrees:arcmin:arcsec) -> declination in degrees."""
    txt = dec_dms.strip()
    sign = -1.0 if txt.startswith('-') else 1.0
    d, m, s = (float(p) for p in txt.lstrip('+-').split(':'))
    if not (0 <= d <= 90 and 0 <= m < 60 and 0 <= s < 60):
        raise ValueError(f'Dec out of range: {dec_dms!r}')
    return round(sign * (d + m / 60 + s / 3600), 5)


@pxt.udf
def parse_mags(mags_raw: str) -> pxt.Array[(None,), pxt.Float]:
    """'12.31;12.29;12.40' (one magnitude per sub-exposure) -> float array."""
    vals = [float(v) for v in mags_raw.split(';') if v.strip()]
    if not vals:
        raise ValueError('no photometry values')
    return np.array(vals, dtype=np.float64)


@pxt.udf
def mag_stats(mags: pxt.Array[(None,), pxt.Float]) -> pxt.Json:
    """Mean, scatter and count of the sub-exposure magnitudes."""
    return {
        'mean': round(float(mags.mean()), 3),
        'std': round(float(mags.std()), 3),
        'n': int(mags.shape[0]),
    }


@pxt.udf
def altitude_deg(airmass: float) -> float:
    """Plane-parallel airmass X = sec(z) -> altitude above the horizon in degrees."""
    if airmass < 1.0:
        raise ValueError(f'airmass must be >= 1.0, got {airmass}')
    return round(90.0 - math.degrees(math.acos(1.0 / airmass)), 2)


@pxt.udf
def flux_rate(mean_mag: float, exposure_s: float) -> float:
    """Relative counts per second (zero point 25 mag); raises on a non-positive exposure."""
    if exposure_s <= 0:
        raise ValueError(f'exposure must be positive, got {exposure_s}')
    return round(10 ** (-0.4 * (mean_mag - 25.0)) / exposure_s, 3)
