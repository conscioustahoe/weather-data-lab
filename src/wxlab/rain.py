"""Rain: matching station gauges to the MRMS radar mosaic, and scoring the match.

A rain gauge and a radar measure rain in very different ways, so neither is the truth.

- A **gauge** catches what falls into a funnel about 20 cm across and reports it as a
  depth. Airport gauges (ASOS) are heated tipping buckets. They miss some rain in wind
  (the air flowing over the funnel carries drops past it), they can clog, and in very
  heavy rain the bucket can't tip fast enough. They see one point.
- **MRMS radar-only** turns reflectivity from the nearest radars into rain on a 1 km
  grid. It sees the whole area but from above, often far above (notebook 02), and the
  reflectivity-to-rain formula is only roughly right.

Rain also needs its own scores. Most hours are dry and a few are very wet, so squared
error is dominated by a handful of storms and by timing. The scores here split the
question into detection (did both see rain?), amount (how do totals compare?) and
category (SEEPS on daily totals, the score WeatherBench 2 uses for precipitation).

The first part of this module extracts MRMS values at station points. It decodes each
gzipped GRIB2 file with ecCodes directly, which is far faster than going through
cfgrib, and works in units of one product-month written to Parquet, so the job can be
stopped and rerun and only does what's missing.
"""

from __future__ import annotations

import gzip
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from wxlab import radar as rd

# MRMS CONUS grid: 7000 columns x 3500 rows at 0.01 degrees, first point at the north-west
# corner, rows running south. Read from the files' own GRIB keys.
MRMS_LAT0, MRMS_LON0, MRMS_STEP, MRMS_NI, MRMS_NJ = 54.995, 230.005, 0.01, 7000, 3500
PRODUCTS = {"radar_only": "RadarOnly_QPE_01H_00.00", "multi_sensor": "MultiSensor_QPE_01H_Pass2_00.00"}

# A gauge reports precipitation in steps of 0.25 mm (0.01 inch), so anything below that is
# "dry" to it. Using the same threshold for both sources keeps detection scores fair.
WET_MM = 0.25


