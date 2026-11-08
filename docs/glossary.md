# Glossary

Terms used in the notebooks, in plain words. Each entry names the notebook where the term is first explained.

| Term | Meaning | First used |
|---|---|---|
| **ASOS / AWOS** | Automated weather stations at US airports. ASOS stations are run by the national weather and aviation agencies at larger airports; AWOS stations are at smaller airports and often report whole degrees Celsius. | 01 |
| **Climatology** | The expected value for a place, day of year and time of day, averaged over many years. "Is this plausible?" means "how far is it from climatology?" | 01 |
| **Anomaly** | A value minus its climatology: what is left after removing the expected daily and seasonal cycle. | 01 |
| **CF conventions** | The community standard for naming and describing weather and climate variables, for example `air_temperature` with units. | 01 |
| **Completeness** | The fraction of expected time steps that have a value. The simplest coverage metric. | 01 |
| **Dew point** | The temperature to which air must cool for its water vapour to start condensing. Never higher than the air temperature. | 01 |
| **Diurnal cycle** | The daily rise and fall of temperature, from a minimum near dawn to a maximum in mid-afternoon. | 01 |
| **FM-12 (SYNOP)** | The international synoptic report exchanged between weather services every 3 or 6 hours, on the hour. | 01 |
| **FM-13** | The marine version of the synoptic report, used by ships, buoys and coastal stations. | 01 |
| **FM-15 (METAR)** | The routine hourly airport report, issued a few minutes before each hour (the exact minute differs by airport). | 01 |
| **FM-16 (SPECI)** | A special airport report, issued whenever the weather changes enough to matter for flying. | 01 |
| **Idempotent** | Running a unit of work twice gives the same result as running it once, so retries and reruns are safe. | 01 |
| **ISD** | NOAA's Integrated Surface Database: hourly and sub-hourly reports from land stations, ships and buoys worldwide. | 01 |
| **Lapse rate** | How fast air temperature falls with height, about 6.5 °C per kilometre on average. | 01 |
| **Quality code (ISD)** | A one-character flag NOAA attaches to every value: `1`/`5` passed, `2`/`6` suspect, `3`/`7` erroneous, `9` missing, plus letters for special cases. Evidence, not truth. | 01 |
| **Sparse sensors** | Instruments that measure accurately at a few points and leave everything in between unobserved, such as weather stations. | 01 |
| **USAF / WBAN** | Two historical station-numbering schemes (US Air Force; Weather Bureau, Army and Navy). Together they form an ISD station id. | 01 |
| **USCRN** | The US Climate Reference Network: research-grade stations built for long-term climate monitoring, with three thermometers per site. | 01 |
| **UTC** | Coordinated Universal Time, the single time zone weather data uses. California is 7 or 8 hours behind. | 01 |
