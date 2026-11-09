# Plan

## Goal

Own one dataset end to end: hourly observations from about 165 California weather stations, 2021–2024, taken from raw public archives to a standardized, training-ready dataset in which every value carries a quality verdict, and show with numbers how accurate those verdicts are and what they change for a model.

"Done" for the project means five things exist:

1. **The pipeline.** Raw files kept untouched, a standardized layer and a QA layer, built in small rerunnable units keyed by station and time.
2. **The dataset.** One row per station-hour: temperature, dew point, the HRRR reference values, each check's flag and a verdict. Nothing deleted.
3. **A monitoring history.** Checks run one month at a time, as a live pipeline would, with metrics recorded per run.
4. **A report card for the verdicts.** Recall per check on planted faults (by fault type and size), precision per check on a hand-checked sample of real flags, and three real problem stations explained with evidence.
5. **The model result.** Error of a model trained on clean, faulty, QA'd and randomly thinned data, with seed-to-seed spread, on held-out years and held-out stations.

## Phases

**Phase 1 (notebooks 01, 03, 04, 05, 09)** builds the whole project on stations and the HRRR model. It is complete when the five items above exist.

**Phase 2 (notebooks 02, 10, 06, 07, 08, 11)** adds radar as a second kind of sensor, deepens monitoring, rebuilds the pipeline with Ray, Spark and Beam, and works with gridded data at scale (xarray, Zarr, xarray-beam). It does not start until phase 1 is complete.

## How every notebook is written

The model is the *Learning WeatherBench 2* style: a story in which the reader learns each concept at the moment the project needs it. Concretely:

- Open with what this step of the project is, why it matters and what to look for in the output, in flowing prose.
- Explain every new term where it first appears, in plain words, and add it to `docs/glossary.md`.
- After each code section, say what just happened and what to notice.
- End with "What surprised me" and a few "Check yourself" questions with answers.
- Use real data for every plot. Keep saved outputs small enough for GitHub to render.
- Where a notebook introduces a tool, its plan below lists a short **Read first**: the few documentation pages that explain the tool's concepts and design, about 30–60 minutes. The notebook then uses the tool on project data. The docs give the vocabulary and the reasons behind the design; the notebook gives the practice.

---

## Phase 1

### 01 · Stations: read and explore

Weather stations are the most direct sensor there is, so they come first. This notebook reads hourly reports for the California stations from NOAA's Integrated Surface Database (ISD), decodes every field it uses from the ISD format document, and explores the result.

- **Data:** ISD (`noaa-global-hourly-pds` on AWS, anonymous) and NOAA's station list `isd-history.csv`. All of 2015–2024 is ingested; later notebooks use 2021–2024.
- **Fields:** air temperature, dew point, wind speed and one-hour precipitation, each with its scaling, missing-value code and NOAA quality code.
- **Key rule:** several report types share each hour (routine METAR at about minute 51, special reports when the weather changes, synoptic reports on the hour, daily and monthly summaries). The standardized table keeps one report per station-hour by a written rule: drop summaries, assign each report to the nearest hour, prefer the routine report closest to the hour. The chosen report's time and type are kept for auditing.
- **Code:** `wxlab.stations`.
- **Output:** one Parquet file per station-year, one row per station-hour, UTC, °C and m/s and mm.
- **Done when:** all stations are standardized, the notebook explains every field, shows coverage, the daily and seasonal cycles at coast, desert and mountain stations, previews two quality questions (dew point above temperature; missing data in storm hours), and records what was surprising.

### 03 · Matching to a reference

To judge a station you need something independent to compare it with. The reference is HRRR, NOAA's hourly 3 km weather model for the US.

- **Which HRRR field:** the 1-hour forecast valid at each hour, not the analysis. HRRR's analysis is built partly from these same airport reports (data assimilation), so it is not independent of the stations and would hide station faults. A forecast made an hour earlier has not seen the observation. (The previous hour's observations did shape its starting point, so independence is improved, not perfect; this is stated in the notebook.)
- **Period:** 2021–2024, every hour. HRRR's last major version change was in December 2020; earlier years mix model versions, and a version change looks like a step fault at every station at once.
- **Matching:** take the HRRR value at each station's location, correct temperature for the height difference between the station and the model's terrain (about 6.5 °C per km), and store station value, HRRR value and difference per station-hour. Whole HRRR grids are never stored, only values at station points.
- **Also kept:** the California crop of every hourly HRRR field (temperature and dew point), one compressed array per day, about 20 GB in total. It costs no extra download and is the raw material for notebook 11.
- **Read first:** xarray's "Overview: Why xarray?", "Data structures" and "Indexing and selecting data" pages; Herbie's "Getting started".
- **Code:** `wxlab.reference`.
- **Done when:** the matched table exists; for most stations the difference is small and steady; the stations where it is not are explained (for example the coastal marine layer, which HRRR handles poorly).