def grid_index(lat, lon):
    """Row and column of the MRMS cell that holds each point."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float) % 360
    row = np.round((MRMS_LAT0 - lat) / MRMS_STEP).astype(int)
    col = np.round((lon - MRMS_LON0) / MRMS_STEP).astype(int)
    return row, col


def _values_at(path: Path, rows, cols, radius: int = 1):
    """Point value and the mean of the surrounding (2r+1)^2 cells, from one MRMS file."""
    import eccodes

    with gzip.open(path) as f:
        msg = f.read()
    h = eccodes.codes_new_from_message(msg)
    try:
        v = eccodes.codes_get_values(h).reshape(MRMS_NJ, MRMS_NI)
    finally:
        eccodes.codes_release(h)
    v = np.where(v < 0, np.nan, v)          # -3: no radar coverage
    point = v[rows, cols]
    k = 2 * radius + 1
    win = np.stack([v[rows + dy, cols + dx] for dy in range(-radius, radius + 1)
                    for dx in range(-radius, radius + 1)])
    with np.errstate(invalid="ignore"):
        area = np.nanmean(win, axis=0) if k > 1 else point
    return point, area


def _month(args):
    raw_dir, product, month, station_ids, rows, cols, out = args
    times = pd.date_range(month, month + pd.offsets.MonthBegin(1), freq="h", inclusive="left", tz="UTC")
    # File time stamps mark the end of the hour; the hour starting at t ends at t+1h.
    frames = []
    for t in times:
        path = rd.mrms_path(raw_dir, PRODUCTS[product], t + pd.Timedelta("1h"))
        if not path.exists():
            continue
        point, area = _values_at(path, rows, cols)
        frames.append(pd.DataFrame({"station_id": station_ids, "time": t, "mm": point, "mm_3x3": area}))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["station_id", "time", "mm", "mm_3x3"])
    tmp = out.with_suffix(".part")
    df.to_parquet(tmp, index=False)
    tmp.rename(out)
    return out


def extract_mrms_points(raw_dir: Path, out_dir: Path, points: pd.DataFrame,
                        start="2021-01", end="2025-01", products=tuple(PRODUCTS), workers=8):
    """MRMS hourly rain at every station, one Parquet file per product and month.

    ``time`` in the output is the start of the hour, the same convention as the station
    table, so ``time`` 22:00 holds rain from 22:00 to 23:00. Months already on disk are
    skipped.
    """
    rows, cols = grid_index(points.latitude, points.longitude)
    jobs = []
    for product in products:
        d = Path(out_dir) / f"product={product}"
        d.mkdir(parents=True, exist_ok=True)
        for m in pd.date_range(start, end, freq="MS", inclusive="left", tz="UTC"):
            out = d / f"{m:%Y-%m}.parquet"
            if not out.exists():
                jobs.append((Path(raw_dir), product, m, points.station_id.to_numpy(), rows, cols, out))
    with ProcessPoolExecutor(workers) as pool:
        for out in pool.map(_month, jobs):
            print("wrote", out.parent.name, out.name, flush=True)
    return len(jobs)


def load_mrms_points(out_dir: Path) -> pd.DataFrame:
    """All extracted MRMS station values, one column pair per product."""
    parts = []
    for product in PRODUCTS:
        files = sorted((Path(out_dir) / f"product={product}").glob("*.parquet"))
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        df = df.rename(columns={"mm": product, "mm_3x3": f"{product}_3x3"})
        parts.append(df.set_index(["station_id", "time"]))
    return pd.concat(parts, axis=1).reset_index()


# ---------------------------------------------------------------------------
# Gauges and matching
# ---------------------------------------------------------------------------

def load_gauges(std_dir: Path, years=range(2021, 2025)) -> pd.DataFrame:
    """Hourly gauge rain from the standardized station table, labelled by hour start.

    The station table puts a report on its nearest hour, so the routine airport report at
    22:53 sits at 23:00. Its one-hour rain covers roughly 21:53 to 22:53, which is the hour
    that *starts* at 22:00. Shifting ``time`` back one hour lines it up with MRMS's hour
    starting at 22:00, about seven minutes apart.

    Reports with ISD quality codes 2, 3, 6 or 7 (suspect or erroneous) are kept but
    marked, because part of the point here is to see whether radar agrees with NOAA.
    """
    from wxlab import stations as st

    df = st.load_standardized(Path(std_dir), years=years)
    df = df[["station_id", "time", "precipitation_1h", "precip_quality", "report_type"]].rename(
        columns={"precipitation_1h": "gauge"})
    df["time"] = df["time"] - pd.Timedelta("1h")
    df["noaa_suspect"] = df["precip_quality"].astype(str).isin(["2", "3", "6", "7"])
    return df.dropna(subset=["gauge"])


def match(gauges: pd.DataFrame, mrms: pd.DataFrame) -> pd.DataFrame:
    """Station-hours with both a gauge value and MRMS values."""
    m = gauges.merge(mrms, on=["station_id", "time"], how="inner")
    return m.dropna(subset=["radar_only", "multi_sensor"])


# ---------------------------------------------------------------------------
# Geometry: how high is the lowest radar beam above each station?
# ---------------------------------------------------------------------------

def radar_sites(lat_range=(31.5, 43.0), lon_range=(-125.5, -113.5)) -> pd.DataFrame:
    """NEXRAD sites around California, with antenna height above sea level in metres.

    Site locations come from Py-ART's table (elevations there are in feet, ground level);
    about 20 m is added for the tower. Terminal Doppler radars (ids starting with T) are
    left out because MRMS weights them differently and they scan at another wavelength.
    """
    import os

    os.environ.setdefault("PYART_QUIET", "1")
    from pyart.io.nexrad_common import NEXRAD_LOCATIONS

    rows = [{"radar": k, "latitude": v["lat"], "longitude": v["lon"], "antenna_m": v["elev"] * 0.3048 + 20}
            for k, v in NEXRAD_LOCATIONS.items()
            if k.startswith("K") and lat_range[0] < v["lat"] < lat_range[1] and lon_range[0] < v["lon"] < lon_range[1]]
    return pd.DataFrame(rows)


def lowest_beam(points: pd.DataFrame, sites: pd.DataFrame, elevation_deg: float = 0.5) -> pd.DataFrame:
    """For each station, the radar whose lowest beam passes closest above the ground there.

    Uses the 4/3 Earth beam height (``radar.beam_height``) at the standard lowest tilt of
    0.5 degrees. Beam blockage by mountains is ignored, so in rough terrain the real
    lowest useful beam is higher than this.
    """
    la1, lo1 = np.deg2rad(points.latitude.to_numpy())[:, None], np.deg2rad(points.longitude.to_numpy())[:, None]
    la2, lo2 = np.deg2rad(sites.latitude.to_numpy())[None, :], np.deg2rad(sites.longitude.to_numpy())[None, :]
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    dist = 2 * 6_371_000 * np.arcsin(np.sqrt(a))
    height = rd.beam_height(dist, elevation_deg, sites.antenna_m.to_numpy()[None, :]) - points.elevation_m.to_numpy()[:, None]
    best = np.argmin(height, axis=1)
    i = np.arange(len(points))
    return pd.DataFrame({"station_id": points.station_id.to_numpy(), "radar": sites.radar.to_numpy()[best],
                         "radar_km": dist[i, best] / 1000, "beam_m": height[i, best]})


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------

def detection(gauge, radar, wet=WET_MM) -> dict:
    """Contingency counts and scores for "did it rain?", with the gauge as reference.

    - POD (probability of detection): of the gauge's wet periods, the share radar also saw.
    - FAR (false alarm ratio): of radar's wet periods, the share the gauge saw as dry.
    - CSI (critical success index): hits over hits + misses + false alarms.
    """
    g, r = np.asarray(gauge) >= wet, np.asarray(radar) >= wet
    hits, misses, false = int((g & r).sum()), int((g & ~r).sum()), int((~g & r).sum())
    return {"hits": hits, "misses": misses, "false_alarms": false,
            "pod": hits / max(hits + misses, 1), "far": false / max(hits + false, 1),
            "csi": hits / max(hits + misses + false, 1)}


def daily(hourly: pd.DataFrame, cols, min_hours: int = 22) -> pd.DataFrame:
    """Daily totals (UTC days) for station-days where every source has ``min_hours`` hours."""
    df = hourly.assign(day=hourly.time.dt.floor("D"))
    g = df.groupby(["station_id", "day"])
    out = g[cols].sum(min_count=1)
    n = g[cols].count().min(axis=1)
    return out[n >= min_hours].reset_index()


def gauge_witness(hourly: pd.DataFrame, gauge_min=25.0, radar_max=2.5) -> pd.DataFrame:
    """Hours where the gauge reports heavy rain and radar sees almost none.

    25 mm in an hour is a downpour. If radar-only MRMS has less than a tenth of that in the
    cell above the gauge, one of them is wrong, and radar rarely misses a downpour
    entirely unless the station is in a known blind spot. These hours are suspects for
    gauge faults: electrical spikes, a tipping bucket chattering, snow melting into a
    heated gauge long after it fell.
    """
    h = hourly
    return h[(h.gauge >= gauge_min) & (h.radar_only < radar_max)]


def seeps_stations(thresholds: pd.DataFrame, lo=0.1, hi=0.85) -> pd.DataFrame:
    """Stations where SEEPS is defined: the dry-day probability must lie in [0.1, 0.85].

    WeatherBench 2 follows Rodwell et al. (2010) and leaves out places that are almost
    always dry or almost always wet, because the score's weights blow up there.
    """
    t = thresholds
    return t[(t.p1 >= lo) & (t.p1 <= hi) & t.heavy_mm.notna()]


def seeps_thresholds(obs_daily: pd.DataFrame, col: str, dry=WET_MM) -> pd.DataFrame:
    """Per-station SEEPS climatology: the dry probability p1 and the light/heavy threshold.

    SEEPS (Rodwell et al. 2010, *QJRMS* 136) puts each day in one of three categories:
    dry, light and heavy. The light/heavy line is set at each station so that, among
    wet days, light days are twice as common as heavy ones. That makes the categories
    mean the same thing in a desert and a rainforest.
    """
    def one(x):
        x = x.dropna()
        wet = x[x > dry]
        return pd.Series({"p1": float((x <= dry).mean()),
                          "heavy_mm": float(wet.quantile(2 / 3)) if len(wet) >= 10 else np.nan,
                          "days": len(x)})
    return obs_daily.groupby("station_id")[col].apply(one).unstack().reset_index()


def seeps(obs, fc, p1, heavy, dry=WET_MM):
    """SEEPS penalty per day: 0 is perfect, about 1 is no better than climatology.

    Rows of the scoring matrix are the observed category and columns the forecast one,
    as in Rodwell et al. (2010) and WeatherBench 2. Use only stations from
    ``seeps_stations``, where ``p1`` is in [0.1, 0.85] and the score is well behaved.
    """
    p1 = np.asarray(p1, dtype=float)
    cat = lambda x: np.where(x <= dry, 0, np.where(x <= heavy, 1, 2))
    o, f = cat(np.asarray(obs)), cat(np.asarray(fc))
    m = np.zeros(o.shape + (3, 3))
    m[..., 0, 1] = 1 / (1 - p1)
    m[..., 0, 2] = 4 / (1 - p1)
    m[..., 1, 0] = 1 / p1
    m[..., 1, 2] = 3 / (1 - p1)
    m[..., 2, 0] = 1 / p1 + 3 / (2 + p1)
    m[..., 2, 1] = 3 / (2 + p1)
    return 0.5 * np.take_along_axis(np.take_along_axis(m, o[..., None, None], -2)[..., 0, :], f[..., None], -1)[..., 0]


if __name__ == "__main__":
    import sys

    data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    pts = pd.read_parquet(data / "reference" / "station_points.parquet")
    n = extract_mrms_points(data / "raw", data / "work" / "mrms_points", pts)
    print("done", n, "months")
