"""Observatory night-log migration, built with Pixeltable.

A legacy telescope logging system exported years of observations as a CSV: coordinates as
sexagesimal strings, photometry as a semicolon-separated list of magnitudes, exposure and airmass
as numbers. Some rows were typed by tired humans at 3 AM. `pipeline.py` migrates the CSV into the
`Observations` table with `insert(rows, on_error='ignore')`: every row lands, parsing runs as
computed columns, and a row that fails to parse keeps its error on the column that failed
(`ra_deg.errortype`, `mags.errormsg`, ...) instead of aborting the batch or vanishing. Fix the
source value with `update()` and only the failed cells are recomputed.

Layout: UDFs live in udfs.py; this file holds table models and @pxt.query functions.

    pxt schema update app.py observatory
    python pipeline.py generate          # writes data/night_log.csv (deterministic sample)
    python pipeline.py migrate           # CSV -> dicts -> Observations.insert(..., on_error='ignore')
    python pipeline.py errors            # which rows failed, in which column, and why
"""

from __future__ import annotations

import pixeltable as pxt
from pixeltable.functions import array as pxt_array

from udfs import altitude_deg, flux_rate, mag_stats, parse_dec, parse_mags, parse_ra

TableModel = pxt.model_base()


class Observations(TableModel, name='observations'):
    """One logged exposure from the legacy night log, parsed by computed columns."""

    obs_id = pxt.Column(type=pxt.String, primary_key=True)
    night: pxt.String
    observer: pxt.String
    target: pxt.String
    ra_hms: pxt.String
    dec_dms: pxt.String
    band: pxt.String
    exposure_s: pxt.Float
    airmass: pxt.Float
    mags_raw: pxt.String

    ra_deg = parse_ra(ra_hms)
    dec_deg = parse_dec(dec_dms)
    mags = parse_mags(mags_raw)
    first_mag = mags[0]
    mag_list = pxt_array.to_list(mags)
    stats = mag_stats(mags)
    mean_mag = stats['mean'].astype(pxt.Float)  # bracket access: `stats.mean` would be the Expr method, not the JSON key
    altitude = altitude_deg(airmass)
    rate = flux_rate(mean_mag, exposure_s)

    __indexes__ = [pxt.BtreeIndex(night), pxt.BtreeIndex(target)]


class Clean(TableModel, name='clean', base=Observations.where(Observations.rate != None)):
    """A view of the rows that parsed end to end: what downstream analysis reads."""

    scatter = Observations.stats['std'].astype(pxt.Float)


@pxt.query
def night_summary(night: str):
    """Every parsed exposure of one night, brightest first."""
    return (
        Observations.where((Observations.night == night) & (Observations.rate != None))
        .order_by(Observations.mean_mag)
        .select(Observations.obs_id, Observations.target, Observations.band, Observations.mean_mag,
                Observations.altitude, Observations.rate)
    )


@pxt.query
def target_light_curve(target: str):
    """One target's mean magnitude per exposure, in log order: a quick light curve."""
    return (
        Observations.where((Observations.target == target) & (Observations.mean_mag != None))
        .order_by(Observations.obs_id)
        .select(Observations.obs_id, Observations.night, Observations.band, Observations.mean_mag,
                Observations.mag_list)
    )
