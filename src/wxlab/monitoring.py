"""Monitoring QA over time: verdict churn, control charts on check rates, review overrides.

Notebook 05 ran the checks once a month, each run seeing only data up to the end of its
month. This module looks at what that history says about the QA system itself.

- **Verdict churn.** A verdict made in March can disagree with the verdict the same hour
  gets once years more data exist: normals are better estimated, neighbours have filled
  in, a shift that looked like noise turns out to be a break. Measuring how often and in
  which direction verdicts change says how much a downstream user should trust a
  fresh verdict, and why verdicts need versions.
- **Control charts.** Each check fires at some background rate. A jump in that rate
  means a new fault, unusual weather or a check that has drifted. A rolling median and
  MAD of the previous months gives a band that doesn't need a normal distribution and
  isn't dragged around by one bad month (the same robust statistics as notebook 05).
- **Overrides.** A person reviewing an alarm produces a label. That label is data: it
  lives beside the automatic verdict with who, when and why, and never replaces it, so
  the automatic verdict can still be scored and the review can be undone.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

VERDICTS = ["pass", "suspect", "quarantine"]


def verdict_churn(asof: pd.DataFrame, final: pd.DataFrame) -> pd.DataFrame:
    """Join each hour's verdict as first issued with its verdict in hindsight.

    ``asof`` is the monthly-run flags (``qa.monitor``) and ``final`` a single run over all
    data (``qa.run_checks``). Hours missing in either are dropped.
    """
    a = asof[["station_id", "time", "verdict"]].rename(columns={"verdict": "first"})
    f = final[["station_id", "time", "verdict"]].rename(columns={"verdict": "final"})
    j = a.merge(f, on=["station_id", "time"])
    j = j[j["first"].isin(VERDICTS) & j["final"].isin(VERDICTS)]
    rank = {v: i for i, v in enumerate(VERDICTS)}
    j["change"] = np.sign(j["final"].map(rank) - j["first"].map(rank)).map({-1: "relaxed", 0: "same", 1: "tightened"})
    return j


def churn_table(j: pd.DataFrame) -> pd.DataFrame:
    """Share of hours per first verdict that end up in each final verdict (rows sum to 1)."""
    return pd.crosstab(j["first"], j["final"], normalize="index").reindex(index=VERDICTS, columns=VERDICTS)


def control_chart(metrics: pd.DataFrame, cols, window: int = 12, min_periods: int = 6,
                  k: float = 4.0, floor: float = 0.0) -> pd.DataFrame:
    """Robust control band per metric from the previous ``window`` months.

    Returns long-form rows: month, metric, value, centre, lower, upper, alert. The band is
    median ± k × 1.4826 × MAD of earlier months only, so a month never judges itself.
    ``floor`` keeps the band from collapsing to zero width for checks that almost never fire.
    """
    out = []
    m = metrics.sort_values("month").reset_index(drop=True)
    for c in cols:
        x = m[c].astype(float)
        prev = x.shift(1)
        centre = prev.rolling(window, min_periods=min_periods).median()
        mad = prev.rolling(window, min_periods=min_periods).apply(lambda s: np.median(np.abs(s - np.median(s))), raw=True)
        half = np.maximum(k * 1.4826 * mad, floor)
        out.append(pd.DataFrame({"month": m["month"], "metric": c, "value": x, "centre": centre,
                                 "lower": centre - half, "upper": centre + half,
                                 "alert": (x > centre + half) | (x < centre - half)}))
    return pd.concat(out, ignore_index=True)


def overrides_from_review(review: pd.DataFrame, reviewer: str, reviewed_at: str) -> pd.DataFrame:
    """Turn a hand-review sheet (station_id, month, label, reason) into override records.

    A review is about one station-month of alarms. ``weather`` means the alarms were real
    weather and the quarantine should be lifted to suspect; ``fault`` confirms it;
    ``unclear`` leaves the automatic verdict alone but records that someone looked.
    """
    r = review.copy()
    r["station_id"] = r["station_id"].astype(str)
    r["month"] = pd.PeriodIndex(r["month"], freq="M")
    r["action"] = r["label"].map({"weather": "lift_to_suspect", "fault": "confirm", "unclear": "none"})
    r["reviewer"], r["reviewed_at"] = reviewer, pd.Timestamp(reviewed_at, tz="UTC")
    return r[["station_id", "month", "label", "action", "reason", "reviewer", "reviewed_at"]]


def apply_overrides(flags: pd.DataFrame, overrides: pd.DataFrame) -> pd.DataFrame:
    """Add ``reviewed_verdict`` beside ``verdict``. The automatic verdict is never changed."""
    f = flags.copy()
    f["month"] = f["time"].dt.tz_localize(None).dt.to_period("M")
    f = f.merge(overrides[["station_id", "month", "label", "action"]], on=["station_id", "month"], how="left")
    lift = (f["action"] == "lift_to_suspect") & (f["verdict"] == "quarantine")
    f["reviewed_verdict"] = f["verdict"].where(~lift, "suspect")
    return f.drop(columns="month")
