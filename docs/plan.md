# Plan

The lab follows one dataset's journey from sensor to model. Each notebook is one step of that journey, and each one builds on the outputs of the ones before it. This file describes what every notebook covers, what data it uses and what "done" looks like.

The first milestone is a complete small project made of notebooks 01, 03, 04, 05 and 09: ingest station data, compare it with a reference, inject and catch faults, and measure the effect on a model. The other notebooks deepen it.

---

## 00 · The map

A short orientation. It describes the chain from a physical sensor to a trained model (sensor, source, decode, standardize, quality checks, training data, model, and the experiment that decides whether the data helped) and shows where each notebook sits on that chain. No data.

**Done when:** a reader who knows ML but not weather understands why each step exists.

## 01 · Stations: read and explore

Weather stations are the most direct sensors there are, so they are the right place to start. This notebook reads hourly observations for a set of California stations from NOAA's Integrated Surface Database (ISD) on AWS, guided by the ISD format document. It explains every field used: temperature stored in tenths of a degree Celsius, `+9999` meaning missing, and the quality code attached to every value. It then looks at the data: plots for a few stations, gaps, the daily and seasonal cycles, and the station list with locations, elevations and active dates.

- **Data:** NOAA ISD (`noaa-global-hourly-pds` on AWS, anonymous); NOAA's station list `isd-history.csv`. Optionally, US airport (ASOS) data from the Iowa Environmental Mesonet.
- **Code to write:** `wxlab.stations` (station list filtering, download, parsing into a standardized table).
- **Output:** standardized Parquet, one row per station and hour, with UTC time, temperature in °C and the original quality code.
- **Done when:** about 200 California stations with long records are parsed for 2015–2024, and the notebook explains what each field means and what surprised you.
- **References:** [ISD on AWS](https://registry.opendata.aws/noaa-isd), [ISD format document](https://www.ncei.noaa.gov/data/global-hourly/doc/isd-format-document.pdf).

## 02 · Radar: read and explore

Radar is the opposite of a station: indirect, three-dimensional and full of artifacts. This notebook opens raw NEXRAD Level II volumes for a California radar with Py-ART, explains reflectivity, radial velocity and the dual-polarization fields, and shows the polar geometry of a volume scan. It then looks for artifacts on purpose: ground clutter near the radar, echoes from birds and insects at dusk, and wedges blocked by terrain. It uses the correlation coefficient field to separate weather from non-weather echoes, then compares a raw volume with MRMS, NOAA's quality-controlled national mosaic, to see what that processing removed.

- **Data:** NEXRAD Level II (`noaa-nexrad-level2` on AWS, anonymous); MRMS (on AWS, anonymous).
- **Code to write:** `wxlab.radar` (listing, downloading and reading volumes; simple masks).
- **Done when:** you can explain each artifact from a plot of real data, and what MRMS does differently from the raw volume.
- **References:** [NEXRAD on AWS](https://registry.opendata.aws/noaa-nexrad), [MRMS on AWS](https://registry.opendata.aws/noaa-mrms-pds), [Py-ART](https://arm-doe.github.io/pyart/).

## 03 · Matching to a reference

To check a station you need something independent to compare it with. This notebook pulls HRRR 2 m temperature for each station's location and hour, interpolates it to the station, and corrects for the height difference between the station and the model's terrain (about 6.5 °C per kilometer). It explains representativeness error, which is why a point measurement and a 3 km grid cell disagree even when both are right, and why the checks react to changes in the difference rather than to the difference itself.

- **Data:** HRRR analyses on AWS through Herbie; the station table from notebook 01.
- **Code to write:** `wxlab.reference` (fetching HRRR, matching by time and place, height correction).
- **Output:** a table of station value, reference value and difference, per station and hour.
- **Done when:** for most stations the difference is small and stable, and you can explain the stations where it isn't.
- **References:** [HRRR on AWS](https://registry.opendata.aws/noaa-hrrr-pds), [Herbie](https://herbie.readthedocs.io).

## 04 · How data goes wrong

Before building checks, learn what the problems look like. This notebook takes clean station data and injects known faults: a step change (an instrument swap), a slow drift (an aging sensor), a constant bias (poor siting), a stuck sensor, a local-time offset and gaps that cluster during extreme weather. Each fault is plotted next to the clean series, both raw and as a difference from the reference, so you can see why subtracting a reference makes faults visible that are invisible in the raw data.

- **Code to write:** `wxlab.faults` (one function per fault type, each returning the faulty series and a ground-truth label).
- **Output:** a labeled dataset in which the faulty stations, fault types and start times are known.
- **Done when:** each fault type has a plot and a sentence explaining why it would hurt a model.

## 05 · QA checks and metrics

This notebook builds the checks, in layers from cheap to subtle: structural (does it parse, are units and coordinates right), physical (is the value possible), climatological (is it plausible for this place and season), spatial (does it agree with neighboring stations), reference (how it compares with HRRR) and temporal (changepoint detection on the monthly station-minus-reference series, and drift tests). It defines the three quality metrics: coverage, correctness and consistency. It then scores every check against the injected faults with precision and recall, and finally runs the checks on the real, uninjected data and investigates the three most suspicious stations.

- **Code to write:** `wxlab.qa` (one module per check layer; metrics; a report card).
- **Done when:** you have a precision and recall table per check on the injected data, and three real stations with an evidence-backed explanation of what probably happened to each.
- **References:** [ruptures](https://centre-borelli.github.io/ruptures-docs/) for changepoint detection.

## 06 · Monitoring and verdicts

A real pipeline sees data arrive over time. This notebook feeds the data through one month at a time and runs the checks on each new month, the way a scheduled job would. Every run writes its metrics to a table, and the history of that table is the monitoring view. Each check's result becomes a verdict (pass, warn, quarantine or block) with a defined action. Flags are stored beside the data rather than deleting it. A hand-labeled sample of flags measures how often each check is right, which is what owning a check's verdicts means in practice.

- **Code to write:** `wxlab.monitor` (incremental runs, metrics history, verdict rules, alerting on changes).
- **Done when:** you can show a metric changing over time, the alert it triggered and each check's measured precision.

## 07 · Pipelines with Ray

The ingestion from notebook 01, rebuilt as a pipeline: an immutable raw layer with a manifest, work split into idempotent units keyed by station and year, and Ray running the units in parallel. It shows a backfill and an incremental run using the same code, and explains how the design would scale to a cluster and to a live feed (event time, watermarks, late and corrected data).

- **Do first:** the official [Ray Data getting started](https://docs.ray.io/en/latest/data/data.html) and Ray Core tasks tutorials.
- **Done when:** rerunning any unit gives identical output, and a failed unit can be retried safely.

## 08 · Spark and Beam

The same station job in PySpark (deduplicate, compute completeness per station, write partitioned Parquet), then a first Apache Beam pipeline on the local runner and a look at xarray-beam for gridded data. It ends with a comparison of Ray, Spark, Beam and Dask: their mental models, what each is best at for weather data and their tradeoffs.

- **Do first:** [Spark Quick Start](https://spark.apache.org/docs/latest/quick-start.html) and [Tour of Beam](https://tour.beam.apache.org/).
- **Done when:** you can explain in two minutes which framework you would pick for stations, radar and gridded data, and why.

## 09 · Does clean data help the model?

A controlled experiment. A small model (gradient boosting) predicts station temperature from the surrounding HRRR fields. It is trained three ways: on clean data, on data with the injected faults, and on the faulty data after QA has quarantined what it caught. Everything else stays fixed. It is evaluated on held-out years with several random seeds, and results are broken down by fault type and by station. The gap between clean and faulty shows how much each fault hurts the model; the gap between faulty and QA'd shows how much the checks recover.

- **Done when:** you have a results table with seed-to-seed spread, and you can say which fault types matter most for this model and whether QA closes the gap.

## 10 · Radar vs gauges (stretch)

Consistency across sources. This notebook compares radar-estimated rainfall (MRMS) with rain gauges at stations, over the same hours. It looks at how the agreement changes with distance from the radar, terrain and rain type, and turns the comparison into a consistency metric that could run every day.

- **Done when:** you can show where radar and gauges disagree and explain why.
