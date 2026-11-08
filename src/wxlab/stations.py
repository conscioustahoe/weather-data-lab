"""Reading and standardizing NOAA Integrated Surface Database (ISD) station data.

Where the data comes from
-------------------------
NOAA's ISD collects observations from tens of thousands of weather stations. The
AWS open-data copy (bucket ``noaa-global-hourly-pds``, readable anonymously)
stores one CSV per station per year at ``YYYY/<USAF><WBAN>.csv``. USAF and WBAN
are two historical station-numbering schemes; together they form the station id.
NOAA's station list, ``isd-history.csv``, gives each station's name, state,
coordinates, elevation and the first and last dates it reported.

How temperature is stored
-------------------------
The ``TMP`` column looks like ``"+0217,1"``: air temperature in *tenths* of a
degree Celsius (so 21.7 °C), then a comma, then a one-character quality code set
by NOAA's own checks. ``+9999`` means "missing". The codes used most are
(ISD format document, section on quality codes):

- ``1`` / ``5`` passed all quality control checks
- ``2`` / ``6`` suspect
- ``3`` / ``7`` erroneous
- ``9`` missing
- ``A`` and other letters: flags from specific processing steps (for example
  ``A`` = "suspect, but accepted as good")

(1/2/3 come from NCEI's own data; 5/6/7 from data that arrived from other sources.)

Other fields used
-----------------
- ``DEW`` dew point, same layout as ``TMP`` (tenths of °C, ``+9999`` missing). Dew point
  is the temperature at which the air's water vapour would start to condense; it can
  never be higher than the air temperature.
- ``WND`` wind, ``"220,1,N,0026,1"``: direction in degrees (``999`` missing or
  variable), its quality code, an observation type (``N`` normal, ``C`` calm,
  ``V`` variable), speed in tenths of m/s (``9999`` missing), and its quality code.
- ``AA1`` liquid precipitation, ``"01,0003,9,5"``: the accumulation period in hours,
  depth in tenths of mm (``9999`` missing), a condition code and a quality code. Only
  one-hour accumulations are kept; synoptic reports carry 6-hour totals and daily
  summaries 24-hour totals, which would double count.

Why one hour has several rows
-----------------------------
A station-year file is not one row per hour. Several *report types* overlap:

- ``FM-15`` is the routine hourly airport report (METAR). Airports issue it a few
  minutes *before* the hour, for example at 00:51, so that it is ready at the top
  of the hour.
- ``FM-16`` is a special report (SPECI), issued whenever the weather changes
  enough to matter to pilots (visibility drops, a storm arrives). They appear at
  any minute.
- ``FM-12`` is a synoptic report, the international format exchanged every 3 or 6
  hours, timed exactly on the hour.
- ``SOD`` / ``SOM`` are daily and monthly summaries, not observations.

For a training table we want exactly one value per station and hour, chosen by a
written rule rather than by whatever row comes first. The rule here is:

1. Drop summaries (``SOD``, ``SOM``) and rows whose temperature is missing.
2. Assign each remaining report to the *nearest* UTC hour (00:51 → 01:00,
   01:17 → 01:00), so each hour owns a window of ±30 minutes.
3. Within an hour, prefer ``FM-15``; among equals, prefer the report closest to
   the hour; break remaining ties by the earlier time.

The original report time, report type and quality code are kept in the output, so
the choice can always be audited. Raw files are cached untouched.
"""

from __future__ import annotations

import io
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

BUCKET = "noaa-global-hourly-pds"
HISTORY_URL = "https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv"
RAW_COLUMNS = ["STATION", "DATE", "LATITUDE", "LONGITUDE", "ELEVATION", "NAME", "REPORT_TYPE",
               "TMP", "DEW", "WND", "AA1"]
SUMMARY_TYPES = {"SOD", "SOM"}
# Preference order for choosing the hourly value; anything not listed ranks last.
REPORT_PREFERENCE = {"FM-15": 0, "FM-16": 1, "FM-12": 2}


