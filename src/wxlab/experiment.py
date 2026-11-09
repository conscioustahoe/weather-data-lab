"""Does cleaner training data make a better model? A controlled experiment.

The model is a small piece of *post-processing*, which is what forecasters call learning
a correction to a weather model from its past errors. Gradient boosting (LightGBM) takes
HRRR's 1-hour forecast at a station plus simple time and place features and predicts how
far the station's real temperature sits from HRRR. The prediction is HRRR plus that
correction.

Every *arm* of the experiment trains the same model with the same settings on a
different version of the 2021–2023 training data:

- **clean**: the true values, no planted faults.
- **faulty**: the observed values with the planted faults of one seed (notebook 04).
- **qa**: faulty minus the hours QA marked *quarantine*.
- **qa_strict**: faulty minus quarantine and *suspect* hours.
- **random**: faulty minus as many hours as ``qa`` removed, picked at random. If
  ``qa`` beats ``faulty`` but not ``random``, QA is only removing data, not bad data.
- **oracle**: faulty minus exactly the planted fault hours. No real QA can know
  these, so it's the best removal could ever do.
- **qa_drop_segments**: ``qa`` minus the hours in segments that break dating found
  to be off (see :func:`wxlab.qa.segments`).
- **qa_adjust_segments**: ``qa`` with those segments shifted back by their offset,
  the way homogenized climate datasets fix a break instead of throwing data away.

Only the training data changes. The test set is the same for every arm: 2024 at the
baseline stations, which no fault touches, minus the few hours the real-data QA
quarantined (2024 has real faults too, like San Nicolas Island reading 0.0 °C, and a
model shouldn't be scored against them). Results are split into groups: the training
stations, the stations that had faults, held-out stations the model never saw, and
storm hours.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from wxlab import faults as fl
from wxlab import qa

ARMS = ["clean", "faulty", "qa", "qa_strict", "random", "oracle", "qa_drop_segments", "qa_adjust_segments"]
FEATURES = ["hrrr_air_temperature_adj", "hrrr_dew_point_temperature", "hrrr_dew_point_depression",
            "hour", "doy_sin", "doy_cos", "latitude", "longitude", "elevation_m", "elevation_gap_m"]
TRAIN_END = pd.Timestamp("2024-01-01", tz="UTC")
COLUMNS = ["station_id", "time", "air_temperature", "air_temperature_true", "hrrr_air_temperature",
           "hrrr_air_temperature_adj", "hrrr_dew_point_temperature", "elevation_m", "model_elevation_m",
           "is_faulty", "storm_hour"]
PARAMS = {"n_estimators": 400, "learning_rate": 0.05, "num_leaves": 63, "min_child_samples": 200,
          "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.9, "verbose": -1, "n_jobs": 8}


def features(df: pd.DataFrame, points: pd.DataFrame) -> pd.DataFrame:
    """Model inputs. Only things known without the station's own reading.

    Hour is UTC, which is fine here because every station is in one time zone. Day of
    year goes in as a sine and cosine so 31 December sits next to 1 January. The
    elevation gap is the station's height minus HRRR's terrain height at that point,
    which is where most of a station's honest difference from HRRR comes from.
    """
    p = points.set_index("station_id")
    out = pd.DataFrame(index=df.index)
    out["hrrr_air_temperature_adj"] = df.hrrr_air_temperature_adj
    out["hrrr_dew_point_temperature"] = df.hrrr_dew_point_temperature
    out["hrrr_dew_point_depression"] = df.hrrr_air_temperature - df.hrrr_dew_point_temperature
    out["hour"] = df.time.dt.hour
    doy = df.time.dt.dayofyear.to_numpy()
    out["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    out["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    out["latitude"] = df.station_id.map(p.latitude).to_numpy()
    out["longitude"] = df.station_id.map(p.longitude).to_numpy()
    out["elevation_m"] = df.elevation_m
    out["elevation_gap_m"] = df.elevation_m - df.model_elevation_m
    return out


def run_qa(planted, clim, points, dup_pairs):
    """QA on one seed's planted data: verdicts, breaks, dated segments and their offsets."""
    nbrs = qa.neighbours(points[points.station_id.isin(planted.station_id.unique())], dup_pairs)
    flags, _, cp = qa.run_checks(planted, clim, nbrs)
    dates = qa.break_dates(flags, cp)
    segs = qa.segments(cp, dates)
    off = qa.segment_offsets(planted, segs)
    slim = pd.DataFrame({"verdict": flags.verdict.reindex(planted.index),
                         "break": flags["break"].reindex(planted.index),
                         "seg_offset": off.offset, "seg_bad": off.bad})
    return slim, dates, segs


