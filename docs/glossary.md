# Glossary

Terms used in the notebooks, in plain words. Each entry names the notebook where the term is first explained.

| Term | Meaning | First used |
|---|---|---|
| **Analysis** | A model's best estimate of the current state of the atmosphere, made by data assimilation. A forecast run starts from it. | 03 |
| **Anomaly** | A value minus its climatology: what is left after removing the expected daily and seasonal cycle. | 01 |
| **ASOS / AWOS** | Automated weather stations at US airports. ASOS stations are run by the national weather and aviation agencies at larger airports; AWOS stations are at smaller airports and often report whole degrees Celsius. | 01 |
| **Background (first guess)** | The model's previous short forecast, which data assimilation nudges toward new observations. | 03 |
| **Ball tree** | A spatial index for finding the nearest grid points to a location quickly, used on grids that are not latitude–longitude. | 03 |
| **Baseline (stations)** | The stations we trust enough to plant faults into: nearly complete, steady against HRRR, no gross errors and no duplicates. "Nothing we can see" rather than "certainly clean". | 04 |
| **Bias** | The average difference between two sources (here, station minus model). A steady offset. | 03 |
| **Break (inhomogeneity)** | A point where a station's record changes character, such as a step from a sensor swap. A property of a period rather than of single hours. | 05 |
| **CF conventions** | The community standard for naming and describing weather and climate variables, for example `air_temperature` with units. | 01 |
| **Chance baseline** | How often a check would "detect" a fault in a given window on a station with no fault, from background false alarms alone. | 05 |
| **Climatology** | The expected value for a place, day of year and time of day, averaged over many years. "Is this plausible?" means "how far is it from climatology?" | 01 |
| **Cold-air pooling** | On clear, calm nights, cold air drains downhill and collects in valley floors, making them much colder than nearby slopes. | 03 |
| **Completeness** | The fraction of expected time steps that have a value. The simplest coverage metric. | 01 |
| **Consistency** | Whether a source agrees with independent sources and with its own past. | 05 |
| **Correctness** | Whether the values that are present are right. | 05 |
| **Coverage** | Whether the data is there: completeness overall and per condition, such as storm hours. | 05 |
| **Data assimilation** | Combining a model's background with observations, weighting each by its reliability, to estimate the current state. | 03 |
| **Detection lag** | How long after a fault starts a scheduled check first raises an alarm. | 05 |
| **Dew point** | The temperature to which air must cool for its water vapour to start condensing. Never higher than the air temperature. | 01 |
| **Diurnal cycle** | The daily rise and fall of temperature, from a minimum near dawn to a maximum in mid-afternoon. | 01 |
| **Drift** | A sensor error that grows slowly over months, as with an ageing sensor or a dirty radiation shield. | 04 |
| **Duplicate station** | One physical station carried under two ids in an archive, for example after a base is renamed. Not an independent witness. | 04 |
| **Fault catalogue** | The answer key for planted faults: which station, which type, what size, and when each fault starts, ends and actually touches the data. | 04 |
| **FM-12 (SYNOP)** | The international synoptic report exchanged between weather services every 3 or 6 hours, on the hour. | 01 |
| **FM-13** | The marine version of the synoptic report, used by ships, buoys and coastal stations. | 01 |
| **FM-15 (METAR)** | The routine hourly airport report, issued a few minutes before each hour (the exact minute differs by airport). | 01 |
| **FM-16 (SPECI)** | A special airport report, issued whenever the weather changes enough to matter for flying. | 01 |
| **GRIB2** | The binary format weather centres exchange model output in: a stack of independent messages, one 2-D field each. | 03 |
| **Held-out stations** | Stations kept out of training and free of planted faults, used to test whether a model works at places it never saw. | 04 |
| **HOMR** | NOAA's Historical Observing Metadata Repository: station names, locations, platforms and equipment history. | 05 |
| **HRRR** | High-Resolution Rapid Refresh: NOAA's hourly weather model for the continental US on a 3 km grid. | 03 |
| **Idempotent** | Running a unit of work twice gives the same result as running it once, so retries and reruns are safe. | 01 |
| **Index file (.idx)** | A small text file next to a GRIB2 file listing each message's byte position, so a reader can download only the fields it needs. | 03 |
| **Initialization, lead and valid time** | When a forecast run started, how far ahead it looks, and the moment it describes (initialization plus lead). | 03 |
| **Inverse-distance weighting** | Estimating a value at a point as an average of nearby grid points, with nearer points weighted more (weight ∝ 1/distance). | 03 |
| **ISD** | NOAA's Integrated Surface Database: hourly and sub-hourly reports from land stations, ships and buoys worldwide. | 01 |
| **Keep-alive (HTTP)** | Reusing one network connection for many requests instead of opening a new one each time. | 03 |
| **Lambert conformal projection** | A map projection made by laying a cone over a region; keeps shapes nearly right in mid-latitudes. HRRR's grid uses it. | 03 |
| **Lapse rate** | How fast air temperature falls with height, about 6.5 °C per kilometre on average. | 01 |
| **MAD (median absolute deviation)** | The median distance of values from their median. Times 1.4826 it estimates the standard deviation without being stretched by outliers. | 05 |
| **Marine layer / inversion** | Cool, moist ocean air near the surface with warmer air above it. Temperature rises with height instead of falling, so the standard lapse rate does not apply. | 03 |
| **Missing not at random** | Data whose chance of being missing depends on the value or the conditions, like readings lost in storms. Dropping those rows biases what a model learns. | 04 |
| **Planted fault** | A known error added on purpose to a copy of good data so a check's recall can be measured. Also called fault or anomaly injection. | 04 |
| **Precision** | The share of a check's alarms that point at real problems. | 04 |
| **Quality code (ISD)** | A one-character flag NOAA attaches to every value: `1`/`5` passed, `2`/`6` suspect, `3`/`7` erroneous, `9` missing, plus letters for special cases. Evidence, not truth. | 01 |
| **Range request** | An HTTP request for a specific span of bytes of a file. | 03 |
| **Recall** | The share of real problems a check catches. Needs a known list of problems, which is why faults are planted. | 04 |
| **Representativeness error** | The honest disagreement between a point measurement and a grid-box average, even when both are right. | 03 |
| **RMSE** | Root mean squared error: the typical size of a difference, with large misses counting extra. | 03 |
| **Robust z-score** | Distance from the median in units of the MAD-based standard deviation. Used for thresholds so faults don't inflate their own yardstick. | 05 |
| **Santa Ana and sundowner winds** | Hot, dry offshore winds in Southern California (Santa Ana) and down the slopes above Santa Barbara (sundowner). Very local and fast-changing. | 05 |
| **Sparse sensors** | Instruments that measure accurately at a few points and leave everything in between unobserved, such as weather stations. | 01 |
| **Step (change)** | A sudden lasting jump in a station's readings, typical of an instrument swap, a recalibration or a station move. | 04 |
| **Storm hour** | Here, a station's windiest 2% of hours or any hour with at least 1 mm of rain. | 04 |
| **Stuck sensor** | A sensor that keeps reporting the same value regardless of the weather. | 04 |
| **Time offset** | Correct values labelled with the wrong time, such as local time read as UTC (8 hours off in California winter). | 04 |
| **Tule fog** | Dense ground fog in California's Central Valley on calm winter nights, sometimes lasting for days. | 05 |
| **USAF / WBAN** | Two historical station-numbering schemes (US Air Force; Weather Bureau, Army and Navy). Together they form an ISD station id. | 01 |
| **USCRN** | The US Climate Reference Network: research-grade stations built for long-term climate monitoring, with three thermometers per site. | 01 |
| **UTC** | Coordinated Universal Time, the single time zone weather data uses. California is 7 or 8 hours behind. | 01 |
| **Verdict** | The combined judgement on a station-hour: pass, suspect (one check fired) or quarantine (two independent kinds of evidence agree). | 05 |
