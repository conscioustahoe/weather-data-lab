"""Matching stations to a gridded reference: the HRRR weather model.

Why a reference
---------------
A station can only be judged against something independent that estimates the same
quantity. Weather affects both, so it largely cancels in the difference
``station - reference``; what remains is the station's own error plus
*representativeness error* (a point measurement and a 3 km grid cell disagree even
when both are right). Checks therefore watch how that difference *changes*, not
whether it is zero.

Why HRRR, and why its 1-hour forecast
-------------------------------------
HRRR (High-Resolution Rapid Refresh) is NOAA's hourly 3 km model for the contiguous
US, on AWS in the bucket ``noaa-hrrr-bdp-pds``. Each hour it starts a new run from an
*analysis*: its best estimate of the current atmosphere, made by data assimilation,
which pulls the model toward recent observations, including airport reports like
ours. Using the analysis would partly compare stations with themselves. Instead we
take the forecast that was started one hour *before* each hour (lead time ``fxx=1``),
which had not yet seen that hour's observation. Its starting point was shaped by the
previous hour's observations, so independence is improved, not perfect.

We use 2021 onward: HRRR version 4 became operational in December 2020, and a model
version change can shift typical values everywhere at once, which would look like a
step fault at every station.

Storage
-------
Whole HRRR grids are never stored. Each hour's GRIB2 subset (2 m temperature and
2 m dew point only, a few MB) is downloaded, sampled at the station points, cropped
to California (kept as one compressed array per day for the gridded-data notebook)
and deleted. The unit of work is one day, written as one Parquet file; finished days are
skipped, so the fetch can be stopped and resumed and a network drop costs little.

Interpolation
-------------
HRRR's grid is on a Lambert conformal projection (a map projection that keeps shapes
right over mid-latitudes), not on a latitude-longitude grid. Herbie finds the grid
points nearest each station with a precomputed search tree; we take an
inverse-distance-weighted average of the 4 nearest points, which behaves like
bilinear interpolation for smooth fields such as temperature.

Height correction
-----------------
A grid cell's terrain height is an average over 3 km, and a station can sit well above
or below it. Air cools by about 6.5 °C per km on average (the standard lapse rate), so
we adjust HRRR's 2 m temperature to the station's height:
``t_corrected = t_hrrr - 0.0065 * (station_elevation - model_elevation)``.
Dew point is not lapse-corrected: it changes with height differently and less
predictably, so it is compared as is and its offsets are interpreted with care.
"""

from __future__ import annotations

import os
import shutil
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

LAPSE_RATE_C_PER_M = 0.0065
# California crop of the HRRR grid, kept for the gridded-data notebook (Zarr, rechunker,
# xarray-beam). Longitudes in HRRR files run 0-360.
CA_BOX = {"lat": (32.0, 42.5), "lon": (360 - 125.0, 360 - 113.5)}
FIELDS = r":(?:TMP|DPT):2 m above ground"
K_NEAREST = 4


def station_points(hourly: pd.DataFrame) -> pd.DataFrame:
    """One row per station: id, latitude, longitude and elevation (medians over time)."""
    pts = hourly.groupby("station_id")[["latitude", "longitude", "elevation_m"]].median()
    return pts.reset_index()


def _herbie(init_time, fxx: int, save_dir):
    from herbie import Herbie

    return Herbie(init_time, model="hrrr", product="sfc", fxx=fxx, save_dir=save_dir, verbose=False)


def _pick(ds, points: pd.DataFrame) -> pd.DataFrame:
    """Sample a Herbie xarray dataset at the station points (4-point inverse-distance weighting)."""
    picked = ds.herbie.pick_points(points[["latitude", "longitude"]], method="weighted", k=K_NEAREST)
    return picked