def training_sets(planted, slim, holdout, seed) -> dict[str, pd.DataFrame]:
    """The rows (and target) each arm trains on. Returns ``{arm: frame with a target column}``."""
    train = planted.loc[(planted.time < TRAIN_END) & ~planted.station_id.isin(holdout), COLUMNS]
    s = slim.loc[train.index]
    obs = train.air_temperature
    present = obs.notna()
    quarantine = s.verdict == "quarantine"
    suspect = s.verdict.isin(["suspect", "quarantine"])
    keep = {
        "faulty": present,
        "qa": present & ~quarantine,
        "qa_strict": present & ~suspect,
        "oracle": present & ~train.is_faulty,
        "qa_drop_segments": present & ~quarantine & ~s.seg_bad,
        "qa_adjust_segments": present & ~quarantine,
    }
    removed = int((present & quarantine).sum())
    rng = np.random.default_rng(seed + 90_000)
    drop = rng.choice(np.flatnonzero(present.to_numpy()), size=removed, replace=False)
    rand = present.to_numpy().copy()
    rand[drop] = False
    keep["random"] = pd.Series(rand, index=train.index)
    target = {arm: obs for arm in keep}
    target["qa_adjust_segments"] = obs - s.seg_offset
    out = {"clean": train.assign(target=train.air_temperature_true)[train.air_temperature_true.notna()]}
    for arm, k in keep.items():
        out[arm] = train.assign(target=target[arm])[k]
    return {arm: out[arm] for arm in ARMS}


def test_set(planted, real_verdict) -> pd.DataFrame:
    """2024 at every baseline station, true values, minus hours real-data QA quarantined."""
    t = planted.loc[planted.time >= TRAIN_END, COLUMNS]
    t = t[t.air_temperature_true.notna()]
    v = real_verdict.reindex(pd.MultiIndex.from_frame(t[["station_id", "time"]]))
    return t[(v != "quarantine").to_numpy()].assign(target=lambda d: d.air_temperature_true)


def fit_predict(train, test, points, seed):
    import lightgbm as lgb

    model = lgb.LGBMRegressor(**PARAMS, random_state=seed)
    y = train.target - train.hrrr_air_temperature_adj        # learn the correction to HRRR
    model.fit(features(train, points)[FEATURES], y)
    return test.hrrr_air_temperature_adj + model.predict(features(test, points)[FEATURES])


def score(test, pred, catalogue, holdout) -> pd.DataFrame:
    """RMSE and mean error (bias) of a prediction, overall and per group."""
    err = pred - test.target
    groups = {
        "training stations": ~test.station_id.isin(holdout),
        "faulty stations": test.station_id.isin(catalogue.station_id),
        "held-out stations": test.station_id.isin(holdout),
        "storm hours": test.storm_hour.astype(bool),
    }
    rows = []
    for g, m in groups.items():
        e = err[m.to_numpy()]
        rows.append({"group": g, "rmse": float(np.sqrt((e ** 2).mean())), "bias": float(e.mean()), "n": int(len(e))})
    return pd.DataFrame(rows)


def station_scores(test, pred) -> pd.DataFrame:
    """RMSE and mean error per station, so results can be split by fault type later."""
    err = pred - test.target
    g = pd.DataFrame({"station_id": test.station_id, "e": err, "e2": err ** 2}).groupby("station_id")
    return pd.DataFrame({"rmse": np.sqrt(g.e2.mean()), "bias": g.e.mean(), "n": g.size()}).reset_index()


