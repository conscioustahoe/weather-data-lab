"""QA checks, verdicts and how to score them.

Each check looks at the data from one angle and writes one boolean flag per
station-hour. They run from cheap to subtle:

- **physical**: the value is possible at all. California's records are about -45 °C
  (Boca, 1937) and 56.7 °C (Death Valley, 1913), and dew point can't be above the air
  temperature.
- **climatology**: the value is plausible for this station, month and hour of day,
  judged against the station's own 2015–2020 history. Catches gross errors.
- **reference**: this hour's station-minus-HRRR difference compared with the station's
  normal difference *for that hour of day* (the difference has a daily cycle).
- **timing**: a whole day whose difference swings through the day far more than
  usual. That's the signature of a time offset, which leaves the average untouched.
- **stuck**: the same value repeated for hours while HRRR says the temperature moved.
- **neighbour**: the station's difference from HRRR compared with its neighbours'
  differences at the same hour. If HRRR is wrong across a region the neighbours share
  the error and it cancels, so what's left is the station's own.
- **shift**: a lasting shift in the daily station-minus-HRRR difference (same method as
  changepoint below, against HRRR alone).
- **changepoint**: a lasting shift in the daily neighbour-adjusted difference, found by
  comparing the mean of the days after each day with the mean of the days before it
  (windows of 30 and 90 days), after removing the station's own seasonal cycle.
  Catches steps, and drifts once they've built up.
- **coverage** (per station-month, not per hour): the station is much less complete in
  storm hours than in calm hours. Storm hours come from the neighbours, because a
  station that loses a storm also loses its own wind and rain readings.

Every threshold is a robust z-score (median and MAD instead of mean and standard
deviation, so the faults themselves don't inflate the scale) with a floor in °C, and
they were tuned on the untouched baseline stations of notebook 04 to keep false alarms
rare. The thresholds live in ``THRESHOLDS`` so they can be changed in one place.

**Verdicts.** The checks are grouped by where their evidence comes from: the station
itself (physical, climatology), HRRR (reference, timing, stuck, shift) and the neighbours
(neighbour, changepoint). Shift belongs with HRRR. An hour is *suspect* if any check fires and *quarantine* if
checks from two different groups agree and at least one of them judged that hour
itself (timing, shift and changepoint judge whole days, so they can support a quarantine
but not cause one).

**Breaks.** Steps and drifts never look wrong hour by hour. When both shift detectors
(one against HRRR, one against the neighbours) see a lasting shift, the days involved
get a ``break`` annotation. A break says the station's record changed character around
then, the way climate datasets mark inhomogeneities, and what to do about it (drop a
segment, adjust it, or keep both) is a separate decision from judging single hours. HRRR has its own weaknesses (notebook 03), so it never gets to
quarantine a value on its own.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

HOURLY_CHECKS = ["physical", "climatology", "reference", "timing", "stuck", "shift", "neighbour", "changepoint"]
GROUPS = {"physical": "self", "climatology": "self",
          "reference": "hrrr", "timing": "hrrr", "stuck": "hrrr", "shift": "hrrr",
          "neighbour": "neighbours", "changepoint": "neighbours"}

DAY_LEVEL = {"timing", "shift", "changepoint"}
PERSISTENT = {"shift", "changepoint"}

THRESHOLDS = {
    "t_min": -45.0, "t_max": 57.0, "dew_margin": 0.5,
    "clim_z": 6.0, "clim_floor": 1.5,          # floor on the robust sd, °C
    "ref_z": 6.0, "ref_floor": 6.0,            # flag if |r| > max(z * sd, floor)
    "timing_ratio": 2.5, "timing_floor": 3.0, "timing_min_hours": 18,
    "stuck_hours": 6, "stuck_hrrr_range": 5.0,
    "nb_k": 5, "nb_max_km": 100.0, "nb_min": 2, "nb_z": 6.0, "nb_floor": 6.0,
    "cp_windows": (30, 90), "cp_z": 4.0, "cp_floor": {30: 0.6, 90: 0.4}, "cp_min_days": 20,
    "cov_min_storm_hours": 6, "cov_gap": 0.25, "cov_storm_share": 0.5,
}

EARTH_KM = 6371.0


def robust_sd(x) -> float:
    """1.4826 x median absolute deviation: the standard deviation if the data were normal."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return np.nan
    return 1.4826 * float(np.median(np.abs(x - np.median(x))))


