"""Download and read raw radar data: MRMS hourly rainfall and NEXRAD Level II volumes.

Phase 2 adds radar as a second kind of sensor. The first half of this module fetches
the raw files and keeps them as they are (the raw layer stays immutable). The second
half reads them: a Level II volume with Py-ART, an MRMS file with cfgrib, and the
step from radar reflectivity to rain on the MRMS grid that notebook 02 uses.

Two sources, both public on AWS and read without an account:

- **MRMS** (Multi-Radar Multi-Sensor) is NOAA's national mosaic. It merges every US
  radar onto one 0.01 degree grid (about 1 km) and corrects it. We take two of its
  one-hour rainfall products at the top of each hour for 2021–2024.
  ``RadarOnly_QPE_01H`` is rainfall estimated from radar alone. ``MultiSensor_QPE_01H_Pass2``
  also blends in rain gauges and model data, so it's better but not independent of
  gauges. Notebook 10 needs both to tell radar error apart from gauge error. Each
  file is a gzipped GRIB2 of about 0.5 MB. Bucket: ``noaa-mrms-pds``.
- **NEXRAD Level II** is the raw output of a single radar: one file per volume scan
  (a full set of sweeps at several elevation angles) every 4 to 10 minutes. A stormy day
  is about 2.6 GB per radar. We take a few big California storms from two radars.
  Bucket: ``unidata-nexrad-level2``.

The download reuses one HTTP connection per worker thread (keep-alive), writes each
file atomically (``.part`` then rename) and skips files already on disk, so it can be
stopped and restarted at any time and picks up where it left off.
"""

from __future__ import annotations

import datetime as dt
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

MRMS_PRODUCTS = ("RadarOnly_QPE_01H_00.00", "MultiSensor_QPE_01H_Pass2_00.00")
MRMS_URL = "https://noaa-mrms-pds.s3.amazonaws.com/CONUS/{product}/{t:%Y%m%d}/MRMS_{product}_{t:%Y%m%d-%H}0000.grib2.gz"

# Two radars covering the two big population centres:
# KMUX is Mount Umunhum above the Bay Area, KVTX is Los Angeles (Sulphur Mountain).
LEVEL2_RADARS = ("KMUX", "KVTX")
# One UTC day for each of five well-known California storms.
LEVEL2_DAYS = {
    "2021-10-24": "bomb cyclone and atmospheric river, record rain in Sacramento",
    "2021-12-14": "strong winter storm, heavy Sierra snow",
    "2023-01-09": "atmospheric river series, widespread flooding",
    "2023-08-21": "remnants of Hurricane Hilary over Southern California",
    "2024-02-05": "atmospheric river, record rain in Los Angeles",
}
LEVEL2_BUCKET = "unidata-nexrad-level2"
LEVEL2_URL = "https://unidata-nexrad-level2.s3.amazonaws.com/{key}"

log = logging.getLogger("wxlab.radar")
_local = threading.local()


def _http():
    """One requests.Session per thread, reused for every request (keep-alive)."""
    if getattr(_local, "session", None) is None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        s = requests.Session()
        retry = Retry(total=5, connect=5, read=5, backoff_factor=1.0, status_forcelist=[500, 502, 503, 504])
        s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1))
        _local.session = s
    return _local.session


def _download(url: str, path: Path) -> str:
    """Fetch one file to ``path`` atomically. Returns 'ok', 'skip' or 'missing'."""
    if path.exists():
        return "skip"
    path.parent.mkdir(parents=True, exist_ok=True)
    r = _http().get(url, timeout=60)
    if r.status_code in (403, 404):
        return "missing"
    r.raise_for_status()
    part = path.with_suffix(path.suffix + ".part")
    part.write_bytes(r.content)
    part.rename(path)
    return "ok"


def mrms_jobs(raw_dir: Path, start="2021-01-01", end="2025-01-01") -> list[tuple[str, Path]]:
    """(url, path) for every top-of-hour file of each MRMS product in [start, end)."""
    hours = pd.date_range(start, end, freq="h", inclusive="left")
    jobs = []
    for product in MRMS_PRODUCTS:
        for t in hours:
            url = MRMS_URL.format(product=product, t=t)
            jobs.append((url, raw_dir / "mrms" / product / f"{t:%Y}" / url.rsplit("/", 1)[1]))
    return jobs


def level2_jobs(raw_dir: Path, radars=LEVEL2_RADARS, days=LEVEL2_DAYS) -> list[tuple[str, Path]]:
    """(url, path) for every volume scan of each radar on each storm day."""
    import s3fs

    fs = s3fs.S3FileSystem(anon=True)
    jobs = []
    for day in days:
        d = dt.date.fromisoformat(day)
        for radar in radars:
            for key in fs.ls(f"{LEVEL2_BUCKET}/{d:%Y/%m/%d}/{radar}"):
                name = key.rsplit("/", 1)[1]
                if name.endswith("_MDM"):          # metadata-only files, not volume scans
                    continue
                rel = key.split("/", 1)[1]
                jobs.append((LEVEL2_URL.format(key=rel), raw_dir / "nexrad" / radar / day / name))
    return jobs