def run_seed(matched, clim, points, dup_pairs, real_verdict, seed, arms=ARMS):
    """Plant one seed's faults, run QA, train every arm and score it on 2024."""
    planted, catalogue, _, holdout = fl.make(matched, seed=seed)
    slim, dates, segs = run_qa(planted, clim, points, dup_pairs)
    sets = training_sets(planted, slim, holdout, seed)
    test = test_set(planted, real_verdict)
    rows, per_station = [], []
    for arm in arms:
        tr = sets[arm]
        pred = fit_predict(tr, test, points, seed)
        rows.append(score(test, pred, catalogue, holdout).assign(arm=arm, seed=seed, train_rows=len(tr)))
        per_station.append(station_scores(test, pred).assign(arm=arm, seed=seed))
    hrrr = test.hrrr_air_temperature_adj
    rows.append(score(test, hrrr, catalogue, holdout).assign(arm="hrrr", seed=seed, train_rows=0))
    per_station.append(station_scores(test, hrrr).assign(arm="hrrr", seed=seed))
    results = pd.concat(rows, ignore_index=True)
    stations = pd.concat(per_station, ignore_index=True)
    return results, stations, dates.assign(seed=seed), segs.assign(seed=seed), catalogue.assign(seed=seed)


def run_seeds(matched, clim, points, dup_pairs, real_verdict, seeds, out_dir):
    """:func:`run_seed` over many seeds, one cached folder per seed (idempotent units)."""
    parts = {"results": [], "stations": [], "dates": [], "segments": [], "catalogue": []}
    for seed in seeds:
        d = Path(out_dir) / f"seed={seed}"
        paths = {k: d / f"{k}.parquet" for k in parts}
        if all(p.exists() for p in paths.values()):
            res = [pd.read_parquet(paths[k]) for k in parts]
        else:
            res = run_seed(matched, clim, points, dup_pairs, real_verdict, seed)
            d.mkdir(parents=True, exist_ok=True)
            for k, r in zip(parts, res):
                r.to_parquet(paths[k])
        for k, r in zip(parts, res):
            parts[k].append(r)
    return {k: pd.concat(v, ignore_index=True) for k, v in parts.items()}


def break_scores(planted, catalogue, segs) -> pd.DataFrame:
    """How well break dating and segment offsets recover each planted level fault.

    For every step, bias and drift: the error in °C of the observed training-year values
    against the truth, before and after the segment adjustment (mean absolute error over
    the fault's hours), and whether any bad segment touched the fault at all.
    """
    p = planted[planted.time < TRAIN_END]
    off = qa.segment_offsets(p, segs)
    rows = []
    for x in catalogue[catalogue.fault_type.isin(["step", "bias", "drift"]) & (catalogue.affected_hours > 0)].itertuples():
        m = ((p.fault_id == x.fault_id) & p.air_temperature.notna()).to_numpy()
        obs, true = p.air_temperature[m], p.air_temperature_true[m]
        adj = obs - off.offset[m]
        rows.append({"fault_id": x.fault_id, "station_id": x.station_id, "fault_type": x.fault_type,
                     "fault_size": x.fault_size, "found": bool(off.bad[m].any()),
                     "error_before": float((obs - true).abs().mean()), "error_after": float((adj - true).abs().mean())})
    return pd.DataFrame(rows)


def evaluate_breaks(matched, clim, points, dup_pairs, seeds, out_dir):
    """Break dating scored over many seeds, cached one file per seed.

    Returns ``(faults, untouched)``: :func:`break_scores` for every planted level fault,
    and for every untouched station the share of its training hours that landed in a
    bad segment and the mean size of the adjustment it got (all of it false).
    """
    out_dir = Path(out_dir)
    parts = {"faults": [], "untouched": []}
    for seed in seeds:
        paths = {k: out_dir / f"{k}_seed{seed}.parquet" for k in parts}
        if not all(p.exists() for p in paths.values()):
            planted, catalogue, _, _ = fl.make(matched, seed=seed)
            slim, _, segs = run_qa(planted, clim, points, dup_pairs)
            p = planted[planted.time < TRAIN_END]
            s = slim.loc[p.index]
            u = ~p.station_id.isin(catalogue.station_id) & p.air_temperature.notna()
            un = (pd.DataFrame({"station_id": p.station_id[u], "bad": s.seg_bad[u], "abs_offset": s.seg_offset[u].abs()})
                  .groupby("station_id").agg(share_bad=("bad", "mean"), mean_abs_offset=("abs_offset", "mean"))
                  .reset_index())
            out_dir.mkdir(parents=True, exist_ok=True)
            break_scores(planted, catalogue, segs).assign(seed=seed).to_parquet(paths["faults"])
            un.assign(seed=seed).to_parquet(paths["untouched"])
        for k in parts:
            parts[k].append(pd.read_parquet(paths[k]))
    return pd.concat(parts["faults"], ignore_index=True), pd.concat(parts["untouched"], ignore_index=True)
