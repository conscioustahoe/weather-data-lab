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

## Decisions already made

See `docs/decisions.md` for the reasoning behind each one.

- **Two modalities:** surface stations (NOAA ISD on AWS; US airport stations via the Iowa Environmental Mesonet) and radar (NEXRAD Level II raw volumes and the MRMS national mosaic, both on AWS).
- **Reference dataset:** HRRR, NOAA's hourly 3 km US model, read from AWS with the Herbie library. ERA5 from the Copernicus Climate Data Store is optional.
- **Runtime:** Google Colab free tier. Notebooks must run there.
- **How the QA skills are learned:** fault injection. Take clean data, inject known faults (step, drift, bias, stuck sensor, time offset, gaps during storms), build checks to catch them, measure precision and recall, then apply the same checks to real data.
- **Monitoring:** simulated by feeding data through month by month and recording metrics per run.
- **Model impact:** a small model (for example, gradient boosting predicting station temperature from HRRR) trained on clean, faulty and quality-checked data, compared on held-out time with several seeds.
- **Starter scope:** about 200 California stations, 2 m temperature first, about 10 years. Widen only after the full path works end to end.

## The plan

The notebooks are numbered and meant to be done in order. See `docs/plan.md` for what each one covers and what "done" looks like. The first milestone is notebooks 01, 03, 04, 05 and 09 working end to end. Together they form a complete small project: ingest station data, compare it with a reference, inject and catch faults, and measure the effect on a model.

## How to work in this repository

### Notebooks
- Name notebooks `NN-short-kebab-name.ipynb` in `notebooks/`.
- Every notebook starts with Markdown cells that tell the story before any code: what the idea is, why it matters, and what to look for in the output. Write descriptively and plainly, as a story that flows, and explain every domain term the first time it appears. Avoid compact, list-only writing that leaves the reader to connect the ideas.
- Between code sections, add short Markdown cells that explain what just happened and what the reader should notice.
- The first code cell is the Colab setup cell (clone the repo, `pip install -e .`, mount Google Drive). Keep it identical across notebooks.
- Keep notebooks thin. Reusable logic (parsers, matching, checks, metrics, fault injection) goes in `src/wxlab/` and is imported.
- Keep saved outputs modest so GitHub can render the notebooks: a few plots, no huge tables.

### Code
- Python package `wxlab` in `src/wxlab/`. Small modules with docstrings that explain the domain reasoning, not just the mechanics.
- Standardized data uses SI units, UTC timestamps and CF-style variable names (for example `air_temperature` in kelvin or degrees Celsius, stated explicitly).
- Keep raw data immutable. Processing goes raw → standardized → QA outputs, with work split into idempotent units keyed by source and time (for example station and year).
- Store quality flags beside the data. Never delete values because a check failed.
- Data lives under `data/` locally (git-ignored) and under the mounted Drive folder on Colab. Never commit data.
- Read public AWS buckets anonymously (`s3fs` with `anon=True`, or `boto3` with unsigned config).

### Git
- Commits are authored and committed by the repository owner only. Never add `Co-Authored-By` trailers, "Generated with" lines or any other AI attribution to commit messages, pull request descriptions or files.
- Write commit messages in plain, imperative English describing the change.

### Working style
- Before writing a notebook, explain the plan for it in a few sentences and confirm it with the owner.
- When a domain fact matters (a format detail, a sensor behavior, a typical error size), say where it comes from, and prefer official documentation.
- Keep `docs/learning-log.md` useful: when the owner learns something surprising or makes a decision, offer to add a line.
