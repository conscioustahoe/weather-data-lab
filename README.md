# weather-data-lab

A hands-on lab for learning how to ingest, standardize and quality-check physical sensor data so that it can be trusted as training data for machine learning models.

The lab uses two kinds of real, public weather data:

- **Surface weather stations**, which measure temperature, humidity, wind and rain at fixed points. These are "sparse sensors": very accurate where they exist, absent everywhere else.
- **Weather radar**, which scans the sky every few minutes and sees precipitation as a three-dimensional volume around each radar site.

Most machine learning weather models train on ERA5, a reanalysis in which a weather center has already combined, checked and cleaned millions of observations. This lab works one step earlier, with the raw observations themselves. It asks the questions that someone has to answer before any of that data reaches a model. How do you read each source correctly? How do you put different sources on a common footing? How do you notice when a sensor is quietly wrong? And how do you show that cleaning the data actually makes a model better?

## How the lab is organized

Each notebook is one lesson. It opens with a plain-language explanation of the idea, then works through it in code on real data. Read and run them in order.

| # | Notebook | What it covers |
|---|---|---|
| 00 | [The map](notebooks/00-the-map.ipynb) | The path from sensor to model, and how the notebooks fit together |
| 01 | [Stations: read and explore](notebooks/01-stations-read-and-explore.ipynb) | Station archives, format specs, quality codes, gaps |
| 02 | [Radar: read and explore](notebooks/02-radar-read-and-explore.ipynb) | Raw radar volumes, the national mosaic, radar artifacts |
| 03 | [Matching to a reference](notebooks/03-matching-to-a-reference.ipynb) | Putting stations and a gridded model on common footing |
| 04 | [How data goes wrong](notebooks/04-how-data-goes-wrong.ipynb) | Bias, drift, step changes and artifacts, by injecting known faults |
| 05 | [QA checks and metrics](notebooks/05-qa-checks-and-metrics.ipynb) | Coverage, correctness and consistency; catching the injected faults |
| 06 | [Monitoring and verdicts](notebooks/06-monitoring-and-verdicts.ipynb) | Running checks over time, verdicts, measuring how often checks are right |
| 07 | [Pipelines with Ray](notebooks/07-pipelines-with-ray.ipynb) | Parallel, idempotent ingestion |
| 08 | [Spark and Beam](notebooks/08-spark-and-beam.ipynb) | The same job in Spark, a first Beam pipeline, and how the three compare |
| 09 | [Does clean data help the model?](notebooks/09-does-clean-data-help-the-model.ipynb) | A controlled experiment: clean vs faulty vs quality-checked data |
| 10 | [Radar vs gauges](notebooks/10-radar-vs-gauges.ipynb) | Consistency across sources (stretch) |

Reusable code lives in `src/wxlab/`, so the notebooks stay short and readable. Longer reference notes live in `docs/`.

## Running the notebooks

The notebooks are written to run on the free tier of Google Colab. Open any notebook from GitHub in Colab, then run the setup cell at the top. It clones this repository, installs the package and mounts Google Drive, so that downloaded and processed data survives between sessions.

All data sources used here are public. Most are read directly from NOAA's open data buckets on AWS and need no account.

## Status

Work in progress. See [docs/learning-log.md](docs/learning-log.md) for notes as the lab develops.
