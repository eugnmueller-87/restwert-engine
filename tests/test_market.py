"""Public anchors: join, grade mapping, curve fit, summary. Fixture rows are invented, labelled so."""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from restwert.market.anchors import load_anchors, months_between, normalise_grade, parse_ram_gb, parse_storage_gb
from restwert.market.curves import HORIZONS, fit_curves
from restwert.market.run import run_market


def _write_fixture(root: Path) -> tuple[Path, Path]:
    cat = root / "catalogue"
    anc = root / "anchors"
    cat.mkdir()
    anc.mkdir()
    models = pd.DataFrame(
        [
            {"slug": "phone-x", "model_name": "Phone X", "oem": "OEM-A", "family": "Smartphone", "series": "X",
             "launch_date_de": "2023-09-22", "launch_date_kind": "verfuegbarkeit", "launch_source_url": "https://example.invalid/x",
             "successor": None, "successor_launch_date": None, "notes": "fixture"},
            {"slug": "phone-y", "model_name": "Phone Y", "oem": "OEM-B", "family": "Smartphone", "series": "Y",
             "launch_date_de": "2022-02", "launch_date_kind": "verfuegbarkeit", "launch_source_url": "https://example.invalid/y",
             "successor": None, "successor_launch_date": None, "notes": "fixture, month-only launch"},
            {"slug": "no-date", "model_name": "No Date", "oem": "OEM-A", "family": "Tablet", "series": "N",
             "launch_date_de": None, "launch_date_kind": None, "launch_source_url": None,
             "successor": None, "successor_launch_date": None, "notes": "fixture"},
        ]
    )
    variants = pd.DataFrame(
        [
            {"slug": "phone-x", "spec": "128 GB", "storage_gb": 128, "ram_gb": None, "rrp_eur_launch_de": 1000.0, "rrp_source_url": "u", "rrp_source_date": "2023-09-12"},
            {"slug": "phone-x", "spec": "256 GB", "storage_gb": None, "ram_gb": None, "rrp_eur_launch_de": 1130.0, "rrp_source_url": "u", "rrp_source_date": "2023-09-12"},
            {"slug": "phone-y", "spec": "8 GB / 128 GB", "storage_gb": None, "ram_gb": 8, "rrp_eur_launch_de": 800.0, "rrp_source_url": "u", "rrp_source_date": "2022-02-01"},
            {"slug": "no-date", "spec": "64 GB", "storage_gb": 64, "ram_gb": None, "rrp_eur_launch_de": 500.0, "rrp_source_url": "u", "rrp_source_date": "2020-01-01"},
        ]
    )
    # ten asks on a clean 2 percent per month decay with grade offsets, plus bids and a broken row
    rows = []
    for age, grade, cond in [(6, "A", "Premium"), (12, "B", "Sehr gut"), (18, "B", "Sehr gut"), (24, "C", "Gut"),
                             (30, "B", "Sehr gut"), (36, "A", "Wie neu"), (12, "D", "Akzeptabel"), (24, "B", "Grade B")]:
        base = math.exp(-0.02 * age) * {"A": 1.08, "B": 1.0, "C": 0.9, "D": 0.7}[grade]
        seen = pd.Timestamp("2023-09-22") + pd.DateOffset(months=age)
        rows.append({"slug": "phone-x", "spec": "128 GB", "condition": cond, "price_eur": round(1000 * base, 2),
                     "source_url": "https://example.invalid/m", "date_seen": seen.date().isoformat(), "source_kind": "refurbished-marktplatz"})
    for age in (12, 24, 36):
        seen = pd.Timestamp("2023-09-22") + pd.DateOffset(months=age)
        rows.append({"slug": "phone-x", "spec": "128 GB", "condition": "Trade-in bis zu", "price_eur": round(1000 * 0.6 * math.exp(-0.02 * age), 2),
                     "source_url": "https://example.invalid/t", "date_seen": seen.date().isoformat(), "source_kind": "ankauf-trade-in"})
    rows.append({"slug": "phone-x", "spec": "1 TB", "condition": "Sehr gut", "price_eur": 700.0, "source_url": "u", "date_seen": "2025-09-22", "source_kind": "refurbished-marktplatz"})
    rows.append({"slug": "phone-y", "spec": "128 GB", "condition": "Sehr gut", "price_eur": 300.0, "source_url": "u", "date_seen": "2026-02-15", "source_kind": "refurbished-marktplatz"})
    rows.append({"slug": "no-date", "spec": "64 GB", "condition": "Gut", "price_eur": 100.0, "source_url": "u", "date_seen": "2026-01-01", "source_kind": "refurbished-marktplatz"})
    rows.append({"slug": "unknown", "spec": "64 GB", "condition": "Gut", "price_eur": 100.0, "source_url": "u", "date_seen": "2026-01-01", "source_kind": "refurbished-marktplatz"})
    rows.append({"slug": "phone-x", "spec": "128 GB", "condition": "Gut", "price_eur": 0.0, "source_url": "u", "date_seen": "2026-01-01", "source_kind": "refurbished-marktplatz"})
    used = pd.DataFrame(rows)
    models.to_csv(cat / "models.csv", index=False)
    variants.to_csv(cat / "variants.csv", index=False)
    used.to_csv(anc / "used_prices.csv", index=False)
    return cat, anc


