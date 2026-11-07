# Decisions

This file records the choices made for the lab and why. When a decision changes, update it here and note the change in the learning log.

## Two modalities: surface stations and radar

There are many kinds of physical sensor data: surface stations, weather balloons, aircraft reports, radar, satellite imagery, hyperspectral sounders, lidar and radio occultation. Learning a few of them well is more useful than skimming all of them, because the skills transfer.

Surface stations come first because they are the most direct measurement. A thermometer measures the air it sits in, with little processing between the sensor and the number. That makes them the clearest place to learn what bias, drift and step changes look like. They are also exactly what people mean by "sparse sensors": accurate at a few points, absent everywhere else.

Radar comes second because it is the opposite. It measures indirectly (how much of a microwave pulse bounces back), it has an unusual geometry (polar coordinates around each radar site, with the beam rising above the curving Earth), and it has a rich set of artifacts: ground clutter, anomalous propagation, bird and insect echoes, beam blockage and calibration differences between radars. Radar and stations also overlap, because radar estimates rainfall and many stations have rain gauges. That overlap makes it possible to measure consistency across sources.

Both are free on NOAA's open data buckets on AWS and need no account.

## Reference dataset: HRRR, with ERA5 optional

Quality checks on station data rely on comparing each station with an independent estimate of the same quantity. Weather happens in both the station and the reference, so it cancels when you subtract one from the other, and what remains is the station's own error.

HRRR (the High-Resolution Rapid Refresh) is NOAA's hourly model analysis for the United States at 3 km resolution. It is free on AWS with no account and no queue. The Herbie library downloads only the variables you ask for, which keeps transfers small. For a US-only lab it is a better fit than ERA5, which is global but coarser at about 30 km.

ERA5 remains useful for comparison. The public cloud copy of ERA5 is stored with one chunk per hour covering the whole globe, so reading a long time series at a few points from it means downloading hundreds of gigabytes. The Copernicus Climate Data Store lets you request a small region instead, with a free account and a queue.

## Runtime: Google Colab

Colab's free tier is enough for this scope. Data is read anonymously from AWS, the libraries install with pip, Ray runs on a single machine and Beam's local runner works. PySpark needs Java, which may require one `apt-get` command.

Colab sessions are temporary, so processed data is saved to Google Drive. Memory is limited to about 12 GB, so processing is done one station or one file at a time.

## Learning QA through fault injection

The fastest way to learn what subtle data problems look like is to create them on purpose. Starting from clean data, the lab injects known faults: a step change, a slow drift, a constant bias, a stuck sensor, a local-time offset and gaps that cluster during storms. Because the faults are known, every check can be scored by how many real problems it catches (recall) and how many of its flags are real (precision). After that, the same checks run on real data, where the answers are unknown and each flag has to be investigated.

## Monitoring simulated month by month

A real pipeline sees data arrive over time, not all at once. The lab simulates this by feeding data through one month at a time and recording quality metrics for each run. The history of those metrics is the monitoring view, and alerts fire when a metric changes.

## Measuring the effect on a model

A small model predicts station temperature from the surrounding HRRR fields. It is trained three ways: on clean data, on data with injected faults, and on the faulty data after QA has quarantined what it caught. The gap between the first two shows how much each fault hurts. The gap between the last two shows how much QA recovers. Comparisons use held-out time periods and several random seeds, so that differences can be told apart from run-to-run noise.

## Framework depth

Ray is learned in depth because it runs ordinary Python functions in parallel, which suits decoding scientific file formats and feeding training. Spark is learned at a working level on the station tables, where its table-oriented model fits. Beam is learned at a high level, with a first pipeline on the local runner and a look at xarray-beam, which large weather datasets have been built with.