def _group_robust_sd(values: pd.Series, keys) -> pd.Series:
    med = values.groupby(keys).transform("median")
    return 1.4826 * (values - med).abs().groupby(keys).transform("median")


# --------------------------------------------------------------------------- inputs

def climatology(history: pd.DataFrame) -> pd.DataFrame:
    """Median and robust sd of air temperature by station, month and UTC hour.

    ``history`` is standardized hourly data from earlier years (2015–2020 here), so the
    climatology is independent of the data being checked.
    """
    h = history.dropna(subset=["air_temperature"])
    keys = [h.station_id, h.time.dt.month.rename("month"), h.time.dt.hour.rename("hour")]
    g = h.air_temperature.groupby(keys)
    clim = pd.DataFrame({"clim_median": g.median()})
    clim["clim_sd"] = (h.air_temperature - h.groupby(keys).air_temperature.transform("median")).abs().groupby(keys).median() * 1.4826
    clim["clim_n"] = g.size()
    return clim.reset_index()


def neighbours(points: pd.DataFrame, exclude_pairs=None, k=None, max_km=None) -> dict[str, list[str]]:
    """Up to k nearest other stations within max_km for each station.

    ``points`` has station_id, latitude, longitude. Duplicate pairs (the same station
    under two ids) are never each other's neighbours.
    """
    k = k or THRESHOLDS["nb_k"]
    max_km = max_km or THRESHOLDS["nb_max_km"]
    pts = points.drop_duplicates("station_id").reset_index(drop=True)
    tree = BallTree(np.radians(pts[["latitude", "longitude"]].to_numpy()), metric="haversine")
    dist, idx = tree.query(np.radians(pts[["latitude", "longitude"]].to_numpy()), k=min(k + 4, len(pts)))
    banned = set()
    if exclude_pairs is not None and len(exclude_pairs):
        for a, b in exclude_pairs[["station_a", "station_b"]].itertuples(index=False):
            banned |= {(a, b), (b, a)}
    ids = pts.station_id.to_numpy()
    out = {}
    for i, sid in enumerate(ids):
        nb = [ids[j] for d, j in zip(dist[i], idx[i])
              if j != i and d * EARTH_KM <= max_km and (sid, ids[j]) not in banned]
        out[sid] = nb[:k]
    return out


# --------------------------------------------------------------------------- checks

def check_physical(df):
    t, d = df.air_temperature, df.dew_point_temperature
    th = THRESHOLDS
    return (t < th["t_min"]) | (t > th["t_max"]) | (d > t + th["dew_margin"])


def check_climatology(df, clim):
    keys = pd.DataFrame({"station_id": df.station_id.to_numpy(), "month": df.time.dt.month.to_numpy(),
                         "hour": df.time.dt.hour.to_numpy()})
    c = keys.merge(clim, on=["station_id", "month", "hour"], how="left")
    sd = np.maximum(c.clim_sd.to_numpy(), THRESHOLDS["clim_floor"])
    z = np.abs(df.air_temperature.to_numpy() - c.clim_median.to_numpy()) / sd
    return pd.Series(np.nan_to_num(z) > THRESHOLDS["clim_z"], index=df.index)


def reference_residual(df) -> pd.Series:
    """Difference from HRRR minus the station's normal difference for that UTC hour."""
    normal = df.groupby([df.station_id, df.time.dt.hour]).diff_air_temperature.transform("median")
    return df.diff_air_temperature - normal


