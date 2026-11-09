"""Framework-neutral pieces of the ingestion pipeline: units of work and a manifest.

Notebooks 07 and 08 run the same station job on Ray, Spark and Beam. What stays the same
across frameworks lives here:

- A **unit** is the smallest piece of work that can be redone on its own. For stations it
  is one station-year: one raw file in, one standardized file out.
- The **manifest** is a table with one row per unit that was attempted: its key, a
  fingerprint of its input, the code version, the outcome and where the output went. It
  answers "what's done?" without listing the output directory, and "what must be
  redone?" when the input or the code changes.
- **Planning** compares the units we want with the manifest. A backfill wants every
  station-year; an incremental run wants this month's. Both go through the same plan
  and the same unit function, which is what keeps the two paths from drifting apart.

The unit function itself is a plain Python function with no framework in it. Each
framework only decides how to schedule it.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from wxlab import stations as st

# Bump when the standardization logic changes, so every unit is redone.
CODE_VERSION = "stations-v1"


@dataclass(frozen=True)
class Unit:
    station_id: str
    year: int

    @property
    def key(self) -> str:
        return f"isd/{self.station_id}/{self.year}"


def fingerprint(path: Path) -> str | None:
    """Cheap identity of an input file: size and modification time, hashed.

    A content hash is stronger but means reading every byte. For immutable raw files
    written once by the downloader, size and mtime are enough to notice a re-download.
    """
    p = Path(path)
    if not p.exists():
        return None
    s = p.stat()
    return hashlib.sha1(f"{s.st_size}:{int(s.st_mtime)}".encode()).hexdigest()[:16]


def units_for(station_ids, years) -> list[Unit]:
    return [Unit(str(s), int(y)) for s in station_ids for y in years]


def load_manifest(path: Path) -> pd.DataFrame:
    cols = ["key", "station_id", "year", "input_fp", "code_version", "status", "rows", "seconds", "output", "error", "finished_at"]
    p = Path(path)
    return pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=cols)


def plan(units: list[Unit], manifest: pd.DataFrame, raw_dir: Path) -> list[Unit]:
    """Units that still need running: never done, failed, input changed or code changed."""
    done = manifest[manifest.status == "ok"].drop_duplicates("key", keep="last").set_index("key")
    todo = []
    for u in units:
        fp = fingerprint(st.raw_path(raw_dir, u.station_id, u.year))
        if fp is None:
            continue                                   # no raw file: nothing to do (yet)
        if u.key in done.index:
            row = done.loc[u.key]
            if row.input_fp == fp and row.code_version == CODE_VERSION:
                continue
        todo.append(u)
    return todo


def run_unit(u: Unit, raw_dir: Path, std_dir: Path) -> dict:
    """Standardize one station-year and return its manifest row. Never raises.

    Writes atomically (``standardize_station_year`` writes ``.part`` then renames), so a
    killed worker leaves either the old output or the new one, never half a file.
    """
    t0 = time.time()
    row = {**asdict(u), "key": u.key, "input_fp": fingerprint(st.raw_path(raw_dir, u.station_id, u.year)),
           "code_version": CODE_VERSION, "rows": 0, "output": None, "error": None}
    try:
        out = st.standardize_station_year(u.station_id, u.year, raw_dir, std_dir, overwrite=True)
        row["status"] = "ok" if out is not None else "missing"
        if out is not None:
            row["output"] = str(out)
            row["rows"] = len(pd.read_parquet(out, columns=["time"]))
    except Exception as e:  # noqa: BLE001 - recorded in the manifest, retried next run
        row["status"], row["error"] = "error", f"{type(e).__name__}: {e}"
    row["seconds"] = round(time.time() - t0, 3)
    row["finished_at"] = pd.Timestamp.now(tz="UTC")
    return row


def record(manifest_path: Path, rows: list[dict]) -> pd.DataFrame:
    """Append results to the manifest and write it atomically."""
    m = pd.concat([load_manifest(manifest_path), pd.DataFrame(rows)], ignore_index=True)
    p = Path(manifest_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    m.to_parquet(tmp, index=False)
    tmp.rename(p)
    return m
