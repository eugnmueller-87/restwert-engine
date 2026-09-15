"""``silver.reconciliation``: the ledger and v0.1 ``device_pnl`` must agree to the cent (SPEC_v0.2 6.4).

Both are computed from the same bronze rows by two different code paths (conform +
``build_device_pnl`` on one side, ``build_ledger_lines`` + ``build_device_ledger`` on the
other). One row per (serial, field) records both values and the difference; ``run_ledger``
refuses to finish when any row fails. Identity behind the last field pair::

    lifecycle_margin (v0.1) = result_v01_basis_eur
    lifecycle_result_eur    = result_v01_basis_eur - (staging + outbound_shipping + wipe_grading + holding_cost + support + mdm_operations)
                              + price_protection_credit

On synthetic data equality holds by construction; on real data a non-empty diff is the
finding the Data page shows (for instance a rental invoice that does not match the
contract's billing calendar).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

RECONCILED_FIELDS: tuple[tuple[str, str], ...] = (  # (device_pnl column, device_ledger column)
    ("landed_cost", "landed_cost"),
    ("purchase_price", "purchase_price"),
    ("months_billed", "months_billed"),
    ("rental_revenue", "rental_revenue"),
    ("repair_cost", "repair_eur"),
    ("replacement_logistics_cost", "replacement_logistics_eur"),
    ("return_logistics_cost", "return_logistics_eur"),
    ("refurb_cost", "refurb_eur"),
    ("channel_fees", "channel_fee_eur"),
    ("realised_rv", "realised_rv"),
    ("lifecycle_margin", "result_v01_basis_eur"),
)

RECONCILIATION_COLUMNS: list[str] = ["serial", "as_of", "field", "device_pnl_value", "ledger_value", "diff", "ok"]


def _num(df: pd.DataFrame, col: str, index: pd.Index) -> pd.Series:
    if df is None or col not in df.columns:
        return pd.Series(np.nan, index=index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce").astype("float64").reindex(index)


def reconcile_to_device_pnl(
    device_ledger: pd.DataFrame,
    device_pnl: pd.DataFrame,
    as_of: date,
    tol: float = 0.01,
) -> pd.DataFrame:
    """One row per (serial of ``device_pnl``, field): both values, ``diff`` and ``ok``.

    ``field`` is the v0.1 ``device_pnl`` column name (``RECONCILED_FIELDS`` maps it to the
    ledger column).

    ``ok`` is true when both values are NULL or ``abs(diff) <= tol``. A serial missing from
    the ledger fails every field (its ledger value is NULL while the P&L has one).
    """
    if device_pnl is None or len(device_pnl) == 0:
        return pd.DataFrame(columns=RECONCILIATION_COLUMNS)
    pnl = device_pnl.copy()
    pnl["serial"] = pnl["serial"].astype(str)
    pnl = pnl.drop_duplicates("serial").set_index("serial")
    dl = device_ledger.copy() if device_ledger is not None and len(device_ledger) else pd.DataFrame(columns=["serial"])
    dl["serial"] = dl["serial"].astype(str)
    dl = dl.drop_duplicates("serial").set_index("serial")
    idx = pnl.index
    parts: list[pd.DataFrame] = []
    for pnl_col, dl_col in RECONCILED_FIELDS:
        left = _num(pnl, pnl_col, idx)
        right = _num(dl, dl_col, idx)
        diff = (left - right).round(2)
        both_null = left.isna() & right.isna()
        ok = both_null | (left.notna() & right.notna() & (diff.abs() <= float(tol) + 1e-9))
        parts.append(pd.DataFrame({
            "serial": idx.to_numpy(),
            "as_of": as_of,
            "field": pnl_col,
            "device_pnl_value": left.round(2).to_numpy(),
            "ledger_value": right.round(2).to_numpy(),
            "diff": diff.to_numpy(),
            "ok": ok.to_numpy(),
        }))
    out = pd.concat(parts, ignore_index=True)
    out = out.sort_values(["serial", "field"], kind="stable").reset_index(drop=True)
    out = out.astype(object).where(pd.notna(out), None)
    out["ok"] = out["ok"].astype(bool)
    return out[RECONCILIATION_COLUMNS]


def assert_reconciled(recon: pd.DataFrame) -> None:
    """Raise ``ValueError`` naming the first failing serial and field; silent when every row is ok."""
    if recon is None or len(recon) == 0:
        return
    bad = recon[~recon["ok"].astype(bool)]
    if len(bad) == 0:
        return
    first = bad.iloc[0]
    n_serials = bad["serial"].nunique()
    raise ValueError(
        f"ledger does not reconcile to device_pnl: {len(bad)} failing row(s) on {n_serials} serial(s); "
        f"first serial {first['serial']} field {first['field']}: device_pnl={first['device_pnl_value']} "
        f"ledger={first['ledger_value']} diff={first['diff']}"
    )


__all__ = ["RECONCILED_FIELDS", "RECONCILIATION_COLUMNS", "reconcile_to_device_pnl", "assert_reconciled"]
