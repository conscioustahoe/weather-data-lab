"""Plant known faults into clean station data so QA checks can be scored.

A QA check's recall (how many real problems it catches) can only be measured against
a known answer. Real data doesn't come with one, so we make it. We take stations we
trust (the *baseline*), break copies of some of them in controlled ways and record
exactly what was broken, where, when and by how much. The checks in notebook 05 are
then scored against that record.

Each fault type imitates something that really happens to weather stations:

- **step**: the reading jumps by a fixed amount on a date and stays there. That's what
  an instrument swap, a recalibration or a move to a new spot looks like. Published
  steps from instrument changes in US networks are a few tenths of a degree up to about
  1 °C (Quayle et al. 1991 found about -0.4 °C in daily maxima and +0.3 °C in minima
  when the cooperative network switched thermometers; Menne & Williams 2009 describe
  detecting breaks of this size with neighbour comparisons).
- **drift**: the error grows slowly and linearly until the sensor gets replaced. That's
  an ageing sensor or a radiation shield that gets dirtier over months.
- **bias**: a constant offset over the whole fault period from day one, like a sensor
  sited next to asphalt. It's expected to be close to undetectable without metadata,
  because it looks exactly like an honest representativeness difference.
- **stuck**: the sensor repeats one value for hours or days.
- **time offset**: the values are right but labelled with the wrong hour. An offset of
  8 hours imitates local Pacific time recorded as UTC.
- **storm gaps**: values go missing during storms, when wind and rain knock out power
  or communications. Nothing is wrong with the values that remain, but the missing ones
  aren't random, so the dataset under-represents the weather that matters most.

Design choices:

- Faults go only into 2021–2023. 2024 stays clean so notebook 09 can test a model on it.
- A fixed set of *held-out* stations never gets faults, also for notebook 09.
- One fault per faulty station so every detection can be attributed to one cause.
- About 25% of baseline stations are faulty. The rest are untouched, so checks can
  raise false alarms and precision means something.
- Sizes come from a grid per fault type, so results become "recall against size".
- Everything is driven by a seed. One seed gives the canonical dataset that notebooks
  05 and 09 use. Many seeds give recall curves with error bars.
- Values are never deleted. The true value stays in ``air_temperature_true`` beside the
  observed value ``air_temperature``. A gap sets the observed value to NaN.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FAULT_START = pd.Timestamp("2021-01-01", tz="UTC")
FAULT_END = pd.Timestamp("2024-01-01", tz="UTC")    # exclusive: 2024 stays clean

# Size grid for each fault type, with the unit of the size.
SIZES = {
    "step": ([-3.0, -2.0, -1.0, -0.5, 0.5, 1.0, 2.0, 3.0], "°C"),
    "drift": ([-3.0, -2.0, -1.0, -0.5, 0.5, 1.0, 2.0, 3.0], "°C at the end of the drift"),
    "bias": ([-3.0, -2.0, -1.0, -0.5, 0.5, 1.0, 2.0, 3.0], "°C"),
    "stuck": ([6, 24, 72, 168], "hours"),
    "time_offset": ([1, 3, 8], "hours"),
    "storm_gaps": ([0.3, 0.5, 0.7, 0.9], "share of storm hours removed"),
}
FAULT_TYPES = list(SIZES)

# Baseline selection thresholds (see notebook 04).
MIN_COMPLETENESS = 0.90        # share of 2021–2024 hours with a temperature and a reference value
MAX_MONTHLY_SPREAD = 0.60      # °C, std of the monthly mean station-minus-HRRR difference
MAX_GROSS_HOURS = 24           # hours with |station - HRRR| > 15 °C
GROSS_DIFF = 15.0


def select_baseline(matched: pd.DataFrame) -> pd.DataFrame:
    """Stations we trust enough to plant faults into, with the numbers behind the choice.

    ``matched`` is the output of :func:`wxlab.reference.match`. A station qualifies if
    its record is nearly complete, its monthly mean difference from HRRR is steady, and
    it has almost no hours that disagree with HRRR by more than 15 °C. Passing doesn't
    prove a station is clean. It means we can't see a fault in it.
    """
    ok = matched.dropna(subset=["air_temperature", "hrrr_air_temperature"])
    n_hours = len(pd.date_range("2021-01-01", "2025-01-01", freq="h", inclusive="left"))
    month = ok.time.dt.tz_localize(None).dt.to_period("M")
    monthly = ok.groupby(["station_id", month]).diff_air_temperature.mean().unstack()
    stats = pd.DataFrame({
        "completeness": ok.groupby("station_id").size() / n_hours,
        "monthly_spread": monthly.std(axis=1),
        "gross_hours": (ok.diff_air_temperature.abs() > GROSS_DIFF).groupby(ok.station_id).sum(),
    })
    dups = duplicate_pairs(ok)
    # Of each duplicate pair keep the more complete station and mark the other.
    stats["duplicate_of"] = None
    for a, b in dups[["station_a", "station_b"]].itertuples(index=False):
        keep, drop = (a, b) if stats.completeness.get(a, 0) >= stats.completeness.get(b, 0) else (b, a)
        stats.loc[drop, "duplicate_of"] = keep
    stats["baseline"] = ((stats.completeness >= MIN_COMPLETENESS)
                         & (stats.monthly_spread <= MAX_MONTHLY_SPREAD)
                         & (stats.gross_hours <= MAX_GROSS_HOURS)
                         & stats.duplicate_of.isna())
    return stats


def duplicate_pairs(matched: pd.DataFrame, min_identical=0.5, min_overlap=24 * 30) -> pd.DataFrame:
    """Pairs of station ids that report the same values most of the time.

    ISD sometimes carries one physical station under two ids, for example after a base
    is renamed or when a second archive republishes the same reports. Nearby stations
    have highly correlated temperatures too, but they almost never report the *same*
    value hour after hour, so the test is the share of overlapping hours with identical
    values. Duplicates matter because a duplicate isn't an independent witness for a
    neighbour check, and in a train/test split one twin would leak into the other.
    """
    ok = matched.dropna(subset=["air_temperature"])
    wide = ok.pivot_table(index="time", columns="station_id", values="air_temperature")
    corr = wide.corr(min_periods=min_overlap)
    corr.values[np.tril_indices_from(corr)] = np.nan
    candidates = corr.stack()
    candidates = candidates[candidates > 0.98].index        # cheap filter before the exact test
    rows = []
    for a, b in candidates:
        both = wide[[a, b]].dropna()
        same = float((both[a] == both[b]).mean())
        if same >= min_identical:
            rows.append({"station_a": a, "station_b": b, "overlap_hours": len(both), "identical_share": same})
    return pd.DataFrame(rows, columns=["station_a", "station_b", "overlap_hours", "identical_share"])


def holdout_stations(baseline_ids, share=0.2, seed=2024) -> list[str]:
    """A fixed random set of baseline stations that never get faults (for notebook 09)."""
    ids = sorted(baseline_ids)
    rng = np.random.default_rng(seed)
    return sorted(rng.choice(ids, size=round(share * len(ids)), replace=False).tolist())


def storm_hours(df: pd.DataFrame, wind_quantile=0.98, rain_mm=1.0) -> pd.Series:
    """True for a station's windiest 2% of hours and any hour with at least 1 mm of rain.

    The wind threshold is per station because a breezy coastal airport and a sheltered
    valley have very different "windy". Many stations don't report rain at all, so
    wind carries the definition there.
    """
    wind_cut = df.groupby("station_id").wind_speed.transform(lambda w: w.quantile(wind_quantile))
    return (df.wind_speed >= wind_cut) | (df.precipitation_1h >= rain_mm)


# --------------------------------------------------------------------------- fault functions
# Each takes one station's rows sorted by time (a copy) plus the fault parameters and
# returns the new observed temperature and dew point and a boolean "affected" mask.

def _window(times, start, end):
    return ((times >= start) & (times < end)).to_numpy()


def apply_step(s: pd.DataFrame, size, start, end=FAULT_END):
    w = _window(s.time, start, end)
    t = s.air_temperature.to_numpy().copy()
    t[w] += size
    return t, s.dew_point_temperature.to_numpy(), w


def apply_drift(s: pd.DataFrame, size, start, end):
    """Linear ramp from 0 at ``start`` to ``size`` at ``end``, then the sensor is replaced."""
    w = _window(s.time, start, end)
    frac = ((s.time - start) / (end - start)).to_numpy(dtype=float)
    t = s.air_temperature.to_numpy().copy()
    t[w] += size * frac[w]
    return t, s.dew_point_temperature.to_numpy(), w


def apply_bias(s: pd.DataFrame, size, start=FAULT_START, end=FAULT_END):
    return apply_step(s, size, start, end)


def apply_stuck(s: pd.DataFrame, hours, start):
    """Repeat the first value at or after ``start`` for ``hours`` hours."""
    end = start + pd.Timedelta(hours=hours)
    w = _window(s.time, start, end)
    t = s.air_temperature.to_numpy().copy()
    if w.any():
        t[w] = t[np.argmax(w)]
    return t, s.dew_point_temperature.to_numpy(), w


def apply_time_offset(s: pd.DataFrame, hours, start, end):
    """Label each value ``hours`` too early: the value stored at t was measured at t + hours.

    That's what happens when a station records Pacific local time and the archive takes
    it as UTC (local = UTC - 8, so a reading labelled 14:00 was really taken at 22:00 UTC).
    Both temperature and dew point shift, since the whole report is mislabelled.
    """
    w = _window(s.time, start, end)
    by_time = s.set_index("time")[["air_temperature", "dew_point_temperature"]]
    shifted = by_time.reindex(s.time + pd.Timedelta(hours=hours)).to_numpy()
    t = s.air_temperature.to_numpy().copy()
    d = s.dew_point_temperature.to_numpy().copy()
    t[w], d[w] = shifted[w, 0], shifted[w, 1]
    return t, d, w


def apply_storm_gaps(s: pd.DataFrame, share, storm: np.ndarray, rng, start=FAULT_START, end=FAULT_END):
    """Remove ``share`` of the station's storm hours in the fault period (whole report lost)."""
    cand = storm & _window(s.time, start, end)
    w = cand & (rng.random(len(s)) < share)
    t = s.air_temperature.to_numpy().copy()
    d = s.dew_point_temperature.to_numpy().copy()
    t[w] = np.nan
    d[w] = np.nan
    return t, d, w


