"""Gridded data at scale: the California HRRR cube in Zarr, in two chunk layouts.

Notebook 03 kept every hour of HRRR's 2 m temperature and dew point over California for
2021–2024, one compressed NumPy file per day (24 hours × 2 variables × 458 × 420 cells).
Stacked, one variable is 35,064 × 458 × 420 float32 values, about 27 GB uncompressed.
That's small next to ERA5 but too big to load casually, so it is a fair stand-in for the
problems of bigger arrays.

**Zarr** stores an n-dimensional array as a grid of **chunks**, each compressed and kept
as its own object, plus small JSON metadata. A reader fetches only the chunks its slice
touches. So the chunk shape decides what every read costs:

- a **training layout** cuts the cube into map-shaped chunks (one day × the whole state):
  reading one hour's map touches one chunk, reading one place's history touches all of them;
- an **analysis layout** cuts it into time-series-shaped chunks (all hours × a small tile):
  the reverse.

Writing is done in idempotent units of one month into a store whose full shape is
declared up front (a "template"), so a run can be stopped and resumed. Converting one
layout into the other is **rechunking**. Doing it naively needs the whole array in
memory; dask and xarray-beam do it in steps under a memory limit.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

VARIABLES = {"air_temperature": 0, "dew_point_temperature": 1}


def day_file(grid_dir: Path, day) -> Path:
    d = pd.Timestamp(day)
    return Path(grid_dir) / f"{d:%Y}" / f"{d:%Y-%m-%d}.npz"


def coords(grid_dir: Path) -> dict:
    c = np.load(Path(grid_dir) / "coords.npz")
    return {"latitude": c["latitude"], "longitude": c["longitude"]}


def template(grid_dir: Path, start="2021-01-01", end="2025-01-01", variable="air_temperature"):
    """A lazy, all-NaN dataset with the cube's full shape, coordinates and CF attributes."""
    import dask.array as da
    import xarray as xr

    c = coords(grid_dir)
    time_ = pd.date_range(start, end, freq="h", inclusive="left")
    ny, nx = c["latitude"].shape
    data = da.full((len(time_), ny, nx), np.nan, dtype="float32", chunks=(24 * 31, ny, nx))
    ds = xr.Dataset({variable: (("time", "y", "x"), data)},
                    coords={"time": time_, "latitude": (("y", "x"), c["latitude"]),
                            "longitude": (("y", "x"), c["longitude"])})
    ds[variable].attrs = {"standard_name": variable, "units": "degree_Celsius",
                          "long_name": "HRRR 1-hour forecast, 2 m above ground"}
    ds.attrs = {"source": "NOAA HRRR via notebook 03", "grid": "HRRR 3 km Lambert conformal, California crop"}
    return ds


def month_block(grid_dir: Path, month, variable="air_temperature"):
    """One month of the cube as a NumPy array, NaN where a day file is missing."""
    m = pd.Timestamp(month)
    days = pd.date_range(m, m + pd.offsets.MonthBegin(1), freq="D", inclusive="left")
    blocks = []
    for d in days:
        f = day_file(grid_dir, d)
        if f.exists():
            blocks.append(np.load(f)["data"][:, VARIABLES[variable]])
        else:
            blocks.append(None)
    shape = next(b.shape for b in blocks if b is not None)
    return np.concatenate([b if b is not None else np.full(shape, np.nan, "float32") for b in blocks])


def write_training_layout(grid_dir: Path, store: Path, chunks=(24, None, None), variable="air_temperature",
                          start="2021-01-01", end="2025-01-01", done_log: Path | None = None) -> None:
    """Write the cube month by month into a Zarr store with map-shaped chunks.

    The template is written once (metadata only). Each month then fills its own region of
    the time axis. Months listed in ``done_log`` are skipped, so the job can be resumed.
    """
    import xarray as xr

    tpl = template(grid_dir, start, end, variable)
    ny, nx = tpl.sizes["y"], tpl.sizes["x"]
    enc = {variable: {"chunks": (chunks[0], chunks[1] or ny, chunks[2] or nx)}}
    store, done_log = Path(store), Path(done_log or Path(store).with_suffix(".done"))
    if not store.exists():
        tpl.to_zarr(store, compute=False, encoding=enc, zarr_format=3)
    done = set(done_log.read_text().split()) if done_log.exists() else set()
    for m in pd.date_range(start, end, freq="MS", inclusive="left"):
        key = f"{m:%Y-%m}"
        if key in done:
            continue
        i0 = tpl.indexes["time"].get_loc(m)
        block = month_block(grid_dir, m, variable)
        region = {"time": slice(i0, i0 + len(block))}
        xr.Dataset({variable: (("time", "y", "x"), block)}).to_zarr(store, region=region)
        with open(done_log, "a") as f:
            f.write(key + "\n")


def store_stats(store: Path) -> dict:
    """Number of chunk objects and total bytes on disk."""
    files = [p for p in Path(store).rglob("*") if p.is_file() and p.name not in ("zarr.json",)]
    return {"objects": len(files), "gb": sum(p.stat().st_size for p in files) / 1e9}


def timed(fn, repeat: int = 3) -> float:
    """Best-of-n wall time in seconds (first call can include metadata caching)."""
    best = float("inf")
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def chunks_touched(var, **sel) -> int:
    """How many chunks a selection reads, from the array's chunk grid (no I/O)."""
    n = 1
    for dim, size in zip(var.dims, var.encoding["chunks"]):
        s = sel.get(dim, slice(None))
        length = var.sizes[dim]
        if isinstance(s, int):
            n *= 1
        else:
            start, stop, _ = s.indices(length)
            n *= (stop - 1) // size - start // size + 1
    return n


def rechunk_via_intermediate(src: Path, intermediate: Path, dst: Path, inter_chunks: dict,
                             target_chunks: dict, variable="air_temperature", workers: int = 4) -> None:
    """Rechunk a Zarr store in two steps through an intermediate store on disk.

    Going straight from day-shaped chunks to column-shaped ones makes every output chunk
    depend on every input chunk along time, and dask's in-memory rechunk can run out of
    memory on an array this size. The rechunker library's answer is to pass through an
    intermediate layout whose chunks sit between the two (here: a month of hours by a
    64 × 64 tile), written to disk. Each step then only regroups a few chunks at a time.
    """
    import dask
    import xarray as xr

    def step(a, b, chunks):
        if Path(b).exists():
            return
        ds = xr.open_zarr(a)
        for v in ds.variables.values():
            v.encoding.pop("chunks", None)
            v.encoding.pop("preferred_chunks", None)
        sizes = {d: (ds.sizes[d] if c == -1 else c) for d, c in chunks.items()}
        out = ds.chunk(chunks)
        enc = {variable: {"chunks": tuple(sizes[d] for d in ds[variable].dims)}}
        with dask.config.set(scheduler="threads", num_workers=workers):
            out.to_zarr(b, zarr_format=3, encoding=enc)

    step(src, intermediate, inter_chunks)
    step(intermediate, dst, target_chunks)
