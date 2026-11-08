# Learning log

Short dated notes: things that surprised me, decisions I made and questions still open. Newest first.

| Date | Note |
|---|---|
| 2026-11-07 | Dropped Colab as a runtime: everything runs locally on a laptop. |
| 2026-11-07 | Reframed the lab as one project: a station dataset owned end to end, with a verdict on every value, tested for accuracy and for its effect on a model. Phase 1 is stations plus HRRR; radar moves to phase 2. Use HRRR's 1-hour forecast rather than its analysis (the analysis assimilates the same station reports) and start in 2021 (after HRRR's last major version change). See `decisions.md`. |
| 2026-11-07 | Set up the lab: two modalities (stations and radar), HRRR as the reference, Colab as the runtime, fault injection as the way to learn QA. See `decisions.md`. |