def test_helpers() -> None:
    assert parse_storage_gb("256 GB") == 256
    assert parse_storage_gb("1 TB") == 1024
    assert parse_storage_gb("8 GB / 512 GB") == 512
    assert parse_storage_gb("Core i5 / 16 GB / 512 GB SSD") == 512
    assert parse_storage_gb(None) is None
    assert parse_ram_gb("8 GB / 256 GB") == 8
    assert parse_ram_gb("M2 / 16 GB / 1 TB") == 16
    assert parse_ram_gb("256 GB") is None
    assert normalise_grade("Sehr gut") == "B"
    assert normalise_grade("Premium") == "A"
    assert normalise_grade("Gut") == "C"
    assert normalise_grade("Akzeptabel") == "D"
    assert normalise_grade("Trade-in bis zu") == "TRADEIN"
    assert normalise_grade("Grade A") == "A"
    assert normalise_grade(None) == "UNKNOWN"
    assert months_between(date(2023, 9, 22), date(2025, 9, 22)) == pytest.approx(24.0)


def test_load_anchors_joins_and_drops(tmp_path: Path) -> None:
    cat, anc = _write_fixture(tmp_path)
    a = load_anchors(cat, anc)
    # 8 asks + 3 bids + phone-y = 12 rows; the 1 TB offer has no 1 TB variant and is dropped, not
    # divided by the base RRP; no-date, unknown slug and zero price are dropped and counted
    assert len(a) == 12
    assert a.attrs["dropped"] == {"no_model": 1, "no_launch_date": 1, "no_rrp": 0, "no_price": 1, "no_date_seen": 0, "spec_mismatch": 1}
    x = a[(a["slug"] == "phone-x") & (a["spec_used"] == "128 GB")]
    assert (x["match_kind"] == "exact").all()
    assert (x["rrp_eur_launch_de"] == 1000.0).all()
    assert not (a["spec_used"] == "1 TB").any()
    y = a[a["slug"] == "phone-y"].iloc[0]
    assert y["launch_date_de"] == date(2022, 2, 15)  # month-only launch -> 15th
    assert y["realisation"] == pytest.approx(300 / 800)
    assert set(a["grade"]) == {"A", "B", "C", "D", "TRADEIN"}


def test_fit_recovers_slope_and_separates_populations(tmp_path: Path) -> None:
    cat, anc = _write_fixture(tmp_path)
    a = load_anchors(cat, anc)
    c = fit_curves(a, min_n=5, min_age_span=6.0)
    fam = c[(c["group_kind"] == "family") & (c["group"] == "Smartphone") & (c["population"] == "marketplace")].iloc[0]
    assert fam["n"] == 9  # 8 phone-x asks plus phone-y; the 1 TB offer is dropped
    assert fam["slope_per_month"] == pytest.approx(-0.02, abs=0.004)
    assert 0.55 < fam["q_24"] < 0.68
    # one predicted point per contract term (HORIZONS = 12, 24, 36, 48), falling with age on a negative slope
    assert HORIZONS == (12, 24, 36, 48) and {f"q_{h}" for h in HORIZONS} <= set(c.columns)
    assert fam["q_12"] > fam["q_24"] > fam["q_36"] > fam["q_48"] > 0
    assert fam["grade_A_offset"] > 0 > fam["grade_C_offset"] > fam["grade_D_offset"]
    tr = c[(c["group_kind"] == "family") & (c["group"] == "Smartphone") & (c["population"] == "tradein")].iloc[0]
    assert tr["n"] == 3 and tr["fit_quality"] == "no_fit" and pd.isna(tr["q_24"])
    thin = c[(c["group_kind"] == "family_oem") & (c["group"] == "Smartphone / OEM-B")]
    assert (thin["fit_quality"] == "no_fit").all()


def test_run_market_writes_outputs(tmp_path: Path) -> None:
    cat, anc = _write_fixture(tmp_path)
    out = tmp_path / "out"
    res = run_market(out, cat, anc, assumptions=None, as_of=date(2026, 9, 13))
    assert res["anchors"] == 12 and res["dropped"] == 4
    assert (out / "market_anchors.csv").exists() and (out / "market_curves.csv").exists()
    md = (out / "market_summary.md").read_text(encoding="utf-8")
    assert "Where do we land" in md and "placeholder" in md and chr(0x2014) not in md
    assert "q(48)" in md and "after 48" in md, "the summary names the 48-month term like the other three"
    assert "q_48" in pd.read_csv(out / "market_curves.csv").columns
    anchors_out = pd.read_csv(out / "market_anchors.csv")
    assert "realisation_vs_purchase_placeholder" in anchors_out.columns


def test_run_market_without_files(tmp_path: Path) -> None:
    out = tmp_path / "out"
    res = run_market(out, tmp_path / "nocat", tmp_path / "noanc", assumptions=None, as_of=date(2026, 9, 13))
    assert res["anchors"] == 0
    assert "No anchors loaded" in (out / "market_summary.md").read_text(encoding="utf-8")