# --------------------------------------------------------------------------- station list
def load_station_history(cache_dir: str | os.PathLike, refresh: bool = False) -> pd.DataFrame:
    """Download (once) and parse NOAA's ``isd-history.csv`` station list."""
    path = Path(cache_dir) / "isd-history.csv"
    if refresh or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        import urllib.request

        urllib.request.urlretrieve(HISTORY_URL, path)
    df = pd.read_csv(path, dtype=str)
    df = df.rename(columns={"STATION NAME": "name", "ELEV(M)": "elevation_m", "LAT": "lat", "LON": "lon"})
    df.columns = [c.lower() for c in df.columns]
    df["station_id"] = df["usaf"] + df["wban"]
    for col in ("lat", "lon", "elevation_m"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["begin"] = pd.to_datetime(df["begin"], format="%Y%m%d", errors="coerce")
    df["end"] = pd.to_datetime(df["end"], format="%Y%m%d", errors="coerce")
    return df


def california_stations(history: pd.DataFrame, start_year: int = 2015, end_year: int = 2024) -> pd.DataFrame:
    """US stations in California whose active dates cover the whole period.

    The station list's dates say when a station *reported at all*; they do not
    promise a complete record, so completeness is measured later from the data.
    Stations with placeholder coordinates (0, 0) or missing elevation are dropped.
    """
    ca = history[(history["ctry"] == "US") & (history["state"] == "CA")]
    ca = ca[(ca["begin"] <= f"{start_year}-01-01") & (ca["end"] >= f"{end_year}-12-31")]
    ca = ca.dropna(subset=["lat", "lon", "elevation_m"])
    ca = ca[(ca["lat"] != 0) & (ca["lon"] != 0)]
    # A few stations appear twice under different WBAN numbers; keep one per id.
    return ca.drop_duplicates("station_id").reset_index(drop=True)


# --------------------------------------------------------------------------- download
def _fs():
    import s3fs

    return s3fs.S3FileSystem(anon=True)


def raw_path(raw_dir, station_id: str, year: int) -> Path:
    return Path(raw_dir) / str(year) / f"{station_id}.csv"


def download_station_year(station_id: str, year: int, raw_dir, fs=None) -> Path | None:
    """Copy one station-year CSV from S3 into the raw cache. Skips if cached.

    Returns the local path, or ``None`` if the station has no file that year
    (common: stations go quiet for a year, or report under another id).
    """
    dest = raw_path(raw_dir, station_id, year)
    if dest.exists():
        return dest
    fs = fs or _fs()
    key = f"{BUCKET}/{year}/{station_id}.csv"
    try:
        data = fs.cat_file(key)
    except FileNotFoundError:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.rename(dest)  # atomic: a crash never leaves a half-written cache file
    return dest


def download_many(station_ids, years, raw_dir, max_workers: int = 16, progress: bool = True) -> pd.DataFrame:
    """Download many station-years in parallel threads (I/O bound, so threads suffice)."""
    fs = _fs()
    jobs = [(s, y) for s in station_ids for y in years]
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(download_station_year, s, y, raw_dir, fs): (s, y) for s, y in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            s, y = futs[fut]
            try:
                path = fut.result()
                results.append((s, y, path is not None, None))
            except Exception as exc:  # network hiccups: record and carry on
                results.append((s, y, False, repr(exc)))
            if progress and i % 100 == 0:
                print(f"  {i}/{len(jobs)} station-years fetched")
    return pd.DataFrame(results, columns=["station_id", "year", "available", "error"])


# --------------------------------------------------------------------------- parse
def _split(series: pd.Series, n: int, missing: str) -> pd.DataFrame:
    """Split a comma-separated ISD field into ``n`` string columns (missing rows → ``missing``)."""
    return series.fillna(missing).str.split(",", n=n - 1, expand=True).reindex(columns=range(n))


def _number(text: pd.Series, sentinel: int, scale: float) -> pd.Series:
    """Parse a scaled integer field, turning the ISD missing sentinel into NaN."""
    v = pd.to_numeric(text, errors="coerce")
    return v.where(v != sentinel) / scale


def parse_raw(source) -> pd.DataFrame:
    """Parse a raw ISD CSV into one row per report (no hourly reduction yet).

    Columns: ``station_id``, ``obs_time`` (UTC), ``report_type``,
    ``air_temperature`` and ``dew_point_temperature`` (°C), ``wind_speed`` (m/s),
    ``wind_from_direction`` (degrees), ``precipitation_1h`` (mm), each value's raw
    NOAA quality code (``*_quality``), ``latitude``, ``longitude``, ``elevation_m``.
    """
    df = pd.read_csv(source, usecols=lambda c: c in RAW_COLUMNS, dtype=str)
    for col in ("DEW", "WND", "AA1"):
        if col not in df:
            df[col] = np.nan
    tmp = _split(df["TMP"], 2, "+9999,9")
    dew = _split(df["DEW"], 2, "+9999,9")
    wnd = _split(df["WND"], 5, "999,9,9,9999,9")
    aa1 = _split(df["AA1"], 4, "99,9999,9,9")
    one_hour = pd.to_numeric(aa1[0], errors="coerce") == 1
    out = pd.DataFrame(
        {
            "station_id": df["STATION"].astype(str).str.zfill(11),
            "obs_time": pd.to_datetime(df["DATE"], utc=True),  # ISD times are UTC
            "report_type": df["REPORT_TYPE"].str.strip(),
            "air_temperature": _number(tmp[0], 9999, 10.0),
            "tmp_quality": tmp[1].fillna("9").str.strip(),
            "dew_point_temperature": _number(dew[0], 9999, 10.0),
            "dew_quality": dew[1].fillna("9").str.strip(),
            "wind_speed": _number(wnd[3], 9999, 10.0),
            "wind_from_direction": _number(wnd[0], 999, 1.0),
            "wind_type": wnd[2].fillna("9").str.strip(),
            "wind_quality": wnd[4].fillna("9").str.strip(),
            "precipitation_1h": _number(aa1[1], 9999, 10.0).where(one_hour),
            "precip_quality": aa1[3].fillna("9").str.strip().where(one_hour, "9"),
            "latitude": pd.to_numeric(df["LATITUDE"], errors="coerce"),
            "longitude": pd.to_numeric(df["LONGITUDE"], errors="coerce"),
            "elevation_m": pd.to_numeric(df["ELEVATION"], errors="coerce"),
        }
    )
    return out


def to_hourly(reports: pd.DataFrame) -> pd.DataFrame:
    """Reduce reports to one row per station-hour by the rule in the module docstring.

    Reports that carry a temperature are preferred, then the report type, then the
    distance from the hour. The other variables come from the chosen report, so every
    value in a row was measured at the same moment. An hour whose only reports lack a
    temperature is kept, with ``air_temperature`` missing: "the station reported but the
    thermometer value is missing" is different from "the station said nothing", and
    the difference matters when asking whether data goes missing in storms.

    Output columns: ``station_id``, ``time`` (UTC hour), the variables and quality codes
    from :func:`parse_raw`, ``report_type``, ``obs_time`` (original report time),
    ``n_reports`` (how many reports with a temperature competed for the hour), and
    coordinates.
    """
    variables = ["air_temperature", "dew_point_temperature", "wind_speed", "precipitation_1h"]
    r = reports[~reports["report_type"].isin(SUMMARY_TYPES)].dropna(subset=variables, how="all").copy()
    if r.empty:
        return r.assign(time=pd.Series(dtype="datetime64[ns, UTC]"), n_reports=pd.Series(dtype=int))
    r["time"] = r["obs_time"].dt.round("h")
    r["_no_temp"] = r["air_temperature"].isna()
    r["_offset"] = (r["obs_time"] - r["time"]).abs()
    r["_pref"] = r["report_type"].map(REPORT_PREFERENCE).fillna(len(REPORT_PREFERENCE)).astype(int)
    r = r.sort_values(["station_id", "time", "_no_temp", "_pref", "_offset", "obs_time"])
    counts = (~r["_no_temp"]).groupby([r["station_id"], r["time"]]).sum().rename("n_reports")
    out = r.drop_duplicates(["station_id", "time"], keep="first").join(counts, on=["station_id", "time"])
    cols = ["station_id", "time", "air_temperature", "tmp_quality",
            "dew_point_temperature", "dew_quality", "wind_speed", "wind_from_direction",
            "wind_type", "wind_quality", "precipitation_1h", "precip_quality",
            "report_type", "obs_time", "n_reports", "latitude", "longitude", "elevation_m"]
    return out[cols].reset_index(drop=True)


# --------------------------------------------------------------------------- standardize
def standardized_path(std_dir, station_id: str, year: int) -> Path:
    return Path(std_dir) / f"year={year}" / f"{station_id}.parquet"


def standardize_station_year(station_id: str, year: int, raw_dir, std_dir, overwrite: bool = False) -> Path | None:
    """Raw CSV → hourly Parquet for one station-year. Idempotent: same input, same file."""
    out = standardized_path(std_dir, station_id, year)
    if out.exists() and not overwrite:
        return out
    src = raw_path(raw_dir, station_id, year)
    if not src.exists():
        return None
    hourly = to_hourly(parse_raw(src))
    hourly = hourly[hourly["time"].dt.year == year]  # :51 on Dec 31 23h rounds into next year
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    hourly.to_parquet(tmp, index=False)
    tmp.rename(out)
    return out


def build(station_ids, years, raw_dir, std_dir, max_workers: int = 16, progress: bool = False) -> pd.DataFrame:
    """Download and standardize every station-year. Safe to rerun; cached units are skipped."""
    status = download_many(station_ids, years, raw_dir, max_workers=max_workers, progress=progress)
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        paths = list(pool.map(lambda sy: standardize_station_year(*sy, raw_dir, std_dir),
                              zip(status["station_id"], status["year"])))
    status["standardized"] = [p is not None for p in paths]
    return status


def load_standardized(std_dir, station_ids=None, years=None) -> pd.DataFrame:
    """Read standardized Parquet files back into one table."""
    files = sorted(Path(std_dir).glob("year=*/*.parquet"))
    if years is not None:
        files = [f for f in files if int(f.parent.name.split("=")[1]) in set(years)]
    if station_ids is not None:
        files = [f for f in files if f.stem in set(station_ids)]
    if not files:
        return pd.DataFrame()
    return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)