### 04 · How data goes wrong

Faults are planted into a clean copy of the data so that every check can be scored against a known answer.

- **Clean baseline first:** keep stations that are nearly complete and whose difference from HRRR is steady over the four years. The real data is not perfectly clean, so this baseline reduces, but does not remove, unknown real faults.
- **Fault types:** step (instrument swap), slow drift (aging sensor), constant bias (poor siting), stuck sensor, time offset, and gaps that cluster in storm hours (defined from the wind and precipitation fields).
- **Realistic setup:** faults go into 20–30% of stations, the rest stay untouched, so checks can raise false alarms. Each fault type is planted at a range of sizes, so results become "detection rate against fault size" rather than one number that depends on an arbitrary choice.
- **Train/test separation for notebook 09:** faults are planted only in 2021–2023. 2024 stays clean.
- **Code:** `wxlab.faults` (one function per fault type, each returning the faulty series and a ground-truth label).
- **Done when:** the labeled dataset exists, and each fault type has a plot (raw and as a difference from HRRR) and a sentence on why it would mislead a model.

### 05 · QA checks and metrics

The checks, the verdicts, and how good they are.

- **Check layers, cheap to subtle:** structural (parses, units, coordinates), physical (possible values; dew point not above temperature), climatological (plausible for this place and season), stuck sensor, spatial (agreement with neighboring stations), reference (difference from HRRR), and temporal (changepoints and drift in the monthly station-minus-HRRR series).
- **A second witness:** a station disagreeing with HRRR alone is "suspect", not "quarantine". Quarantine needs agreement from a second source, usually neighboring stations, because HRRR has its own weaknesses.
- **Verdicts:** pass, suspect, quarantine, per station-hour, with each check's flag stored as a column.
- **Monitoring:** the checks run one month at a time, as a scheduled job would, and each run records coverage, flag rates and verdict counts.
- **Scoring:** a detection counts if it names the right station and lands within a set tolerance of the true fault start, scored per station-month. Recall per check and fault type, against fault size. Precision on injected data, and separately on a hand-checked sample of real flags.
- **Expected limits, stated up front:** a constant bias that has always been there looks the same as a real local effect and is mostly undetectable without metadata or neighbors.
- **Real data:** run the checks on the real 2021–2024 data and investigate the three most suspicious stations, using the data and NOAA's station metadata.
- **Code:** `wxlab.qa`.
- **Done when:** the verdict table, the monitoring history, the precision and recall tables, and three explained real stations exist.

### 09 · Does clean data help the model?

A controlled experiment. A small model (gradient boosting) predicts station temperature from HRRR fields and simple time and place features. This is a form of what forecasters call post-processing: learning corrections to a weather model from past errors.