def fetch_hour(valid_time, points: pd.DataFrame, save_dir, attempts: int = 3,
               error_log=None, box=None):
    """HRRR 1-hour forecast of 2 m temperature and dew point, valid at ``valid_time``, at each station.

    Returns one row per station with ``hrrr_air_temperature`` and
    ``hrrr_dew_point_temperature`` in °C, or ``None`` if every attempt failed. With
    ``box`` (row and column slices from :func:`california_box`), returns
    ``(table, crop)`` where ``crop`` is a float32 array (2, rows, cols) of temperature
    and dew point in °C over California.
    Waits grow between attempts (5, 10 s); anything still failing is left for the next
    pass rather than holding a worker for minutes.
    """
    warnings.filterwarnings("ignore")
    valid_time = pd.Timestamp(valid_time)
    if valid_time.tzinfo:
        valid_time = valid_time.tz_localize(None)
    init = valid_time - pd.Timedelta(hours=1)
    for attempt in range(attempts):
        # A network drop mid-download can leave a partial subset file (for example
        # temperature without dew point). Herbie reuses an existing file, so a retry
        # would read the same partial file forever; delete it first.
        for stale in Path(save_dir).glob(f"hrrr/{init:%Y%m%d}/subset_*__hrrr.t{init:%H}z.wrfsfcf01.grib2*"):
            stale.unlink(missing_ok=True)
        try:
            H = _herbie(init, 1, save_dir)
            ds = H.xarray(FIELDS, remove_grib=True)
            p = _pick(ds, points)
            table = pd.DataFrame(
                {
                    "station_id": points["station_id"].values,
                    "time": pd.Timestamp(valid_time, tz="UTC"),
                    "hrrr_air_temperature": p["t2m"].values - 273.15,
                    "hrrr_dew_point_temperature": p["d2m"].values - 273.15,
                }
            )
            if box is None:
                return table
            (y0, y1), (x0, x1) = box
            crop = np.stack([ds["t2m"].values[y0:y1, x0:x1], ds["d2m"].values[y0:y1, x0:x1]]) - 273.15
            return table, crop.astype("float32")
        except Exception as exc:
            if error_log:
                with open(error_log, "a") as f:
                    f.write(f"{valid_time} attempt {attempt + 1}: {type(exc).__name__}: {str(exc)[:200]}\n")
            if attempt < attempts - 1:
                time.sleep(5 * 2**attempt)
    return None


def model_elevation(points: pd.DataFrame, save_dir, when="2022-01-01 00:00") -> pd.Series:
    """HRRR's terrain height (m) at each station, from the surface geopotential height field."""
    warnings.filterwarnings("ignore")
    H = _herbie(pd.Timestamp(when), 0, save_dir)
    ds = H.xarray(":HGT:surface", remove_grib=True)
    p = _pick(ds, points)
    return pd.Series(p["orog"].values, index=points["station_id"].values, name="model_elevation_m")


def hour_path(out_dir, hour: pd.Timestamp) -> Path:
    return Path(out_dir).parent / "hrrr_hours" / f"{hour:%Y-%m-%d}" / f"{hour:%H}.parquet"


def california_box(save_dir, grid_dir, when="2022-01-01 00:00"):
    """Row and column ranges of the HRRR grid covering California; saves the crop's coordinates."""
    warnings.filterwarnings("ignore")
    ds = _herbie(pd.Timestamp(when), 1, save_dir).xarray(":TMP:2 m above ground", remove_grib=True)
    lat, lon = ds.latitude.values, ds.longitude.values
    inside = ((lat >= CA_BOX["lat"][0]) & (lat <= CA_BOX["lat"][1])
              & (lon >= CA_BOX["lon"][0]) & (lon <= CA_BOX["lon"][1]))
    rows, cols = np.where(inside.any(axis=1))[0], np.where(inside.any(axis=0))[0]
    box = ((int(rows.min()), int(rows.max()) + 1), (int(cols.min()), int(cols.max()) + 1))
    (y0, y1), (x0, x1) = box
    Path(grid_dir).mkdir(parents=True, exist_ok=True)
    np.savez(Path(grid_dir) / "coords.npz", latitude=lat[y0:y1, x0:x1], longitude=lon[y0:y1, x0:x1] - 360,
             rows=np.array(box[0]), cols=np.array(box[1]))
    return box


def grid_dir_for(out_dir) -> Path:
    return Path(out_dir).parent / "hrrr_grid_ca"