# --------------------------------------------------------------------------- planting

def _random_start(rng, earliest="2021-03-01", latest="2023-06-30"):
    lo, hi = pd.Timestamp(earliest, tz="UTC"), pd.Timestamp(latest, tz="UTC")
    return (lo + pd.Timedelta(hours=int(rng.integers(0, (hi - lo) / pd.Timedelta(hours=1))))).floor("h")


def plan_faults(baseline_ids, holdout, seed=0, share=0.25) -> pd.DataFrame:
    """Decide which stations get which fault, at what size and when. Returns the catalogue.

    Faulty stations are drawn from the baseline minus the held-out stations. Fault types
    are dealt out in turn so each type gets the same number of stations (give or take
    one), and sizes are drawn from each type's grid.
    """
    rng = np.random.default_rng(seed)
    pool = sorted(set(baseline_ids) - set(holdout))
    n = round(share * len(baseline_ids))
    chosen = rng.choice(pool, size=n, replace=False)
    types = [FAULT_TYPES[i % len(FAULT_TYPES)] for i in rng.permutation(n)]
    rows = []
    for fid, (sid, ftype) in enumerate(zip(chosen, types)):
        size = rng.choice(SIZES[ftype][0])
        if ftype in ("bias", "storm_gaps"):
            start, end = FAULT_START, FAULT_END
        elif ftype == "step":
            start, end = _random_start(rng), FAULT_END
        elif ftype == "drift":
            start = _random_start(rng, latest="2022-12-31")
            end = min(start + pd.Timedelta(days=int(rng.integers(180, 548))), FAULT_END)
        elif ftype == "stuck":
            start = _random_start(rng, latest="2023-12-20")
            end = start + pd.Timedelta(hours=int(size))
        elif ftype == "time_offset":
            start = _random_start(rng)
            end = min(start + pd.Timedelta(days=int(rng.integers(90, 366))), FAULT_END)
        rows.append({"fault_id": fid, "station_id": sid, "fault_type": ftype, "fault_size": float(size),
                     "unit": SIZES[ftype][1], "start": start, "end": end})
    return pd.DataFrame(rows)