def completeness(hourly: pd.DataFrame) -> pd.DataFrame:
    """Fraction of hours with a temperature value, per station and year."""
    h = hourly[hourly["air_temperature"].notna()]
    h = h.assign(year=h["time"].dt.year)
    got = h.groupby(["station_id", "year"]).size()
    years = got.index.get_level_values("year")
    hours_in_year = np.where(pd.Series(years).apply(lambda y: pd.Timestamp(f"{y}-12-31").is_leap_year), 8784, 8760)
    return (got / hours_in_year).unstack("year")


NETWORKS = {
    "FM-15": "airport", "FM-16": "airport", "FM-12": "airport", "SY-MT": "airport",
    "CRN05": "climate reference", "FM-13": "coastal",
}


def station_networks(hourly: pd.DataFrame) -> pd.Series:
    """Label each station by the kind of network it belongs to, from its usual report type.

    The station list mixes three networks that look alike in ``isd-history.csv``:

    - **airport** stations (ASOS/AWOS) issue routine METAR reports (FM-15);
    - **climate reference** stations belong to the US Climate Reference Network, a
      research network built for long-term climate monitoring with three redundant
      thermometers per site; ISD stores their 5-minute data as report type CRN05;
    - **coastal** stations are water-level and weather stations on piers, bays and
      platforms, reporting in the marine synoptic format (FM-13).
    """
    usual = hourly.groupby("station_id")["report_type"].agg(lambda s: s.mode().iat[0])
    return usual.map(NETWORKS).fillna("other").rename("network")

