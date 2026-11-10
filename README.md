# weather-data-lab

I wanted to learn how raw sensor data becomes something a model can learn from. So I took four years of hourly data from about 165 California weather stations and carried it from NOAA's public archives to a training-ready dataset with a quality verdict (pass, suspect or quarantine) on every value. Then I checked how often those verdicts are right and whether they actually make a model better.

Once that worked I added radar as a second sensor, rebuilt the pipeline in Ray, Spark and Beam and stored four years of the HRRR weather model in Zarr to see what changes with scale.

The story with the main results is on the [project page](https://conscioustahoe.github.io/weather-data-lab/).

## The notebooks

Each one answers one question. They're saved with their outputs so you can read them on GitHub without running anything.

| # | Notebook | The question it answers |
|---|---|---|
| 01 | [Stations: read and explore](notebooks/01-stations-read-and-explore.ipynb) | what does a station actually report? |
| 02 | [Radar: read and explore](notebooks/02-radar-read-and-explore.ipynb) | how does a radar see and what fools it? |
| 03 | [Matching to a reference](notebooks/03-matching-to-a-reference.ipynb) | what can a thermometer be compared against? |
| 04 | [How data goes wrong](notebooks/04-how-data-goes-wrong.ipynb) | what do real sensor faults look like? |
| 05 | [QA checks and metrics](notebooks/05-qa-checks-and-metrics.ipynb) | how often are the verdicts right? |
| 06 | [Monitoring and verdicts](notebooks/06-monitoring-and-verdicts.ipynb) | do verdicts change over time? |
| 07 | [Pipelines with Ray](notebooks/07-pipelines-with-ray.ipynb) | how do I rerun everything safely? |
| 08 | [Spark and Beam](notebooks/08-spark-and-beam.ipynb) | what is each framework built for? |
| 09 | [Does clean data help the model?](notebooks/09-does-clean-data-help-the-model.ipynb) | does any of this matter to a model? |
| 10 | [Radar vs gauges](notebooks/10-radar-vs-gauges.ipynb) | when do two rain sensors disagree and why? |
| 11 | [Gridded data at scale](notebooks/11-gridded-data-at-scale.ipynb) | what does chunk shape cost? |

The reusable code is in `src/wxlab/` and weather terms are explained in the [glossary](docs/glossary.md).

## Running them

Everything runs on a laptop (no GPU).

```bash
uv venv .venv && uv pip install -e ".[reference,radar,model,arrays,pipelines]"
```

Reading GRIB2 needs ecCodes (`brew install eccodes`) and notebook 08 needs Java 17 or 21 for Spark. Data goes to `data/` which git ignores. All sources are public and read anonymously from NOAA's buckets on AWS.