def fetch_hour_cached(hour, points: pd.DataFrame, out_dir, save_dir, box=None, weights=None) -> bool:
    """Fetch one hour unless it is already cached; returns whether the hour is now cached.

    Caching each hour means a network drop only costs the hours it hit: a later pass
    fetches just those, instead of the whole day.
    """
    path = hour_path(out_dir, hour)
    if path.exists():
        return True
    log = Path(out_dir).parent / "hrrr_errors.log"
    if weights is not None:  # direct fetch over one reused connection
        got = fetch_hour_direct(hour, points, save_dir, *weights, box=box, error_log=log)
    else:
        got = fetch_hour(hour, points, save_dir, error_log=log, box=box)
    if got is None:
        return False
    df, crop = got if box is not None else (got, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    if crop is not None:
        np.save(path.with_suffix(".npy"), crop)
    tmp = path.with_suffix(".part")
    df.to_parquet(tmp, index=False)
    tmp.rename(path)  # the Parquet file is written last: its presence means the hour is complete
    return True


def day_path(out_dir, day: pd.Timestamp) -> Path:
    return Path(out_dir) / f"{day:%Y}" / f"{day:%Y-%m-%d}.parquet"


def _write_day(day, frames, missing_hours, points, out_dir) -> Path:
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if missing_hours:  # recorded as rows without values, so the gap is visible
        gap = pd.DataFrame([(s, pd.Timestamp(h, tz="UTC")) for h in missing_hours for s in points["station_id"]],
                           columns=["station_id", "time"])
        result = pd.concat([result, gap], ignore_index=True)
    result = result.sort_values(["station_id", "time"]).reset_index(drop=True)
    hours = pd.date_range(day, periods=24, freq="h")
    crops = [hour_path(out_dir, h).with_suffix(".npy") for h in hours]
    if any(c.exists() for c in crops):
        shape = np.load(next(c for c in crops if c.exists())).shape
        cube = np.stack([np.load(c) if c.exists() else np.full(shape, np.nan, "float32") for c in crops])
        grid_out = grid_dir_for(out_dir) / f"{day:%Y}" / f"{day:%Y-%m-%d}.npz"
        grid_out.parent.mkdir(parents=True, exist_ok=True)
        tmp = grid_out.with_name(grid_out.stem + ".part.npz")
        np.savez_compressed(tmp, data=cube, variables=np.array(["air_temperature", "dew_point_temperature"]),
                            time=hours.values.astype("datetime64[s]"))
        tmp.rename(grid_out)
    out = day_path(out_dir, day)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    result.to_parquet(tmp, index=False)
    tmp.rename(out)
    return out


def build(points: pd.DataFrame, start: str, end: str, out_dir, save_dir, workers: int = 8,
          log=print, passes: int = 8) -> int:
    """Fetch every day from ``start`` to ``end`` (inclusive) and return how many days are done.

    The unit of work is one day, written as one Parquet file only when all 24 hours
    succeeded, so a network drop costs at most the affected days, and a rerun only
    fetches what is missing. Hours run in parallel processes (the GRIB decoder,
    ecCodes, is not reliably thread-safe). After ``passes - 1`` passes, the final pass
    writes days even with failed hours, recording those hours as empty rows: by then
    the file is most likely missing on AWS rather than a passing network problem.
    """
    # Build Herbie's nearest-point search tree once, before any parallel work, so
    # worker processes read it from Herbie's cache instead of racing to create it.
    first = fetch_fields(pd.Timestamp(start) - pd.Timedelta(hours=1), 1, save_dir)
    weights = neighbour_weights(first.latitude.values, first.longitude.values, points)
    box = california_box(save_dir, grid_dir_for(out_dir))
    days = pd.date_range(start, end, freq="D")
    for n in range(1, passes + 1):
        todo = [d for d in days if not day_path(out_dir, d).exists()]
        if not todo:
            break
        final = n == passes
        log(f"pass {n}: {len(todo)} days to fetch")
        t0, done_before = time.time(), len(days) - len(todo)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for i in range(0, len(todo), 7):  # submit a week at a time to bound memory
                week = todo[i:i + 7]
                futs = {pool.submit(fetch_hour_cached, h, points, out_dir, save_dir, box, weights): (d, h)
                        for d in week for h in pd.date_range(d, periods=24, freq="h")}
                missing = {d: [] for d in week}
                for fut in as_completed(futs):
                    d, h = futs[fut]
                    if not fut.result():
                        missing[d].append(h)
                for d, miss in missing.items():
                    if not miss or final:
                        frames = [pd.read_parquet(hour_path(out_dir, h))
                                  for h in pd.date_range(d, periods=24, freq="h") if h not in miss]
                        _write_day(d, frames, miss, points, out_dir)
                        shutil.rmtree(hour_path(out_dir, d).parent, ignore_errors=True)
                done = sum(day_path(out_dir, d).exists() for d in days)
                rate = (done - done_before) / max(time.time() - t0, 1) * 3600
                log(f"{week[-1]:%Y-%m-%d}: {done}/{len(days)} days done, {rate:.0f} days/hour")
        if n < passes:
            time.sleep(30)
    return sum(day_path(out_dir, d).exists() for d in days)


def load(out_dir) -> pd.DataFrame:
    files = sorted(Path(out_dir).glob("*/*.parquet"))
    return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True) if files else pd.DataFrame()