def run(jobs, workers=16, passes=4, label="") -> dict:
    """Download all jobs with a thread pool, retrying failures for a few passes."""
    counts = {"ok": 0, "skip": 0, "missing": 0, "error": 0}
    todo = list(jobs)
    for p in range(passes):
        failed = []
        with ThreadPoolExecutor(workers) as pool:
            futures = {pool.submit(_download, url, path): (url, path) for url, path in todo}
            for i, f in enumerate(as_completed(futures), 1):
                try:
                    counts[f.result()] += 1
                except Exception as e:  # noqa: BLE001 - log and retry next pass
                    failed.append(futures[f])
                    log.warning("error %s: %s", futures[f][0], e)
                if i % 2000 == 0:
                    log.info("%s pass %d: %d/%d %s", label, p + 1, i, len(todo), counts)
        if not failed:
            break
        todo = failed
    counts["error"] = len(failed)
    log.info("%s finished: %s", label, counts)
    return counts


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def level2_files(raw_dir: Path, radar: str, day: str) -> list[Path]:
    """Volume files for one radar on one downloaded storm day, in time order."""
    return sorted(p for p in (Path(raw_dir) / "nexrad" / radar / day).iterdir()
                  if p.name.startswith(radar) and not p.name.endswith(("_MDM", ".part")))


def volume_time(path: Path) -> pd.Timestamp:
    """Start time of a volume scan, from its file name (KMUX20211024_185215_V06)."""
    return pd.Timestamp(dt.datetime.strptime(Path(path).name[4:19], "%Y%m%d_%H%M%S"), tz="UTC")


def read_volume(path: Path, fields=None):
    """Read one Level II volume with Py-ART.

    Level II is the radar's own output: a series of compressed binary messages, one
    per ray, holding every field the radar measured at every range gate. Py-ART
    turns it into a ``Radar`` object with all sweeps stacked ray after ray.
    """
    import pyart

    return pyart.io.read_nexrad_archive(str(path), include_fields=fields)


def sweep_table(radar) -> pd.DataFrame:
    """One row per sweep: elevation angle, number of rays, Nyquist velocity and fields.

    Modern scan strategies use split cuts. The lowest angles are scanned twice: once
    with a long pulse spacing that sees far and measures reflectivity and the
    dual-polarization fields, and once with a short spacing that measures velocity.
    The table makes that visible, because sweep 0 and sweep 1 share an angle but
    carry different fields.
    """
    import numpy as np

    rows = []
    for s in range(radar.nsweeps):
        sl = radar.get_slice(s)
        present = [k for k, v in radar.fields.items() if np.ma.count(v["data"][sl]) > 0]
        rows.append({
            "sweep": s,
            "elevation_deg": round(float(radar.fixed_angle["data"][s]), 2),
            "rays": sl.stop - sl.start,
            "nyquist_m_s": round(float(radar.instrument_parameters["nyquist_velocity"]["data"][sl.start]), 1),
            "fields": ", ".join(sorted(SHORT.get(k, k) for k in present)),
        })
    return pd.DataFrame(rows).set_index("sweep")


# Short names for Py-ART's long field names.
SHORT = {
    "reflectivity": "Z", "velocity": "V", "spectrum_width": "SW",
    "differential_reflectivity": "ZDR", "cross_correlation_ratio": "rhoHV",
    "differential_phase": "PhiDP", "clutter_filter_power_removed": "CFP",
}


def beam_height(range_m, elevation_deg, radar_alt_m=0.0):
    """Height of the beam centre above sea level, in metres.

    Uses the standard 4/3 Earth radius model: the atmosphere bends the beam down a
    little, which is the same as a straight beam over an Earth a third larger. Even
    so the beam climbs away from the ground with range because the Earth curves away
    beneath it. (Doviak and Zrnic, *Doppler Radar and Weather Observations*, eq. 2.28b.)
    """
    import numpy as np

    ke_a = 4 / 3 * 6_371_000.0
    r = np.asarray(range_m, dtype=float)
    th = np.deg2rad(elevation_deg)
    return np.sqrt(r**2 + ke_a**2 + 2 * r * ke_a * np.sin(th)) - ke_a + radar_alt_m


def mrms_path(raw_dir: Path, product: str, time) -> Path:
    """Path of one downloaded MRMS hourly file. ``time`` is the end of the hour."""
    t = pd.Timestamp(time)
    return Path(raw_dir) / "mrms" / product / f"{t:%Y}" / f"MRMS_{product}_{t:%Y%m%d-%H}0000.grib2.gz"


