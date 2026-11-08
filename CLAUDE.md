# Context for Claude

This file is read at the start of every session. It explains what this repository is for, what has already been decided and how to work in it. Longer background lives in `docs/`: read `docs/plan.md` and `docs/decisions.md` before starting any notebook.

## What this repository is

A learning lab, owned by one person, for building real skill in ingesting and quality-checking physical sensor data for machine learning. The owner is an experienced backend and ML engineer (distributed systems, production pipelines, model training and evaluation) who is new to atmospheric and weather data. Explanations should assume strong engineering and ML knowledge and no meteorology.

The repository is public. Keep it about the subject matter. Do not add anything personal about the owner: no employer names, no career plans and no mention of why the lab exists beyond learning.

## What the owner wants to learn

1. How different kinds of physical sensor data work, starting with two: surface weather stations and weather radar. For each: what it measures, its geometry, its formats and how it fails.
2. How to read and work with each source in practice, in notebooks.
3. How to build ingestion pipelines that turn raw sources into standardized, training-ready data, for history (batch) and for new data (streaming). Ray in depth, Spark at a working level, Beam at a high level, plus how the three compare.
4. How to define quality metrics for coverage, correctness and consistency across sources, and catch subtle problems such as sensor bias, drift and processing artifacts. This area is completely new to the owner, so it needs the most care.
5. How to design automated QA checks that run continuously over time, turn measurements into verdicts, and measure how often those verdicts are right.
6. How to show, with a controlled experiment, that a cleaner or better dataset leads to better model performance.

## The project

One dataset owned end to end: hourly observations from about 165 California weather stations, 2021–2024, taken from raw public archives to a standardized, training-ready dataset with a quality verdict (pass, suspect, quarantine) on every station-hour. The verdicts are tested for accuracy (recall on planted faults, precision on hand-checked real flags) and for their effect on a model. A value is "good" if a model would learn the right thing from it.

## Decisions already made

See `docs/decisions.md` for the reasoning behind each one.

- **Phases:** phase 1 is stations (NOAA ISD on AWS) plus the HRRR model; phase 2 adds radar (NEXRAD Level II and the MRMS mosaic) and rebuilds the pipeline with Ray, Spark and Beam. Phase 2 does not start until phase 1 is complete.
- **Variables:** air temperature is the main variable; dew point is checked too; wind and precipitation are parsed as supporting data.
- **Reference:** HRRR's 1-hour forecast (not the analysis, which assimilates the same station reports), read with Herbie, 2021–2024 to avoid earlier model-version changes. Only values at station points are stored.
- **QA:** planted faults (step, drift, bias, stuck sensor, time offset, storm gaps) in 20–30% of stations at a range of sizes measure recall; hand-checked real flags measure precision; the three most suspicious real stations are investigated. Quarantine needs a second independent witness.
- **Monitoring:** checks run month by month, recording metrics per run.
- **Model impact:** gradient boosting predicting station temperature from HRRR, trained on clean, faulty, QA'd and randomly thinned data; test on clean 2024 and held-out stations; several seeds.
- **Runtime:** everything runs locally on a laptop; no GPU. Notebooks are not designed for Colab.

## The plan

See `docs/plan.md` for each notebook's scope and what "done" looks like. Phase 1 is notebooks 01, 03, 04, 05 and 09.

## How to work in this repository

### Notebooks
- Name notebooks `NN-short-kebab-name.ipynb` in `notebooks/`.
- Every notebook starts with Markdown cells that tell the story before any code: what the idea is, why it matters, and what to look for in the output. Write descriptively and plainly, as a story that flows, and explain every domain term the first time it appears. Avoid compact, list-only writing that leaves the reader to connect the ideas.
- Teach through a story, in the style of the *Learning WeatherBench 2* guide: the reader learns each concept at the moment the project needs it. Add new terms to `docs/glossary.md`, use short "In ML terms" blockquotes where an idea has an ML or engineering equivalent, and end with "What surprised me" and a few "Check yourself" questions with answers. Every plot uses real data.
- Between code sections, add short Markdown cells that explain what just happened and what the reader should notice.
- The first code cell is the setup cell (sets `DATA_DIR` to `../data`). Keep it identical across notebooks.
- Keep notebooks thin. Reusable logic (parsers, matching, checks, metrics, fault injection) goes in `src/wxlab/` and is imported.
- Keep saved outputs modest so GitHub can render the notebooks: a few plots, no huge tables.

### Code
- Python package `wxlab` in `src/wxlab/`. Small modules with docstrings that explain the domain reasoning, not just the mechanics.
- Standardized data uses SI units, UTC timestamps and CF-style variable names (for example `air_temperature` in kelvin or degrees Celsius, stated explicitly).
- Keep raw data immutable. Processing goes raw → standardized → QA outputs, with work split into idempotent units keyed by source and time (for example station and year).
- Store quality flags beside the data. Never delete values because a check failed.
- Data lives under `data/` (git-ignored). Never commit data.
- Read public AWS buckets anonymously (`s3fs` with `anon=True`, or `boto3` with unsigned config).

### Git
- Commits are authored and committed by the repository owner only. Never add `Co-Authored-By` trailers, "Generated with" lines or any other AI attribution to commit messages, pull request descriptions or files.
- Write commit messages in plain, imperative English describing the change.

### Working style
- Before writing a notebook, explain the plan for it in a few sentences and confirm it with the owner.
- When a domain fact matters (a format detail, a sensor behavior, a typical error size), say where it comes from, and prefer official documentation.
- Keep `docs/learning-log.md` useful: when the owner learns something surprising or makes a decision, offer to add a line.