def plant(baseline: pd.DataFrame, catalogue: pd.DataFrame, seed=0) -> pd.DataFrame:
    """Apply the catalogue's faults to the baseline rows and add ground-truth labels.

    ``baseline`` is the matched table for baseline stations. The result keeps every row
    and adds ``air_temperature_true`` and ``dew_point_temperature_true`` (the clean
    values), overwrites ``air_temperature`` and ``dew_point_temperature`` with what a
    pipeline would observe, and labels each row with ``fault_id`` (-1 if none),
    ``fault_type`` and ``is_faulty`` (True where the observed value differs from the
    truth or went missing because of the fault). The station-minus-HRRR differences are
    recomputed from the observed values.
    """
    rng = np.random.default_rng(seed + 10_000)
    out = baseline.sort_values(["station_id", "time"]).reset_index(drop=True)
    out["air_temperature_true"] = out.air_temperature
    out["dew_point_temperature_true"] = out.dew_point_temperature
    out["fault_id"] = -1
    out["fault_type"] = ""
    out["is_faulty"] = False
    storm = storm_hours(out).to_numpy()

    for f in catalogue.itertuples():
        idx = np.flatnonzero((out.station_id == f.station_id).to_numpy())
        s = out.iloc[idx]
        if f.fault_type == "step":
            t, d, w = apply_step(s, f.fault_size, f.start, f.end)
        elif f.fault_type == "drift":
            t, d, w = apply_drift(s, f.fault_size, f.start, f.end)
        elif f.fault_type == "bias":
            t, d, w = apply_bias(s, f.fault_size, f.start, f.end)
        elif f.fault_type == "stuck":
            t, d, w = apply_stuck(s, int(f.fault_size), f.start)
        elif f.fault_type == "time_offset":
            t, d, w = apply_time_offset(s, int(f.fault_size), f.start, f.end)
        elif f.fault_type == "storm_gaps":
            t, d, w = apply_storm_gaps(s, f.fault_size, storm[idx], rng, f.start, f.end)
        else:
            raise ValueError(f.fault_type)
        out.loc[idx, "air_temperature"] = t
        out.loc[idx, "dew_point_temperature"] = d
        if f.fault_type == "storm_gaps":
            # The whole report is lost, so the station's own wind and rain go missing too.
            # A check can't use the station itself to see that it missed a storm.
            out.loc[idx[w], ["wind_speed", "precipitation_1h"]] = np.nan
        out.loc[idx[w], "fault_id"] = f.fault_id
        out.loc[idx[w], "fault_type"] = f.fault_type
        out.loc[idx[w], "is_faulty"] = True

    # A row only counts as faulty if something actually changed (a stuck window's first
    # hour keeps its true value, a 0.5 °C step on a NaN hour changes nothing).
    t_obs, t_true = out.air_temperature, out.air_temperature_true
    changed = ~(np.isclose(t_obs, t_true) | (t_obs.isna() & t_true.isna()))
    out["is_faulty"] = out.is_faulty & changed
    out["diff_air_temperature"] = out.air_temperature - out.hrrr_air_temperature_adj
    out["diff_dew_point_temperature"] = out.dew_point_temperature - out.hrrr_dew_point_temperature
    out["storm_hour"] = storm
    return out