def check_reference(df, r=None):
    r = reference_residual(df) if r is None else r
    sd = _group_robust_sd(r, df.station_id)
    lim = np.maximum(THRESHOLDS["ref_z"] * sd, THRESHOLDS["ref_floor"])
    return r.abs() > lim


def check_timing(df, r=None):
    """Flag whole days whose residual swings far more through the day than is normal."""
    r = reference_residual(df) if r is None else r
    day = df.time.dt.floor("D")
    g = r.groupby([df.station_id, day])
    daily = pd.DataFrame({"swing": g.std(), "n": g.count()})
    base = daily.swing.groupby(level=0).transform("median")
    bad = ((daily.swing > np.maximum(THRESHOLDS["timing_ratio"] * base, THRESHOLDS["timing_floor"]))
           & (daily.n >= THRESHOLDS["timing_min_hours"]))
    key = pd.MultiIndex.from_arrays([df.station_id, day])
    return pd.Series(bad.reindex(key).fillna(False).to_numpy(), index=df.index)


def check_stuck(df):
    """Runs of one repeated value at least N hours long while HRRR moved by M °C or more."""
    t = df.air_temperature
    new_run = (t != t.shift()) | (df.station_id != df.station_id.shift()) | t.isna()
    run = new_run.cumsum()
    g = df.groupby(run)
    length = g.air_temperature.transform("size")
    hrrr_range = g.hrrr_air_temperature_adj.transform("max") - g.hrrr_air_temperature_adj.transform("min")
    return (length >= THRESHOLDS["stuck_hours"]) & (hrrr_range >= THRESHOLDS["stuck_hrrr_range"]) & t.notna()


def neighbour_difference(df, nbrs) -> pd.Series:
    """Median of the neighbours' station-minus-HRRR difference at the same hour."""
    wide = df.pivot_table(index="time", columns="station_id", values="diff_air_temperature")
    cols = {c: i for i, c in enumerate(wide.columns)}
    arr = wide.to_numpy()
    med = {}
    for sid in wide.columns:
        idx = [cols[n] for n in nbrs.get(sid, []) if n in cols]
        if len(idx) < THRESHOLDS["nb_min"]:
            continue
        sub = arr[:, idx]
        enough = (~np.isnan(sub)).sum(axis=1) >= THRESHOLDS["nb_min"]
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            m = np.nanmedian(sub, axis=1)
        m[~enough] = np.nan
        med[sid] = m
    nd = pd.DataFrame(med, index=wide.index).stack(future_stack=True).rename("nb_diff")
    key = pd.MultiIndex.from_arrays([df.time, df.station_id])
    return pd.Series(nd.reindex(key).to_numpy(), index=df.index)


def check_neighbour(df, nb_diff):
    nr = df.diff_air_temperature - nb_diff
    centred = nr - nr.groupby(df.station_id).transform("median")
    sd = _group_robust_sd(nr, df.station_id)
    lim = np.maximum(THRESHOLDS["nb_z"] * sd, THRESHOLDS["nb_floor"])
    return (centred.abs() > lim) & nr.notna()


def deseasonalize(s: pd.Series) -> pd.Series:
    """Remove a station's own seasonal cycle from a daily series.

    Some stations' difference from HRRR moves with the seasons for honest reasons (the
    marine layer comes and goes) and neighbours don't always share it. The cycle is the
    median for each day of the year across all years, smoothed with a 31-day window.
    A lasting step still shows after this, because it changes the level abruptly while
    the cycle is smooth and repeats every year.
    """
    doy = np.minimum(s.index.dayofyear, 365)
    prof = s.groupby(doy).median().reindex(range(1, 366))
    wrapped = pd.concat([prof.iloc[-30:], prof, prof.iloc[:30]])
    prof = wrapped.rolling(31, center=True, min_periods=10).mean().iloc[30:-30]
    return s - prof.reindex(doy).to_numpy()


