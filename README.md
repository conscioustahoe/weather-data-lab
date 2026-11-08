# weather-data-lab

A hands-on lab for learning how to ingest, standardize and quality-check physical sensor data so that it can be trusted as training data for machine learning models.

Most machine learning weather models train on ERA5, a reanalysis in which a weather center has already combined, checked and cleaned millions of observations. This lab works one step earlier, with the raw observations themselves, and asks the questions someone has to answer before that data reaches a model. How do you read each source correctly? How do you put different sources on a common footing? How do you notice when a sensor is quietly wrong? And how do you show that cleaning the data actually makes a model better?

## The project

The lab is built around one project:

> **Own one dataset end to end.** Take hourly observations from about 165 California weather stations (2021–2024) from raw public archives to a standardized, training-ready dataset in which every value carries a quality verdict. Measure how often those verdicts are right, and show what they change for a model.

**What "quality" means here.** A value is good if a model would learn the right thing from it. Every check is a written rule, and each station-hour gets a verdict from those rules:

- **pass**: no check objects;
- **suspect**: unusual but plausible; kept and used for training by default;
- **quarantine**: evidence that the value is wrong; kept in the dataset but excluded from training by default.

Values are never deleted. Each check's flag is stored as its own column beside the data, so a user can choose a different strictness level.

**How the verdicts are tested.** A verdict is a claim, and it can be wrong in two ways. Recall (how many real problems are caught) is measured on faults planted into clean data, where the answer is known. Precision (how many flags are real problems) is measured by checking a sample of flags on real data by hand. Finally, a controlled model experiment tests whether quarantining what the checks flag actually helps a model more than removing the same amount of data at random.

## Two phases

**Phase 1: stations and a weather model.** Two public sources in two formats: NOAA station reports (CSV) and the HRRR weather model (GRIB2), matched station by station and hour by hour. Variables: air temperature and dew point, with wind and precipitation as supporting data.

**Phase 2: radar and scale.** Radar adds a new kind of sensor, a new format and a new variable (precipitation), which makes it possible to check consistency between radar and station rain gauges. The same pipeline is then rebuilt with Ray, Spark and Beam, and four years of gridded HRRR data over California are stored and reorganised with Zarr and xarray-beam.

## The notebooks

Each notebook is one step of the project and one chapter of learning. It tells a story, explains every new term where it first appears, and ends with what was learned. Read and run them in order.

| # | Notebook | Phase | What it covers |
|---|---|---|---|
| 00 | [The map](notebooks/00-the-map.ipynb) | 1 | The path from sensor to model, and where each notebook sits on it |
| 01 | [Stations: read and explore](notebooks/01-stations-read-and-explore.ipynb) | 1 | Station archives, the ISD format, report types, quality codes, gaps |
| 03 | [Matching to a reference](notebooks/03-matching-to-a-reference.ipynb) | 1 | The HRRR model, GRIB2, forecast vs analysis, height correction, representativeness |
| 04 | [How data goes wrong](notebooks/04-how-data-goes-wrong.ipynb) | 1 | Planting known faults: step, drift, bias, stuck sensor, time offset, storm gaps |
| 05 | [QA checks and metrics](notebooks/05-qa-checks-and-metrics.ipynb) | 1 | Layered checks, verdicts, monthly monitoring, precision and recall, real problem stations |
| 09 | [Does clean data help the model?](notebooks/09-does-clean-data-help-the-model.ipynb) | 1 | Clean vs faulty vs QA'd vs randomly thinned training data |
| 02 | [Radar: read and explore](notebooks/02-radar-read-and-explore.ipynb) | 2 | Raw radar volumes, the national mosaic, radar artifacts |
| 10 | [Radar vs gauges](notebooks/10-radar-vs-gauges.ipynb) | 2 | Consistency across sources |
| 06 | [Monitoring and verdicts](notebooks/06-monitoring-and-verdicts.ipynb) | 2 | Long-running monitoring, alerting, verdict history |
| 07 | [Pipelines with Ray](notebooks/07-pipelines-with-ray.ipynb) | 2 | The same ingestion as a parallel, idempotent Ray pipeline |
| 08 | [Spark and Beam](notebooks/08-spark-and-beam.ipynb) | 2 | The same job in Spark, a first Beam pipeline, and how the frameworks compare |
| 11 | Gridded data at scale | 2 | xarray, Zarr and xarray-beam on four years of HRRR over California |

Two **tool primers** teach the tools on synthetic data, so they can be read before or alongside the project notebooks:

| # | Primer | What it covers |
|---|---|---|
| P1 | [Arrays](notebooks/P1-arrays-primer.ipynb) | xarray (labels, selection, groupby, climatology, weighting), dask, Zarr (chunks, compression, append and region writes, missing chunks), chunk layout and rechunking |
| P2 | [Pipelines](notebooks/P2-pipelines-primer.ipynb) | Beam (transforms, streaming windows, watermarks, late data), xarray-beam (rechunking and reductions on chunked arrays), Spark (DataFrames, shuffles, window functions, joins, partitioned Parquet, Structured Streaming) |

Reusable code lives in `src/wxlab/`, so the notebooks stay short. Reference notes live in `docs/`, including a running [glossary](docs/glossary.md).

## Running the notebooks

The notebooks run locally on a laptop; no GPU is needed. Create an environment and install the package with its extras, then open the notebooks from the `notebooks/` folder:

```bash
uv venv .venv && uv pip install -e ".[reference,radar,model,arrays,pipelines]"
```

Reading HRRR's GRIB2 files needs the ecCodes library (`brew install eccodes` on macOS). Spark needs a Java 17 or 21 runtime; the pipelines primer looks for `JAVA_HOME`, then `~/.jdks`, then the system default. Downloaded and processed data goes to `data/`, which git ignores. The notebooks are saved with their outputs, so they can also be read on GitHub without running anything.

All data sources are public and read anonymously from NOAA's open data buckets on AWS.

## Status

Phase 1 in progress. See [docs/learning-log.md](docs/learning-log.md).