def make(matched: pd.DataFrame, seed=0, share=0.25):
    """Baseline selection, held-out stations, catalogue and planted data in one call.

    Returns ``(planted, catalogue, baseline_stats, holdout)``. Cheap enough (seconds) to
    call once per seed when building recall curves.
    """
    stats = select_baseline(matched)
    ids = stats.index[stats.baseline].tolist()
    holdout = holdout_stations(ids)
    catalogue = plan_faults(ids, holdout, seed=seed, share=share)
    base = matched[matched.station_id.isin(ids)].dropna(subset=["hrrr_air_temperature"])
    planted = plant(base, catalogue, seed=seed)
    # The planned window can start inside a real outage, so record when each fault
    # actually touches the data. Scoring in notebook 05 uses these.
    hit = planted[planted.is_faulty].groupby("fault_id").time.agg(["min", "max", "size"])
    hit.columns = ["first_affected", "last_affected", "affected_hours"]
    catalogue = catalogue.join(hit, on="fault_id")
    catalogue["affected_hours"] = catalogue.affected_hours.fillna(0).astype(int)
    return planted, catalogue, stats, holdout


# --------------------------------------------------------------------------- storage

def fault_dir(data_dir, seed=0):
    from pathlib import Path
    return Path(data_dir) / "faults" / f"seed={seed}"


def save(data_dir, planted, catalogue, baseline_stats, holdout, seed=0):
    """Write one seed's planted data, catalogue, baseline stats and held-out list."""
    d = fault_dir(data_dir, seed)
    d.mkdir(parents=True, exist_ok=True)
    planted.to_parquet(d / "planted.parquet", index=False)
    catalogue.to_parquet(d / "catalogue.parquet", index=False)
    baseline_stats.to_parquet(d / "baseline.parquet")
    pd.Series(holdout, name="station_id").to_frame().to_parquet(d / "holdout.parquet", index=False)
    return d


def load(data_dir, seed=0):
    """Read back ``(planted, catalogue, baseline_stats, holdout)`` for one seed."""
    d = fault_dir(data_dir, seed)
    return (pd.read_parquet(d / "planted.parquet"), pd.read_parquet(d / "catalogue.parquet"),
            pd.read_parquet(d / "baseline.parquet"), pd.read_parquet(d / "holdout.parquet").station_id.tolist())