def changepoint_days(df, nb_diff=None) -> pd.DataFrame:
    """Daily neighbour-adjusted difference and a before/after shift statistic per window.

    For each day the statistic is the mean of the next w days minus the mean of the
    previous w days. Its noise level is measured from the statistic itself (a robust sd
    over the station's whole record): a step only moves it for the w days on either side,
    so the median and MAD still describe the quiet periods. That accounts for weather
    being autocorrelated from day to day, which a textbook standard error would ignore.
    """
    if nb_diff is None:            # shift against HRRR alone
        adj = df.diff_air_temperature
    else:                          # shift against the neighbours (HRRR mostly cancels)
        adj = (df.diff_air_temperature - nb_diff).fillna(df.diff_air_temperature - df.groupby("station_id").diff_air_temperature.transform("median"))
    daily = adj.groupby([df.station_id, df.time.dt.floor("D").rename("day")]).mean().rename("adj").reset_index()
    out = []
    for sid, s in daily.groupby("station_id", sort=False):
        s = s.set_index("day").adj.asfreq("D")
        if len(s) >= 730:      # a seasonal cycle needs at least two years to estimate
            s = deseasonalize(s)
        res = pd.DataFrame({"adj": s})
        res["flag"] = False
        for w in THRESHOLDS["cp_windows"]:
            mp = THRESHOLDS["cp_min_days"] if w == 30 else int(w * 0.6)
            before = s.rolling(w, min_periods=mp).mean().shift(1)
            after = s[::-1].rolling(w, min_periods=mp).mean()[::-1]
            delta = after - before
            lim = max(THRESHOLDS["cp_z"] * robust_sd(delta.to_numpy()), THRESHOLDS["cp_floor"][w])
            res[f"delta_{w}"] = delta
            res["flag"] |= (delta.abs() > lim).fillna(False)
        res["station_id"] = sid
        out.append(res.reset_index())
    return pd.concat(out, ignore_index=True)


def check_changepoint(df, cp_days):
    flagged = cp_days[cp_days.flag].set_index(["station_id", "day"]).index
    key = pd.MultiIndex.from_arrays([df.station_id, df.time.dt.floor("D")])
    return pd.Series(key.isin(flagged), index=df.index)


def check_coverage(df, nbrs, storm_wind_quantile=0.98, rain_mm=1.0) -> pd.DataFrame:
    """Per station-month completeness in storm hours vs other hours, storms from neighbours."""
    th = THRESHOLDS
    hours = pd.date_range(df.time.min().floor("D"), df.time.max().ceil("D"), freq="h", inclusive="left")
    temp = df.pivot_table(index="time", columns="station_id", values="air_temperature").reindex(hours)
    wind = df.pivot_table(index="time", columns="station_id", values="wind_speed").reindex(hours)
    rain = df.pivot_table(index="time", columns="station_id", values="precipitation_1h").reindex(hours).reindex(columns=wind.columns)
    storm_self = (wind >= wind.quantile(storm_wind_quantile)) | (rain >= rain_mm)
    reported = wind.notna() | rain.notna()
    rows = []
    month = hours.tz_localize(None).to_period("M") if hours.tz is not None else hours.to_period("M")
    for sid in temp.columns:
        nb = [n for n in nbrs.get(sid, []) if n in storm_self.columns]
        if len(nb) < th["nb_min"]:
            continue
        votes = storm_self[nb].sum(axis=1)
        counted = reported[nb].sum(axis=1)
        storm = (counted >= th["nb_min"]) & (votes >= th["cov_storm_share"] * counted)
        have = temp[sid].notna()
        g = pd.DataFrame({"storm": storm.to_numpy(), "have": have.to_numpy(), "month": month})
        agg = g.groupby(["month", "storm"]).have.agg(["mean", "size"]).unstack("storm")
        for m, row in agg.iterrows():
            n_storm = row.get(("size", True), 0)
            c_storm, c_calm = row.get(("mean", True), np.nan), row.get(("mean", False), np.nan)
            rows.append({"station_id": sid, "month": m, "storm_hours": n_storm,
                         "complete_storm": c_storm, "complete_calm": c_calm})
    cov = pd.DataFrame(rows)
    cov["storm_hours"] = cov.storm_hours.fillna(0)
    cov["flag"] = ((cov.storm_hours >= th["cov_min_storm_hours"])
                   & (cov.complete_storm < cov.complete_calm - th["cov_gap"]))
    return cov