- **Breaks first:** date each break episode, cut stations into segments, measure each segment against the newest one (homogenization). Score it on planted faults over 20 seeds and look at the real stations it flags. Code in `wxlab.qa`.
- **Eight training sets, everything else fixed:** clean; faulty; QA'd (quarantine removed); QA'd strict (suspect removed too); random thinning (as many hours as QA'd, at random); oracle (exactly the planted fault hours removed); QA'd plus dropping bad segments; QA'd plus adjusting bad segments. Code in `wxlab.experiment`.
- **Split:** train on 2021–2023 with fixed hyperparameters, test on 2024 minus the hours real-data QA quarantined, at training stations, faulty stations, held-out stations and storm hours.
- **Metrics:** RMSE and bias, five seeds, broken down by fault type per station.
- **Done when:** a results table with seed-to-seed spread shows which faults hurt this model most, whether QA closes the gap, whether QA beats random removal, and whether repairing breaks beats dropping them.

---

## Phase 2

### 02 · Radar: read and explore

Raw NEXRAD Level II volumes from KMUX (Bay Area) and KVTX (Los Angeles) on five storm days, read with Py-ART: the polar geometry and split cuts, reflectivity, radial velocity and the correlation coefficient. Non-weather echo found with ρHV, the beam's height with range, one hour of radar rain from a Z–R formula set beside MRMS with and without gauges, and volume coverage per hour including KMUX's 18-hour archive gap on 2023-01-09. Done (terrain blockage and biological echo were tried and left out because the signal on these storm days was not clear).

### 10 · Radar vs gauges

MRMS radar-only rain at the 1 km cell above every station with a rain gauge, every hour of 2021–2024, decoded straight from 70,000 GRIB2 files. Scored by detection (POD, FAR, CSI), totals by height of the lowest radar beam, and SEEPS on daily totals where it is defined. Radar as a witness against gauges: hours where a gauge reports a downpour and radar sees almost nothing. Done.

### 06 · Monitoring and verdicts

The monthly QA job replayed on real data: how often verdicts change in hindsight and why, control charts on each check's firing rate, and hand reviews stored as overrides beside the automatic verdicts. Done.

### 07 · Pipelines with Ray

The phase 1 ingestion as Ray tasks over station-year units, with a manifest that fingerprints inputs and code, a backfill and an incremental run sharing one plan, actors for connection reuse, Ray Data for a dataset-wide step, and Ray's documented strengths and limits. Done.

### 08 · Spark and Beam

The neighbour check as a Spark join (and the shuffles it needs), window functions and a pandas UDF; the same unit function in Beam; event time, watermarks and a late reading re-firing a daily window; and a comparison of Ray, Spark and Beam. Done.

### 11 · Gridded data at scale

The station table is small. Gridded data is not, and at scale the way an array is cut into chunks decides what every read costs. This notebook uses the California HRRR crop kept by notebook 03 (every hour of 2021–2024 on the 3 km grid) as a real, mid-sized array.

- **xarray:** build the cube as a labelled dataset (time, y, x) with coordinates and CF attributes.
- **Zarr:** write it in a *training layout* (chunks shaped like maps: a day of hours by the whole of California), with compression; look at the chunk files on disk.
- **Rechunking:** convert it to an *analysis layout* (chunks shaped like long time series: all hours by small tiles) with dask and with xarray-beam, within a memory limit, through an intermediate layout. (The rechunker library is not used: it supports only Zarr format 2.)
- **Measure:** time and bytes read for "one map at one hour" and "four years at one point" in each layout.
- **xarray-beam:** compute an hourly climatology of the cube with Beam on the local runner, and compare with the same computation in plain xarray.
- **Read first:** Zarr's "Quickstart" and the pages on arrays, chunks, compression and sharding; the xarray-beam README and "Why xarray-beam".
- **Done when:** the two layouts exist, the read-cost table shows why the layout must match the reader, and the notebook explains how the same steps would work for a petabyte dataset. Done: the rechunk goes through an intermediate layout on disk to stay under about 2 GB of memory.

---

## Project page

Written last after notebook 09. One page in the style of the *Learning WeatherBench 2* guide that's quick to read and links to the notebooks in this repository wherever a reader wants the detail. It covers:

- The project in a few lines: one dataset taken from raw archives to a training-ready table with a verdict on every station-hour.
- The findings, led by the real ones: broken sensors that NOAA's checks passed, a NOAA flag that looks wrong, and the subtleties that make weather data hard (airport reporting minutes, whole-°F precision, coastal grid boxes, marine-layer inversions and cold-air pools).
- The results: recall on planted faults by type and size, precision on real flags, and the clean vs faulty vs QA'd vs thinned model comparison.
- The lessons, including the ingestion ones (connection reuse, idempotent units, keeping raw data immutable).
- How the design would scale to petabytes. This part is described and not built, and it needs its own research before writing. The questions it answers:
  - What kinds of weather and climate data exist (station time series, radar volumes, satellite swaths, model and reanalysis grids, ensembles) and how big each one is. WeatherBench 2 describes several of them.
  - How each kind is stored in production: file formats (GRIB, NetCDF, HDF5, Zarr, Parquet), chunk layout, compression and object storage.
  - How to read them fast: reading only the chunks you need, matching the chunk layout to the reader, indexes and catalogues.
  - How to work with petabytes from a notebook without loading them into memory: lazy arrays, dask and distributed compute next to the data.
  - How ingestion, QA and monitoring run on a cluster and on a live feed, and what changes at that scale.
- The page lives on the owner's website as its own page (for example `/weather-data-lab`) and links back to this repository.