def match(hourly: pd.DataFrame, reference: pd.DataFrame, model_elev: pd.Series) -> pd.DataFrame:
    """Join stations and HRRR by station and hour, apply the height correction, add differences.

    Columns added: ``model_elevation_m``, ``hrrr_air_temperature_adj`` (height-corrected),
    ``diff_air_temperature`` (station minus corrected HRRR) and
    ``diff_dew_point_temperature`` (station minus HRRR, uncorrected).
    """
    cols = ["station_id", "time", "air_temperature", "tmp_quality", "dew_point_temperature",
            "dew_quality", "wind_speed", "precipitation_1h", "elevation_m"]
    m = hourly[cols].merge(reference, on=["station_id", "time"], how="inner")
    m["model_elevation_m"] = m["station_id"].map(model_elev)
    dz = m["elevation_m"] - m["model_elevation_m"]
    m["hrrr_air_temperature_adj"] = m["hrrr_air_temperature"] - LAPSE_RATE_C_PER_M * dz
    m["diff_air_temperature"] = m["air_temperature"] - m["hrrr_air_temperature_adj"]
    m["diff_dew_point_temperature"] = m["dew_point_temperature"] - m["hrrr_dew_point_temperature"]
    return m


# --------------------------------------------------------------------------- direct fetch
# Herbie opens several new HTTPS connections per file (an existence check, the index,
# the byte ranges). At thousands of files that rate of new connections got refused by
# the network between this machine and AWS. The functions below do the same work with
# one long-lived connection per worker process (HTTP keep-alive): fetch the small index
# file, then only the byte ranges of the two fields, over the same connection.

HRRR_URL = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{init:%Y%m%d}/conus/hrrr.t{init:%H}z.wrfsfcf{fxx:02d}.grib2"
WANTED = {":TMP:2 m above ground:", ":DPT:2 m above ground:"}
_session = None


def _http():
    """One requests.Session per process, reused for every request (keep-alive)."""
    global _session
    if _session is None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        _session = requests.Session()
        retry = Retry(total=5, connect=5, read=5, backoff_factor=1.0, status_forcelist=[500, 502, 503, 504])
        _session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1))
    return _session


def byte_ranges(index_text: str, wanted=WANTED) -> list[tuple[int, int | None]]:
    """Byte ranges of the wanted messages, read from a GRIB2 ``.idx`` file.

    Each index line is ``number:start_byte:d=YYYYMMDDHH:VARIABLE:LEVEL:FORECAST:``; a
    message ends one byte before the next message starts.
    """
    lines = [l for l in index_text.strip().splitlines() if l]
    starts = [int(l.split(":")[1]) for l in lines]
    out = []
    for i, line in enumerate(lines):
        if any(w in ":" + ":".join(line.split(":")[3:]) for w in wanted):
            end = starts[i + 1] - 1 if i + 1 < len(lines) else None
            out.append((starts[i], end))
    return out


