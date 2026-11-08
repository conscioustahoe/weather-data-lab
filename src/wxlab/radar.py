"""Download raw radar data: MRMS hourly rainfall and NEXRAD Level II volumes.

Phase 2 adds radar as a second kind of sensor. This module only fetches the raw files
and keeps them as they are (the raw layer stays immutable). Reading and processing
them belongs to notebooks 02 and 10.

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
