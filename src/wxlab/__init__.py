"""wxlab: ingestion and quality checks for weather station and radar data.

Modules are added as the notebooks need them:

- ``stations``  reading and standardizing station archives (notebook 01)
- ``radar``     reading radar volumes and mosaics (notebook 02)
- ``reference`` matching stations to a gridded reference (notebook 03)
- ``faults``    injecting known faults for testing checks (notebook 04)
- ``qa``        quality checks, metrics and report cards (notebook 05)
- ``monitor``   incremental runs, verdicts and alerting (notebook 06)
"""

__version__ = "0.1.0"