def read_mrms(path: Path, box=None):
    """Read one gzipped MRMS GRIB2 file as rain in mm over the hour.

    Two traps. MRMS uses NOAA's own local GRIB2 parameter tables, so generic readers
    like cfgrib find the grid but call the variable "unknown" with unknown units. The
    product name comes from the file name instead and the units (mm) from the MRMS
    documentation. And -3 means "no radar coverage here", which must become missing
    and not be read as rain. Longitudes run 230-300 degrees east.

    ``box`` is (lat_min, lat_max, lon_min, lon_max) in degrees east, to crop early.
    """
    import gzip
    import os
    import tempfile

    import xarray as xr

    with gzip.open(path) as f:
        raw = f.read()
    tmp = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False)
    try:
        tmp.write(raw)
        tmp.close()
        ds = xr.open_dataset(tmp.name, engine="cfgrib", backend_kwargs={"indexpath": ""})
        da = ds[list(ds.data_vars)[0]]
        if box is not None:
            la0, la1, lo0, lo1 = box
            da = da.sel(latitude=slice(la1, la0), longitude=slice(lo0, lo1))
        da = da.load()
    finally:
        os.unlink(tmp.name)
    da = da.where(da >= 0)
    da.name = "precipitation_amount"
    da.attrs = {"units": "mm", "product": Path(path).name.split("_00.00")[0].removeprefix("MRMS_")}
    return da


def rain_rate(dbz, a=300.0, b=1.4, max_dbz=53.0):
    """Rain rate in mm/h from reflectivity in dBZ, through Z = a R^b.

    Reflectivity grows with the sixth power of drop size while rain rate grows with
    roughly the third and a half, so no single formula is right for every storm. The
    WSR-88D's long-standing default for ordinary rain is Z = 300 R^1.4 (Fulton et al.
    1998, *Weather and Forecasting* 13). Echo above 53 dBZ is capped there because it
    is usually hail, which would otherwise turn into absurd rain rates.
    """
    import numpy as np

    z = 10 ** (np.minimum(dbz, max_dbz) / 10)
    return (z / a) ** (1 / b)


def radar_rain_on_grid(files, start, end, lat, lon, sweep=0, rhohv_min=0.9, max_range_m=150_000.0):
    """Rain between ``start`` and ``end`` (UTC), accumulated onto a lat/lon grid (mm).

    Each volume's lowest sweep is turned into a rain rate per gate, gates whose
    correlation coefficient says "not rain" are dropped, and the rates are averaged
    into the grid cells (``lat`` descending, ``lon`` ascending, degrees east, regular
    spacing as in MRMS). Each volume's rate is assumed to hold until the next one
    starts, clipped to the window, so pass ``files`` covering a little before and after.
    """
    import numpy as np

    start, end = pd.Timestamp(start), pd.Timestamp(end)
    times = [volume_time(f) for f in files]
    total = np.zeros((len(lat), len(lon)))
    dlat, dlon = float(lat[0] - lat[1]), float(lon[1] - lon[0])
    for i, f in enumerate(files[:-1]):
        hours = (min(times[i + 1], end) - max(times[i], start)).total_seconds() / 3600
        if hours <= 0:
            continue
        r = read_volume(f, fields=["reflectivity", "cross_correlation_ratio"])
        sl = r.get_slice(sweep)
        z = r.fields["reflectivity"]["data"][sl]
        rho = r.fields["cross_correlation_ratio"]["data"][sl]
        glat, glon, _ = r.get_gate_lat_lon_alt(sweep)
        ok = (~np.ma.getmaskarray(z)) & (np.ma.filled(rho, 0) >= rhohv_min)
        ok &= (r.range["data"] <= max_range_m)[None, :]
        rate = np.where(ok, rain_rate(np.ma.filled(z, -99)), 0.0)
        iy = np.round((float(lat[0]) - glat) / dlat).astype(int)
        ix = np.round((glon % 360 - float(lon[0])) / dlon).astype(int)
        inside = (iy >= 0) & (iy < len(lat)) & (ix >= 0) & (ix < len(lon))
        s = np.zeros_like(total)
        n = np.zeros_like(total)
        np.add.at(s, (iy[inside], ix[inside]), rate[inside])
        np.add.at(n, (iy[inside], ix[inside]), 1)
        total += np.where(n > 0, s / np.maximum(n, 1), 0.0) * hours
    return total


def volume_counts(raw_dir: Path, radars=LEVEL2_RADARS, days=LEVEL2_DAYS) -> pd.DataFrame:
    """Number of downloaded volumes per radar, day and UTC hour (zeros included)."""
    rows = []
    for radar in radars:
        for day in days:
            hours = pd.Series([volume_time(f).hour for f in level2_files(raw_dir, radar, day)])
            counts = hours.value_counts().reindex(range(24), fill_value=0)
            rows += [{"radar": radar, "day": day, "hour": h, "volumes": int(c)} for h, c in counts.items()]
    return pd.DataFrame(rows)


if __name__ == "__main__":
    # python -m wxlab.radar data  -> Level II storm days first (small), then MRMS.
    data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    raw = data / "raw"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(data / "raw" / "radar_download.log")])
    run(level2_jobs(raw), workers=32, label="level2")
    run(mrms_jobs(raw), workers=32, label="mrms")
    log.info("DONE")
