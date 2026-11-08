# Decisions

The choices made for the lab and why. When a decision changes, this file is rewritten to state the current decision, and the change is noted in the learning log.

## One project, owned end to end

The lab is organized around one dataset rather than a tour of topics. Hourly observations from about 165 California weather stations are taken from raw archives to a training-ready dataset with a quality verdict on every value, and the verdicts are tested for accuracy and for their effect on a model. Every notebook is a step of that project. A single concrete dataset forces the hard questions (what exactly counts as wrong, how sure are we, does it matter) that a survey of topics lets you skip.

## What "quality" means

A value is good if a model would learn the right thing from it. This ties quality to use: a value can be what the sensor reported and still be bad training data, for example during a timestamp mix-up. Each check is a written rule. Verdicts are pass, suspect or quarantine per station-hour; each check's flag is stored beside the data; nothing is deleted. Quarantine requires evidence from two independent sources (for example the reference model and neighboring stations), because each source has weaknesses of its own.

## Two phases, stations first

There are many kinds of physical sensor data; learning a few well is more useful than skimming many, because the skills transfer.

**Phase 1 uses surface stations and a weather model.** A thermometer measures the air it sits in, with little processing between the sensor and the number. That makes stations the clearest place to learn what bias, drift and step changes look like. Pairing them with a gridded model adds a second source and a second format (GRIB2).

**Phase 2 adds radar.** Radar is the opposite of a station: indirect, three-dimensional and full of artifacts (ground clutter, anomalous propagation, birds and insects, beam blockage, calibration differences between radars). Radar estimates rainfall and many stations have rain gauges, so the two can be checked against each other. Radar is not part of phase 1 because learning its formats and artifacts properly takes days, and a rushed radar notebook would weaken both phases.

The Iowa Environmental Mesonet also serves US airport station data, but it reports the same sensors as ISD, so it is not an independent cross-check. It is not used.

## Variables

Air temperature is the main variable: it is checked, predicted and scored. HRRR has a direct counterpart (2 m temperature), and temperature is smooth enough that faults stand out. Dew point is checked too; it can never exceed the air temperature, which gives a real physical check across two variables. Wind and precipitation are parsed as supporting data, to define storm hours and to see whether data goes missing when the weather is extreme. All of them come from the same ISD files.

## Reference: the HRRR 1-hour forecast, 2021 onward

Checks compare each station with an independent estimate of the same quantity. Weather affects both the station and the reference, so it cancels when one is subtracted from the other; what remains is mostly the station's own error plus representativeness error (a point measurement and a 3 km grid cell disagree even when both are right).

**Why HRRR.** HRRR (the High-Resolution Rapid Refresh) is NOAA's hourly 3 km model for the US, free on AWS with no account. The Herbie library downloads only the fields requested. ERA5 is global but about 30 km, and reading long series at points from its public cloud copy means downloading hundreds of gigabytes, because it is stored one whole-globe hour per chunk.

**Why the 1-hour forecast and not the analysis.** HRRR's analysis is produced by data assimilation, which pulls the model toward observations, including these same airport reports. Using it would partly compare stations with themselves: faults would be damped in the checks, and in the model experiment the input would already contain the target. The forecast valid at the same hour, started one hour earlier, has not seen that observation. Independence is improved rather than perfect, because the previous hour's observations shaped the forecast's starting point. The same issue applies to ERA5, which also assimilates station data.

**Why 2021–2024.** HRRR changed model versions several times (version 4 became operational in December 2020). Each change can shift the model's typical values, which would look like a step fault at every station at once. Starting in 2021 avoids that. A local timing test fetched one hourly field pair in about 2.6 seconds, so all hours of 2021–2024 (about 35,000 fetches) are practical with parallel downloads.

**Storage.** HRRR values at station points are kept for the project (under 1 GB). Full continental grids would be about 100 GB per variable for ten years and are never stored. One deliberate exception: the California crop of the temperature and dew point fields for 2021–2024 (about 20 GB compressed) is kept, because the download already contains it and it is the raw material for learning gridded storage (Zarr, xarray-beam) in notebook 11. Without it, that notebook would need the same hours downloaded again.

## Learning QA through planted faults, tested on real data

Faults are planted into clean data because a known answer is the only way to measure recall: how many problems a check misses. The setup avoids the usual traps. The baseline is screened first, because real data already contains unrecorded faults that would otherwise be counted as false alarms. Faults go into a realistic minority of stations (20–30%), so precision is not inflated. Each fault type is planted at a range of sizes, because detection depends strongly on size. Detections are scored per station-month within a tolerance of the true start.

Planted faults cannot measure precision on real data or produce real findings, so the same checks also run on the real data, a sample of flags is checked by hand, and the three most suspicious stations are investigated.

## Measuring the effect on a model

A small gradient-boosting model predicts station temperature from HRRR fields, a form of post-processing. It is trained on clean, faulty, QA'd and randomly thinned data, with everything else fixed. The random-thinning arm is the control: without it, an improvement from QA could simply come from training on less data. Faults are planted only in the training years; the test year (2024) and a set of held-out stations stay clean. Results use several random seeds, so differences can be told apart from run-to-run noise.

## Notebooks teach through a story

Each notebook follows the style of the *Learning WeatherBench 2* guide: the reader learns each concept at the moment the project needs it, in flowing prose, with every term explained where it first appears, "In ML terms" bridges, and "Check yourself" questions. The point is that someone new to weather data can follow the whole project and come out understanding the domain.

## Runtime: a laptop

Everything runs locally on one laptop, with no GPU. The data fits comfortably: about 10 GB of raw station files, under 300 MB of standardized station data and under 1 GB of HRRR values at station points. Notebooks are saved with their outputs so they can be read on GitHub without running them. Colab is not a target: long background downloads and a local cache suit a laptop better than temporary cloud sessions.

## Framework depth

Ray is learned in depth because it runs ordinary Python functions in parallel, which suits decoding scientific file formats and feeding training. Spark is learned at a working level on the station tables, where its table-oriented model fits. Beam is learned at a high level, with a first pipeline on the local runner and a look at xarray-beam, which large weather datasets have been built with. All three come in phase 2, rebuilding the phase 1 pipeline, so the frameworks are learned on a dataset that is already understood.

xarray is learned from notebook 03 onward, since every HRRR file opens as an xarray dataset. Zarr and xarray-beam are learned together in notebook 11 on a real four-year HRRR cube, because their purpose (choosing and changing how a large array is chunked) only shows up on data big enough for the choice to matter. Each tool comes with a short list of documentation pages to read first: the docs explain the concepts and design reasons, and the notebook provides the practice.