# --------------------------------------------------------------------------- running

def run_checks(df: pd.DataFrame, clim: pd.DataFrame, nbrs: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run every check. Returns ``(flags, coverage, changepoint_days)``.

    ``flags`` has one row per row of ``df`` (same index) with station_id, time, one
    boolean column per hourly check, ``n_checks``, ``n_groups`` and ``verdict``.
    """
    df = df.sort_values(["station_id", "time"])
    r = reference_residual(df)
    nb_diff = neighbour_difference(df, nbrs)
    cp = changepoint_days(df, nb_diff)
    hs = changepoint_days(df, None)
    flags = pd.DataFrame({"station_id": df.station_id, "time": df.time}, index=df.index)
    flags["physical"] = check_physical(df)
    flags["climatology"] = check_climatology(df, clim)
    flags["reference"] = check_reference(df, r)
    flags["timing"] = check_timing(df, r)
    flags["stuck"] = check_stuck(df)
    flags["neighbour"] = check_neighbour(df, nb_diff)
    flags["shift"] = check_changepoint(df, hs)
    flags["changepoint"] = check_changepoint(df, cp)
    # A missing value can't be judged by value checks.
    present = df.air_temperature.notna()
    for c in HOURLY_CHECKS:
        flags[c] &= present
    flags["n_checks"] = flags[HOURLY_CHECKS].sum(axis=1)
    groups = pd.DataFrame({g: flags[[c for c in HOURLY_CHECKS if GROUPS[c] == g]].any(axis=1)
                           for g in ("self", "hrrr", "neighbours")})
    flags["n_groups"] = groups.sum(axis=1)
    # timing and changepoint judge whole days. They can support a quarantine but can't
    # condemn an hour on their own, so at least one hour-level check has to fire too.
    hour_level = flags[[c for c in HOURLY_CHECKS if c not in DAY_LEVEL]].any(axis=1)
    flags["verdict"] = np.select([(flags.n_groups >= 2) & hour_level, flags.n_checks >= 1],
                                 ["quarantine", "suspect"], "pass")
    # A lasting shift seen against both HRRR and the neighbours is a *break* in the
    # station's record. It's a property of a period, not of single hours, so it's kept as
    # its own annotation beside the verdict instead of quarantining every hour around it.
    flags["break"] = flags["shift"] & flags["changepoint"]
    flags.loc[~present, "verdict"] = "missing"
    cov = check_coverage(df, nbrs)
    return flags, cov, cp


# --------------------------------------------------------------------------- scoring

def scoring_windows(catalogue, tol_days=30) -> pd.DataFrame:
    """The time window in which a detection counts, per fault.

    A detection has to be about the fault, not just anywhere on the station:

    - step and bias: within ``tol_days`` of the first affected hour (the onset). A bias
      starts on the first day of the data, so there's no "before" and its onset is
      expected to be close to undetectable.
    - drift: from the onset to ``tol_days`` after it ends (a drift is invisible at first).
    - time offset: from the onset to ``tol_days`` after it ends.
    - stuck: from a day before to a day after.
    - storm gaps: the whole affected period, by the coverage check only.
    """
    tol = pd.Timedelta(days=tol_days)
    c = catalogue[catalogue.affected_hours > 0].copy()
    first, last = c.first_affected, c.last_affected
    lo = np.where(c.fault_type.isin(["step", "bias"]), first - tol,
         np.where(c.fault_type == "stuck", first - pd.Timedelta(days=1), first))
    hi = np.where(c.fault_type.isin(["step", "bias"]), first + tol,
         np.where(c.fault_type == "stuck", last + pd.Timedelta(days=1), last + tol))
    c["lo"] = pd.to_datetime(lo, utc=True)
    c["hi"] = pd.to_datetime(hi, utc=True)
    return c


def score_faults(flags, coverage, catalogue, min_hours=3, tol_days=30, stations=None) -> pd.DataFrame:
    """For each planted fault and each check: did the check fire on that station in the fault's window?

    A check *detects* a fault if it flags at least ``min_hours`` hours at the station
    inside the window from :func:`scoring_windows`. Coverage detects a fault if it flags
    a month overlapping the window. ``suspect`` and ``quarantine`` score the verdicts.

    ``stations`` optionally maps fault_id to a different station: used for the chance
    baseline, where the same windows are scored on untouched stations.
    """
    c = scoring_windows(catalogue, tol_days)
    if stations is not None:
        c["station_id"] = c.fault_id.map(stations)
    f = flags.assign(suspect=flags.verdict.isin(["suspect", "quarantine"]), quarantine=flags.verdict == "quarantine")
    cols = HOURLY_CHECKS + ["break", "suspect", "quarantine"]
    by_station = {sid: g for sid, g in f.groupby("station_id")}
    cov_by_station = {sid: g for sid, g in coverage.groupby("station_id")}
    rows = []
    for x in c.itertuples():
        s = by_station.get(x.station_id, f.iloc[:0])
        s = s[(s.time >= x.lo) & (s.time <= x.hi)]
        hit = {col: int(s[col].sum()) >= min_hours for col in cols}
        cm = cov_by_station.get(x.station_id, coverage.iloc[:0])
        m_start = cm.month.dt.to_timestamp().dt.tz_localize("UTC")
        m_end = (cm.month + 1).dt.to_timestamp().dt.tz_localize("UTC")
        hit["coverage"] = bool(cm.flag[(m_end > x.lo) & (m_start <= x.hi)].any())
        rows.append({"fault_id": x.fault_id, "station_id": x.station_id, "fault_type": x.fault_type,
                     "fault_size": x.fault_size, **hit})
    return pd.DataFrame(rows)


def chance_stations(catalogue, untouched, seed=0) -> dict:
    """Map each fault to a random untouched station, for the chance baseline."""
    rng = np.random.default_rng(seed + 777)
    return dict(zip(catalogue.fault_id, rng.choice(sorted(untouched), size=len(catalogue))))


def alarms(flags, coverage, min_hours=3) -> pd.DataFrame:
    """Station-months where each check raised an alarm (at least ``min_hours`` flagged hours)."""
    f = flags.assign(suspect=flags.verdict.isin(["suspect", "quarantine"]), quarantine=flags.verdict == "quarantine",
                     month=flags.time.dt.tz_localize(None).dt.to_period("M"))
    cols = HOURLY_CHECKS + ["break", "suspect", "quarantine"]
    sm = (f.groupby(["station_id", "month"])[cols].sum() >= min_hours)
    cov = coverage.set_index(["station_id", "month"]).flag.rename("coverage")
    out = sm.join(cov, how="outer")
    return out.astype("boolean").fillna(False).astype(bool)


def precision(alarm_table, catalogue, tol_days=30) -> pd.Series:
    """Share of alarm station-months that fall inside a planted fault's window, per check."""
    c = catalogue[catalogue.affected_hours > 0].copy()
    c["lo"] = c.first_affected - pd.Timedelta(days=tol_days)
    c["hi"] = c.last_affected + pd.Timedelta(days=tol_days)
    idx = alarm_table.index.to_frame(index=False)
    m_start = idx.month.dt.to_timestamp().dt.tz_localize("UTC")
    m_end = (idx.month + 1).dt.to_timestamp().dt.tz_localize("UTC")
    true = np.zeros(len(idx), dtype=bool)
    for x in c.itertuples():
        true |= ((idx.station_id == x.station_id) & (m_end > x.lo) & (m_start <= x.hi)).to_numpy()
    out = {}
    for col in alarm_table.columns:
        a = alarm_table[col].to_numpy()
        out[col] = (true & a).sum() / a.sum() if a.sum() else np.nan
    res = pd.DataFrame({"precision": out, "alarms": alarm_table.sum()})
    return res


def evaluate_seed(matched, clim, points, dup_pairs, seed, min_hours=3):
    """Plant faults with one seed, run every check, and score them.

    Returns ``(scores, chance, precision_table)`` where ``scores`` has one row per
    planted fault, ``chance`` the same windows scored on random untouched stations,
    and ``precision_table`` the station-month precision per check.
    """
    from wxlab import faults as fl

    planted, catalogue, _, _ = fl.make(matched, seed=seed)
    nbrs = neighbours(points[points.station_id.isin(planted.station_id.unique())], dup_pairs)
    flags, cov, _ = run_checks(planted, clim, nbrs)
    scores = score_faults(flags, cov, catalogue, min_hours=min_hours).assign(seed=seed)
    untouched = sorted(set(planted.station_id) - set(catalogue.station_id))
    chance = score_faults(flags, cov, catalogue, min_hours=min_hours,
                          stations=chance_stations(catalogue, untouched, seed)).assign(seed=seed)
    prec = precision(alarms(flags, cov, min_hours=min_hours), catalogue).assign(seed=seed)
    return scores, chance, prec


def evaluate_seeds(matched, clim, points, dup_pairs, seeds, cache_dir=None):
    """:func:`evaluate_seed` over many seeds, cached one file per seed (idempotent units)."""
    from pathlib import Path

    parts = {"scores": [], "chance": [], "precision": []}
    for seed in seeds:
        paths = {k: Path(cache_dir) / f"{k}_seed{seed}.parquet" for k in parts} if cache_dir else None
        if paths and all(p.exists() for p in paths.values()):
            res = [pd.read_parquet(paths[k]) for k in parts]
        else:
            res = evaluate_seed(matched, clim, points, dup_pairs, seed)
            if paths:
                Path(cache_dir).mkdir(parents=True, exist_ok=True)
                for k, r in zip(parts, res):
                    r.to_parquet(paths[k])
        for k, r in zip(parts, res):
            parts[k].append(r)
    return tuple(pd.concat(parts[k]) for k in parts)


def monitor(df, clim, nbrs, months=None, verbose=False):
    """Run the checks month by month, the way a scheduled job would.

    Each run sees all data up to the end of its month (nothing from the future), judges
    only that month's hours and records metrics for the run. Returns
    ``(metrics, flags)`` where ``flags`` holds every hour's verdict as of its own run.
    """
    df = df.sort_values(["station_id", "time"])
    month = df.time.dt.tz_localize(None).dt.to_period("M")
    months = months if months is not None else sorted(month.unique())
    n_stations = df.station_id.nunique()
    metrics, kept = [], []
    for m in months:
        upto = df[month <= m]
        flags, cov, _ = run_checks(upto, clim, nbrs)
        cur = flags[flags.time.dt.tz_localize(None).dt.to_period("M") == m]
        hours = m.days_in_month * 24
        present = cur.verdict != "missing"
        row = {"month": m, "stations_reporting": cur.station_id[present].nunique(),
               "coverage": present.sum() / (n_stations * hours)}
        for c in HOURLY_CHECKS:
            row[f"rate_{c}"] = cur.loc[present, c].mean()
        for v in ("pass", "suspect", "quarantine"):
            row[v] = int((cur.verdict == v).sum())
        q = cur[cur.verdict == "quarantine"].groupby("station_id").size()
        row["stations_quarantined"] = int((q >= 3).sum())
        row["coverage_alarms"] = int(cov[(cov.month == m) & cov.flag].shape[0])
        metrics.append(row)
        kept.append(cur)
        if verbose:
            print(m, row["quarantine"], flush=True)
    return pd.DataFrame(metrics), pd.concat(kept)