def fetch_fields(init: pd.Timestamp, fxx: int, tmp_dir) -> "xr.Dataset":
    """Download the 2 m temperature and dew point messages of one HRRR file and open them."""
    import xarray as xr

    url = HRRR_URL.format(init=init, fxx=fxx)
    s = _http()
    idx = s.get(url + ".idx", timeout=30)
    idx.raise_for_status()
    parts = []
    for start, end in byte_ranges(idx.text):
        r = s.get(url, headers={"Range": f"bytes={start}-{'' if end is None else end}"}, timeout=60)
        r.raise_for_status()
        parts.append(r.content)
    if len(parts) != len(WANTED):
        raise RuntimeError(f"{url}: found {len(parts)} of {len(WANTED)} fields in the index")
    path = Path(tmp_dir) / f"hrrr_{init:%Y%m%d%H}_f{fxx:02d}_{os.getpid()}.grib2"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(parts))
    try:
        ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""}).load()
    finally:
        path.unlink(missing_ok=True)
    return ds


def neighbour_weights(grid_lat, grid_lon, points: pd.DataFrame, k: int = K_NEAREST):
    """Flat indices of the k nearest grid points to each station and inverse-distance weights.

    Same method as Herbie's ``pick_points(method="weighted")``: great-circle distances
    from a ball tree, weights proportional to 1/distance, normalised to sum to 1.
    """
    from sklearn.neighbors import BallTree

    lon = np.where(grid_lon > 180, grid_lon - 360, grid_lon)
    tree = BallTree(np.deg2rad(np.c_[grid_lat.ravel(), lon.ravel()]), metric="haversine")
    dist, idx = tree.query(np.deg2rad(points[["latitude", "longitude"]].to_numpy()), k=k)
    w = 1.0 / np.maximum(dist, 1e-12)
    return idx, w / w.sum(axis=1, keepdims=True)


def fetch_hour_direct(valid_time, points, tmp_dir, idx, w, box=None, attempts: int = 3, error_log=None):
    """Like :func:`fetch_hour`, over one reused connection and with precomputed neighbour weights."""
    valid_time = pd.Timestamp(valid_time)
    if valid_time.tzinfo:
        valid_time = valid_time.tz_localize(None)
    init = valid_time - pd.Timedelta(hours=1)
    for attempt in range(attempts):
        try:
            ds = fetch_fields(init, 1, tmp_dir)
            t2m, d2m = ds["t2m"].values, ds["d2m"].values
            table = pd.DataFrame({
                "station_id": points["station_id"].values,
                "time": pd.Timestamp(valid_time, tz="UTC"),
                "hrrr_air_temperature": (t2m.ravel()[idx] * w).sum(axis=1) - 273.15,
                "hrrr_dew_point_temperature": (d2m.ravel()[idx] * w).sum(axis=1) - 273.15,
            })
            if box is None:
                return table
            (y0, y1), (x0, x1) = box
            crop = np.stack([t2m[y0:y1, x0:x1], d2m[y0:y1, x0:x1]]) - 273.15
            return table, crop.astype("float32")
        except Exception as exc:
            if error_log:
                with open(error_log, "a") as f:
                    f.write(f"{valid_time} attempt {attempt + 1}: {type(exc).__name__}: {str(exc)[:200]}\n")
            if attempt < attempts - 1:
                time.sleep(5 * 2**attempt)
    return None


if __name__ == "__main__":
    # Resumable full fetch: python -m wxlab.reference [data_dir]
    import sys

    from wxlab import stations

    data_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    hourly = stations.load_standardized(data_dir / "standardized" / "isd", years=range(2021, 2025))
    pts = station_points(hourly)
    (data_dir / "reference").mkdir(parents=True, exist_ok=True)
    pts.to_parquet(data_dir / "reference" / "station_points.parquet", index=False)
    elev = model_elevation(pts, data_dir / "hrrr_cache")
    elev.rename_axis("station_id").reset_index().to_parquet(data_dir / "reference" / "hrrr_model_elevation.parquet", index=False)
    build(pts, "2021-01-01", "2024-12-31", data_dir / "reference" / "hrrr", data_dir / "hrrr_cache",
          log=lambda m: print(m, flush=True))
    print("DONE", flush=True)
