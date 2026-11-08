"""Synthetic weather data for the tool primers.

The primers teach tools (xarray, Zarr, rechunker, Beam, xarray-beam, Spark), not
weather, so they use generated data that behaves like the real thing in shape, size
and structure, and can be made as big or as broken as a lesson needs. Three kinds:

- :func:`make_cube`: an hourly gridded temperature field over a western-US-sized box,
  with a seasonal cycle, a daily cycle that follows the sun across longitudes, colder
  air over higher ground, travelling warm and cold waves, and noise. Shaped like a
  model or reanalysis field (time, latitude, longitude).
- :func:`make_station_table`: point observations sampled from the cube at random
  locations, with a few planted faults (a stuck sensor, a step, gaps), shaped like a
  station archive table.
- :func:`make_event_stream`: those observations as a stream of events that arrive out
  of order and sometimes late, for streaming lessons.

Nothing here is real data; every function takes a seed so results are reproducible.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import xarray as xr


def make_cube(start="2023-01-01", days=730, ny=80, nx=120, seed=0,
              lat=(30.0, 42.0), lon=(-125.0, -110.0)) -> xr.Dataset:
    """Hourly synthetic 2 m air temperature (°C) on a regular latitude-longitude grid.

    Default size: 17,520 hours x 80 x 120 points, about 670 MB as float32.
    """
    rng = np.random.default_rng(seed)
    time = pd.date_range(start, periods=days * 24, freq="h")
    lats = np.linspace(lat[1], lat[0], ny)          # north to south, as many datasets store it
    lons = np.linspace(lon[0], lon[1], nx)
    LAT, LON = np.meshgrid(lats, lons, indexing="ij")

    # Terrain: a north-south ridge plus bumps, 0-3000 m.
    ridge = 2500 * np.exp(-((LON + 119.5) / 1.2) ** 2) * (0.6 + 0.4 * np.sin(LAT / 2.0))
    bumps = 600 * np.sin(LAT * 1.7) * np.cos(LON * 1.3)
    elevation = np.clip(ridge + bumps + 200, 0, None).astype("float32")

    base = 22 - 0.6 * (LAT - lat[0]) - 6.5e-3 * elevation              # warmer south, colder high
    t = np.arange(len(time))
    doy = time.dayofyear.values
    hour_utc = time.hour.values
    seasonal = -9 * np.cos(2 * np.pi * (doy - 15) / 365.25)            # coldest mid-January

    out = np.empty((len(time), ny, nx), dtype="float32")
    phase = rng.uniform(0, 2 * np.pi, 3)
    for k in range(0, len(time), 24 * 30):                              # build a month at a time
        sl = slice(k, min(k + 24 * 30, len(time)))
        tt = t[sl][:, None, None]
        solar_hour = (hour_utc[sl][:, None, None] + LON[None] / 15.0) % 24  # local solar time
        diurnal = 6 * np.cos(2 * np.pi * (solar_hour - 15) / 24)          # warmest mid-afternoon
        waves = sum(3.0 / (i + 1) * np.sin(2 * np.pi * ((LON[None] - lon[0]) / (12 / (i + 1))
                                                         - tt / (24 * (4 + 2 * i))) + phase[i])
                    for i in range(3))                                   # weather systems moving east
        noise = rng.normal(0, 0.6, (sl.stop - sl.start, ny, nx))
        out[sl] = base[None] + seasonal[sl][:, None, None] + diurnal + waves + noise

    ds = xr.Dataset(
        {"air_temperature": (("time", "latitude", "longitude"), out,
                             {"units": "degC", "standard_name": "air_temperature",
                              "long_name": "synthetic 2 m air temperature"})},
        coords={
            "time": time,
            "latitude": ("latitude", lats, {"units": "degrees_north"}),
            "longitude": ("longitude", lons, {"units": "degrees_east"}),
            "elevation": (("latitude", "longitude"), elevation, {"units": "m"}),
        },
        attrs={"title": "Synthetic hourly temperature cube", "source": "wxlab.synthetic.make_cube"},
    )
    return ds


def make_station_table(cube: xr.Dataset, n=300, seed=1) -> pd.DataFrame:
    """Hourly observations at ``n`` random points, with a few planted faults.

    Columns: station_id, time, air_temperature, latitude, longitude, elevation_m.
    Planted faults, recorded in ``df.attrs["faults"]``: one stuck sensor (repeats a
    value for 3 days), one step (+2.5 °C from a date on) and random gaps.
    """
    rng = np.random.default_rng(seed)
    lat = rng.uniform(float(cube.latitude.min()), float(cube.latitude.max()), n)
    lon = rng.uniform(float(cube.longitude.min()), float(cube.longitude.max()), n)
    ids = [f"ST{i:04d}" for i in range(n)]
    pts = cube.air_temperature.sel(latitude=xr.DataArray(lat, dims="station"),
                                   longitude=xr.DataArray(lon, dims="station"), method="nearest")
    elev = cube.elevation.sel(latitude=xr.DataArray(lat, dims="station"),
                              longitude=xr.DataArray(lon, dims="station"), method="nearest").values
    values = pts.values + rng.normal(0, 0.3, pts.shape).astype("float32")  # sensor noise
    times = cube.time.values
    df = pd.DataFrame({
        "station_id": np.repeat(ids, len(times)),
        "time": np.tile(times.astype("datetime64[us]"), n),  # microseconds: what Spark and Parquet expect
        "air_temperature": values.T.ravel(),
        "latitude": np.repeat(lat, len(times)),
        "longitude": np.repeat(lon, len(times)),
        "elevation_m": np.repeat(elev, len(times)),
    })
    faults = []
    stuck, step = ids[7], ids[42]
    t0 = pd.Timestamp(times[len(times) // 3])
    m = (df.station_id == stuck) & (df.time >= t0) & (df.time < t0 + pd.Timedelta(days=3))
    df.loc[m, "air_temperature"] = df.loc[m, "air_temperature"].iloc[0]
    faults.append({"station_id": stuck, "fault": "stuck", "start": str(t0), "end": str(t0 + pd.Timedelta(days=3))})
    t1 = pd.Timestamp(times[len(times) // 2])
    m = (df.station_id == step) & (df.time >= t1)
    df.loc[m, "air_temperature"] += 2.5
    faults.append({"station_id": step, "fault": "step +2.5", "start": str(t1), "end": None})
    df = df.drop(index=df.sample(frac=0.02, random_state=seed).index).reset_index(drop=True)  # gaps
    df.attrs["faults"] = faults
    return df


def make_event_stream(stations: pd.DataFrame, hours=6, n_stations=5, seed=2,
                      late_share=0.1) -> pd.DataFrame:
    """A few hours of observations as events with an event time and an arrival time.

    Most events arrive within a couple of minutes of being measured; ``late_share`` of
    them arrive 20-90 minutes late, as happens when a station's link drops and it
    resends its backlog. Rows are sorted by arrival time, the order a pipeline sees.
    """
    rng = np.random.default_rng(seed)
    ids = stations.station_id.drop_duplicates().iloc[:n_stations]
    t0 = stations.time.min()
    s = stations[stations.station_id.isin(ids) & (stations.time < t0 + pd.Timedelta(hours=hours))].copy()
    # Sub-hourly measurements: spread each hourly value over 6 readings, 10 minutes apart.
    s = s.loc[s.index.repeat(6)].copy()
    s["event_time"] = s.time + pd.to_timedelta(np.tile(np.arange(6) * 10, len(s) // 6), unit="min")
    delay = rng.exponential(60, len(s))                                   # seconds
    late = rng.random(len(s)) < late_share
    delay[late] = rng.uniform(20 * 60, 90 * 60, late.sum())
    s["arrival_time"] = s.event_time + pd.to_timedelta(delay, unit="s")
    s["late"] = late
    for col in ("event_time", "arrival_time"):  # explicit UTC, microsecond precision (what Spark expects)
        s[col] = s[col].astype("datetime64[us]").dt.tz_localize("UTC")
    return s[["station_id", "event_time", "arrival_time", "air_temperature", "late"]].sort_values(
        "arrival_time").reset_index(drop=True)
