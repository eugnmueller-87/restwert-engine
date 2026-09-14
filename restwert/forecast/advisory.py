"""Advisories from the forecast (SPEC 5.6): ADV01 sell-before-launch, ADV02 calibration.

An advisory is a MODEL OPINION. It is attached to decision records by the decisions
module and never changes an outcome, never orders, lists or sends anything. Every row
carries the threshold key and the named owner of that threshold.

ADV01 ``sell_before_launch``: for a returned, unsold device whose family launches within
the lookahead window, compare the current marketplace forecast with the forecast 30 days
after the launch (one more launch step, same grade) net of holding cost. Emitted only when
``drop_pct >= sell_before_launch_min_drop_pct`` AND ``delta_eur > 0``. Devices whose grade
is the ``as_is_only_grade`` are skipped: rule R02 can only ever send them As Is, so a
marketplace-forecast drop is no argument for them.

ADV02 ``forecast_calibration``: trailing three complete months of the ``'*'`` error series;
when the mean CHANNEL-ADJUSTED bias (``bias_channel_adjusted``: forecast of record times
the run's factor for the channel actually used) exceeds ``forecast_recalibration_bias_pct``
in absolute value, one row asks a human to review the model. The channel-adjusted series
is used because a buyout or wholesale discount is channel mix, not model error; when the
column is absent (older tables) the baseline ``bias`` is used and the note says so.

"Has a return and no sale" is read from ``rv_forecast_current.grade_source``: a device
whose grade came from an inspection or a pre-return declaration has a return event; a
device on the 'expected' grade has not come back yet.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

from restwert.dates import days_between, next_launch_date
from restwert.forecast.features import as_date, to_datetime_series
from restwert.forecast.model import ResidualValueModel, predict_ratio
from restwert.records import Advisory

ADVISORY_COLUMNS = [
    "advisory_id",
    "as_of",
    "run_id",
    "kind",
    "subject_type",
    "subject_id",
    "confidence",
    "payload_json",
    "note",
    "threshold_key",
    "threshold_owner",
]

CONFIDENCE_MIN_TRAIN = 200
DAYS_AFTER_LAUNCH = 30


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=ADVISORY_COLUMNS)


def _row(
    *,
    advisory_id: str,
    as_of: date,
    run_id: str | None,
    adv: Advisory,
    subject_type: str,
    subject_id: str,
    threshold_key: str,
    threshold_owner: str,
) -> dict:
    return {
        "advisory_id": advisory_id,
        "as_of": pd.Timestamp(as_of),
        "run_id": run_id,
        "kind": adv.kind,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "confidence": adv.confidence,
        "payload_json": json.dumps(adv.payload, sort_keys=True, default=str),
        "note": adv.note,
        "threshold_key": threshold_key,
        "threshold_owner": threshold_owner,
    }


def sell_before_launch(
    current: pd.DataFrame,
    rvm: ResidualValueModel,
    catalogue: pd.DataFrame,
    devices: pd.DataFrame,
    families_cfg,
    a,
    thr,
    as_of: date,
) -> pd.DataFrame:
    """ADV01 rows (``advisories`` columns) for returned, unsold devices facing a launch."""
    as_of = as_date(as_of)
    if current is None or len(current) == 0:
        return _empty()
    look = thr.get("sell_before_launch_lookahead_days", as_of=as_of)
    min_drop = thr.get("sell_before_launch_min_drop_pct", as_of=as_of)
    holding_per_day = float(a.get("holding_cost_per_day_eur"))
    try:
        as_is_grade: str | None = str(thr.get("as_is_only_grade", as_of=as_of).value)
    except KeyError:
        as_is_grade = None

    cand = current[current["grade_source"].isin(["inspected", "pre_return"])].copy()
    if as_is_grade is not None:
        cand = cand[cand["grade_used"].astype(str) != as_is_grade]
    if len(cand) == 0:
        return _empty()
    dev = devices[["serial", "purchase_price", "launch_date", "storage_gb", "model"]].copy()
    dev["launch_date"] = to_datetime_series(dev["launch_date"])
    dev["purchase_price"] = pd.to_numeric(dev["purchase_price"], errors="coerce").astype(float)
    cand = cand.merge(dev, on="serial", how="inner", suffixes=("", "_dev"))
    if "model" not in cand.columns and "model_dev" in cand.columns:
        cand["model"] = cand["model_dev"]
    base = None
    if catalogue is not None and len(catalogue) and "base_storage_gb" in catalogue.columns:
        base = catalogue.set_index("model")["base_storage_gb"].astype(float)

    next_launch: dict[str, date | None] = {}
    rows = []
    for r in cand.itertuples(index=False):
        fam = str(r.model_family)
        if fam not in next_launch:
            next_launch[fam] = next_launch_date(fam, as_of, families_cfg)
        nl = next_launch[fam]
        if nl is None:
            continue
        days_to_launch = days_between(as_of, nl)
        if days_to_launch > int(look.value):
            continue
        rv_now = float(r.forecast_rv) if pd.notna(r.forecast_rv) else np.nan
        if not np.isfinite(rv_now) or rv_now <= 0 or pd.isna(r.launch_date):
            continue
        target = nl + timedelta(days=DAYS_AFTER_LAUNCH)
        m_after = (target - r.launch_date.date()).days / 30.4375
        n_after = int(r.n_launches_since) + 1 if pd.notna(r.n_launches_since) else 1
        base_gb = float(base.get(r.model, np.nan)) if base is not None else np.nan
        if not np.isfinite(base_gb):
            base_gb = float(families_cfg[fam].base_storage_gb) if fam in families_cfg else float(r.storage_gb)
        ratio_after = predict_ratio(
            rvm,
            family=fam,
            months_since_launch=m_after,
            n_launches_since=n_after,
            grade=str(r.grade_used),
            storage_gb=int(r.storage_gb),
            base_storage_gb=int(base_gb),
            channel="marketplace",
        )
        rv_after = round(ratio_after * float(r.purchase_price), 2)
        holding = round(holding_per_day * days_to_launch, 2)
        drop_pct = 1.0 - rv_after / rv_now
        delta_eur = round(rv_now - rv_after - holding, 2)
        if drop_pct < float(min_drop.value) or delta_eur <= 0:
            continue
        n_train = int(rvm.n_train.get(fam, 0))
        confidence = "low" if (n_train < CONFIDENCE_MIN_TRAIN or rvm.fit_quality.get(fam) != "family") else "medium"
        adv = Advisory(
            kind="sell_before_launch",
            run_id=rvm.run_id,
            payload={
                "rv_now": round(rv_now, 2),
                "rv_after": rv_after,
                "drop_pct": round(float(drop_pct), 4),
                "delta_eur": delta_eur,
                "holding_eur": holding,
                "days_to_launch": int(days_to_launch),
                "next_launch_date": nl.isoformat(),
                "grade": str(r.grade_used),
                "min_drop_pct": float(min_drop.value),
                "lookahead_days": int(look.value),
            },
            confidence=confidence,
            note=(
                f"advisory only: forecast {fam} launch on {nl.isoformat()} in {days_to_launch} days; "
                f"marketplace forecast drops {drop_pct * 100:.1f} % ({rv_now:.2f} -> {rv_after:.2f} EUR), "
                f"net of holding {delta_eur:.2f} EUR. Threshold owner: {look.owner}"
            ),
        )
        rows.append(
            _row(
                advisory_id=f"ADV01-{as_of:%Y%m%d}-{r.serial}",
                as_of=as_of,
                run_id=rvm.run_id,
                adv=adv,
                subject_type="device",
                subject_id=str(r.serial),
                threshold_key=look.key,
                threshold_owner=look.owner,
            )
        )
    if not rows:
        return _empty()
    return pd.DataFrame(rows)[ADVISORY_COLUMNS].sort_values("subject_id").reset_index(drop=True)


def calibration_advisory(error_series: pd.DataFrame, thr, as_of: date, run_id: str) -> pd.DataFrame:
    """ADV02: one row when the trailing 3 complete months' mean channel-adjusted bias exceeds
    the threshold (baseline ``bias`` when the channel-adjusted column is absent)."""
    as_of = as_date(as_of)
    if error_series is None or len(error_series) == 0:
        return _empty()
    t = thr.get("forecast_recalibration_bias_pct", as_of=as_of)
    df = error_series[error_series["model_family"] == "*"].copy()
    df["month"] = to_datetime_series(df["month"])
    floor = pd.Timestamp(as_of).to_period("M").to_timestamp()
    col = "bias_channel_adjusted" if "bias_channel_adjusted" in df.columns and df["bias_channel_adjusted"].notna().any() else "bias"
    basis = "channel-adjusted forecast (model view)" if col == "bias_channel_adjusted" else "marketplace-baseline forecast (business view; channel-adjusted column absent)"
    df = df[(df["month"] < floor) & df[col].notna()].sort_values("month").tail(3)
    if len(df) == 0:
        return _empty()
    mean_bias = float(df[col].astype(float).mean())
    baseline_bias = float(df["bias"].astype(float).mean()) if "bias" in df.columns and df["bias"].notna().any() else None
    if abs(mean_bias) <= float(t.value):
        return _empty()
    months = [m.strftime("%Y-%m") for m in df["month"]]
    direction = "too high (collateral overstated)" if mean_bias > 0 else "too low"
    adv = Advisory(
        kind="forecast_calibration",
        run_id=run_id,
        payload={
            "mean_bias_3m": round(mean_bias, 4),
            "bias_basis": basis,
            "mean_bias_3m_marketplace_baseline": None if baseline_bias is None else round(baseline_bias, 4),
            "months": ",".join(months),
            "n_months": int(len(df)),
            "n_sales": int(df["n_with_forecast"].sum()),
            "threshold_bias_pct": float(t.value),
        },
        confidence="medium",
        note=(
            f"advisory only: mean forecast bias over {len(df)} complete months is {mean_bias * 100:+.1f} % "
            f"against the {basis}, forecast {direction}; beyond {float(t.value) * 100:.0f} %. "
            f"A human reviews the model. Owner: {t.owner}"
        ),
    )
    row = _row(
        advisory_id=f"ADV02-{as_of:%Y%m%d}-{run_id}",
        as_of=as_of,
        run_id=run_id,
        adv=adv,
        subject_type="forecast",
        subject_id=run_id,
        threshold_key=t.key,
        threshold_owner=t.owner,
    )
    return pd.DataFrame([row])[ADVISORY_COLUMNS]
