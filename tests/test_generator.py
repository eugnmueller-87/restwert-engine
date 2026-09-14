"""Tests for the synthetic generator (SPEC.md section 3.5, tests/test_generator.py)."""

from __future__ import annotations

import importlib.util
import re
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from restwert import config, db, schema  # noqa: E402
from restwert.config import TERM_MONTHS  # noqa: E402
from restwert.dates import months_between_float  # noqa: E402
from restwert.generate import generate_all, run_generate, write_csvs, write_synthetic_md  # noqa: E402
from restwert.generate.truth import true_rv_ratio  # noqa: E402

SMALL_N = 400
LARGE_N = 4000


def _load_denylist() -> list[str] | None:
    """Import tests/fixtures/denylist.py (module 6) if present; None when absent."""
    path = ROOT / "tests" / "fixtures" / "denylist.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("restwert_test_denylist", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = getattr(mod, "DENYLIST", None)
    if not names:
        return None
    return [str(n) for n in names]


@pytest.fixture(scope="module")
def cfg() -> config.GeneratorConfig:
    return config.load_generator_config()


@pytest.fixture(scope="module")
def frames(cfg) -> dict[str, pd.DataFrame]:
    return generate_all(cfg, seed=42, n_devices=SMALL_N)


@pytest.fixture(scope="module")
def frames_large(cfg) -> dict[str, pd.DataFrame]:
    return generate_all(cfg, seed=42, n_devices=LARGE_N)


def _string_values(frames: dict[str, pd.DataFrame]):
    for table, df in frames.items():
        for col in df.columns:
            s = df[col]
            if s.dtype == object or pd.api.types.is_string_dtype(s):
                vals = s.dropna()
                vals = vals[[isinstance(v, str) for v in vals]]
                if len(vals):
                    yield table, col, vals


# --------------------------------------------------------------------------- shape and honesty


def test_generate_all_returns_all_source_tables_in_order(frames):
    assert list(frames) == list(schema.SOURCE_TABLES)
    for table, df in frames.items():
        assert list(df.columns) == list(schema.ROW_MODELS[table].model_fields), table
        assert len(df) > 0, table


def test_is_synthetic_true_on_every_row(frames):
    for table, df in frames.items():
        assert "is_synthetic" in df.columns, table
        assert df["is_synthetic"].astype(bool).all(), table
        assert (df["source_file"] == f"{table}.csv").all(), table


def test_same_seed_identical_frames(cfg):
    a = generate_all(cfg, seed=7, n_devices=SMALL_N)
    b = generate_all(cfg, seed=7, n_devices=SMALL_N)
    assert list(a) == list(b)
    for table in a:
        pd.testing.assert_frame_equal(a[table], b[table], check_dtype=True)


def test_different_seed_different_devices(cfg, frames):
    other = generate_all(cfg, seed=43, n_devices=SMALL_N)
    a = frames["devices"][["purchase_date", "purchase_price", "model", "storage_gb"]].reset_index(drop=True)
    b = other["devices"][["purchase_date", "purchase_price", "model", "storage_gb"]].reset_index(drop=True)
    assert len(a) == len(b)
    assert not a.equals(b)


def test_generate_400_under_5_seconds(cfg):
    t0 = time.perf_counter()
    generate_all(cfg, seed=1, n_devices=SMALL_N)
    assert time.perf_counter() - t0 < 5.0


def test_no_forbidden_company_names(frames):
    denylist = _load_denylist()
    if denylist is None:
        pytest.skip("tests/fixtures/denylist.py (module 6) not present; name test skipped")
    pattern = re.compile("|".join(re.escape(n) for n in denylist), re.IGNORECASE)
    for table, col, vals in _string_values(frames):
        hits = [v for v in vals if pattern.search(v)]
        assert not hits, f"{table}.{col} contains a forbidden name: {hits[:3]}"


def test_names_follow_synthetic_patterns(frames):
    """Positive allowlist: every counterparty and model id has the synthetic shape."""
    supplier_re = re.compile(r"^Supplier-[A-L]$")
    customer_re = re.compile(r"^CUST-\d{4}$")
    model_re = re.compile(r"^[PAL]-Gen\d{2}$")
    for table in ("devices", "purchase_orders", "supplier_contracts", "indirect_spend"):
        assert frames[table]["supplier"].map(lambda v: bool(supplier_re.match(v))).all(), table
    assert frames["rental_contracts"]["customer_id"].map(lambda v: bool(customer_re.match(v))).all()
    for table in ("model_catalogue", "devices", "benchmarks"):
        assert frames[table]["model"].map(lambda v: bool(model_re.match(v))).all(), table
    assert frames["purchase_orders"]["model"].dropna().map(lambda v: bool(model_re.match(v))).all()
    assert (frames["benchmarks"]["source_note"] == "synthetic: generator band midpoint, not a market figure").all()


# --------------------------------------------------------------------------- generation rules


def test_damage_rate_per_family_within_band(cfg, frames_large):
    dev = frames_large["devices"][["serial", "model_family"]]
    rc = frames_large["rental_contracts"].merge(dev, on="serial")
    eff_end = [min(a if a is not None else e, cfg.as_of) for a, e in zip(rc["actual_end_date"], rc["end_date"])]
    years = np.array([(e - s).days for e, s in zip(eff_end, rc["start_date"])], dtype=float).clip(min=0) / 365.25
    rc = rc.assign(years=years)
    dmg = frames_large["events"][frames_large["events"]["event_type"] == "damage"].merge(dev, on="serial")
    for fam in cfg.families:
        exposure = rc.loc[rc["model_family"] == fam, "years"].sum()
        assert exposure > 0
        rate = len(dmg[dmg["model_family"] == fam]) / exposure
        assert 0.06 <= rate <= 0.14, f"{fam}: {rate:.3f} p.a."


def test_refurbishment_days_and_one_row_per_serial(frames):
    rf = frames["refurbishment"]
    diff = [(e - s).days for s, e in zip(rf["start_date"], rf["end_date"])]
    assert list(rf["days"].astype(int)) == diff
    assert not rf["serial"].duplicated().any()
    assert set(rf["outcome"].unique()) <= {"sellable", "as_is", "scrap"}
    assert (rf.loc[rf["outcome"] == "scrap", "grade_out"] == "D").all()
    assert (rf.loc[rf["outcome"] == "as_is", "grade_out"] == "D").all()


def test_resale_at_most_one_row_per_serial_and_consistent(cfg, frames):
    rs = frames["resale"]
    assert not rs["serial"].duplicated().any()
    assert (rs["sale_date"] <= cfg.as_of).all()
    assert (rs["price"] > 0).all()
    assert (rs["fees"] >= 0).all()
    rf = frames["refurbishment"].set_index("serial")
    assert set(rs["serial"]) <= set(rf.index)
    assert (rf.loc[rs["serial"], "outcome"].values != "scrap").all()
    assert (rf.loc[rs["serial"], "end_date"].values <= rs["sale_date"].values).all()
    d_rows = rs[rs["grade_at_sale"] == "D"]
    assert (d_rows["channel"] == "as_is").all()
    assert (rs.loc[rs["grade_at_sale"] != "D", "channel"] != "as_is").all()
    for ch, fee_pct in cfg.fee_pct.items():
        sub = rs[rs["channel"] == ch]
        if len(sub):
            expected = (sub["price"] * fee_pct + cfg.fee_fixed_eur[ch]).round(2)
            assert np.allclose(sub["fees"].astype(float), expected.astype(float), atol=0.011)


def test_referential_integrity_and_ids(frames):
    dev = frames["devices"]
    assert not dev["serial"].duplicated().any()
    assert dev["serial"].str.match(r"^D-\d{6}$").all()
    assert set(dev["model"]) <= set(frames["model_catalogue"]["model"])
    assert set(dev["po_number"]) <= set(frames["purchase_orders"]["po_number"])
    for t in ("rental_contracts", "events", "refurbishment", "resale"):
        assert set(frames[t]["serial"]) <= set(dev["serial"]), t
    assert frames["rental_contracts"]["contract_id"].str.match(r"^RC-\d{6}$").all()
    assert frames["events"]["event_id"].str.match(r"^EV-\d{6}$").all()
    assert frames["refurbishment"]["refurb_id"].str.match(r"^RF-\d{6}$").all()
    assert frames["resale"]["sale_id"].str.match(r"^S-\d{6}$").all()
    assert frames["supplier_contracts"]["supplier_contract_id"].str.match(r"^SC-\d{2}$").all()
    assert frames["purchase_orders"]["po_number"].str.match(r"^PO-\d{6}$").all()
    assert frames["indirect_spend"]["spend_id"].str.match(r"^IS-\d{6}$").all()
    db.referential_checks(frames)


def test_devices_prices_and_launch_consistency(cfg, frames):
    dev = frames["devices"]
    cat = frames["model_catalogue"].set_index("model")
    assert (dev["launch_date"].values == cat.loc[dev["model"], "launch_date"].values).all()
    assert (dev["launch_date"] < dev["purchase_date"]).all()
    for fam, fc in cfg.families.items():
        sub = dev[dev["model_family"] == fam]
        lp = cat.loc[sub["model"], "list_price"].values.astype(float)
        disc = 1 - sub["purchase_price"].values.astype(float) / lp
        assert disc.min() >= fc.discount_min - 1e-6 and disc.max() <= fc.discount_max + 1e-6
        expected_landed = np.round(sub["purchase_price"].values.astype(float) * (1 + fc.freight_duty_pct), 2)
        assert np.allclose(sub["landed_cost"].values.astype(float), expected_landed, atol=0.011)
        assert set(sub["storage_gb"]) <= set(fc.storage_options)
    n_spare = (dev["contract_id"].isna()).sum()
    assert len(dev) > SMALL_N and len(dev) <= SMALL_N + int(np.ceil(SMALL_N * 0.05)) + len(cfg.families)
    first_contract = frames["rental_contracts"].sort_values("start_date").drop_duplicates("serial")
    deployed = dev[dev["contract_id"].notna()]
    mapped = deployed.merge(first_contract[["serial", "contract_id"]], on="serial", suffixes=("", "_rc"))
    assert (mapped["contract_id"] == mapped["contract_id_rc"]).all()
    assert n_spare <= int(np.ceil(SMALL_N * 0.05)) + len(cfg.families)


def test_rental_contract_term_mix_shares(cfg):
    """With four terms in ``term_mix`` every term is drawn and the shares land within 5 points at 4000 devices."""
    from restwert.generate.rentals import build_rental_contracts

    n = 4000
    for fam, fc in cfg.families.items():
        assert set(fc.term_mix) == set(TERM_MONTHS), fam
        devices = pd.DataFrame(
            {
                "serial": [f"D-{i:06d}" for i in range(n)],
                "model_family": [fam] * n,
                "purchase_date": [date(2023, 1, 1)] * n,
                "landed_cost": [800.0] * n,
            }
        )
        rc = build_rental_contracts(cfg, devices, np.random.default_rng(42))
        assert len(rc) == n
        counts = rc["term_months"].value_counts()
        assert set(counts.index) == set(TERM_MONTHS), (fam, dict(counts))
        for term, share in fc.term_mix.items():
            observed = counts.get(term, 0) / n
            assert abs(observed - share) <= 0.05, (fam, term, observed, share)
        # the same seed gives the same terms (determinism), another seed differs somewhere
        again = build_rental_contracts(cfg, devices, np.random.default_rng(42))
        assert list(again["term_months"]) == list(rc["term_months"])
        other = build_rental_contracts(cfg, devices, np.random.default_rng(43))
        assert list(other["term_months"]) != list(rc["term_months"])
    # a two-term legacy mix draws exactly those two terms
    legacy = cfg.model_copy(deep=True)
    fam = next(iter(legacy.families))
    legacy.families[fam] = legacy.families[fam].model_copy(update={"term_mix": {24: 0.7, 36: 0.3}})
    devices = pd.DataFrame({"serial": [f"L-{i:04d}" for i in range(500)], "model_family": [fam] * 500,
                            "purchase_date": [date(2023, 1, 1)] * 500, "landed_cost": [800.0] * 500})
    rc = build_rental_contracts(legacy, devices, np.random.default_rng(1))
    assert set(rc["term_months"]) == {24, 36}


def test_rental_contract_rules(cfg, frames):
    rc = frames["rental_contracts"]
    dev = frames["devices"].set_index("serial")
    assert set(rc["term_months"]) <= set(TERM_MONTHS)   # 12, 24, 36, 48 (the shares are tested in test_rental_contract_term_mix_shares)
    from restwert.dates import add_months

    planned = [add_months(s, int(t)) for s, t in zip(rc["start_date"], rc["term_months"])]
    initial = rc[rc["replaces_contract_id"].isna()]
    assert all(e == p for e, p, r in zip(rc["end_date"], planned, rc["replaces_contract_id"]) if r is None)
    gap = [(s - dev.loc[ser, "purchase_date"]).days for s, ser in zip(initial["start_date"], initial["serial"])]
    assert min(gap) >= 3 and max(gap) <= 30
    rates = [round(float(dev.loc[ser, "landed_cost"]) * cfg.families[dev.loc[ser, "model_family"]].monthly_rate_pct_of_landed
                   * float(cfg.term_rate_factor[int(term)]), 2)
             for ser, term in zip(initial["serial"], initial["term_months"])]
    assert np.allclose(initial["monthly_rate"].astype(float), rates, atol=0.011)
    active = rc[rc["status"] == "active"]
    assert (active["end_date"] > cfg.as_of).all() and active["actual_end_date"].isna().all()
    ended = rc[rc["status"] == "ended"]
    assert (ended["end_date"] <= cfg.as_of).all()
    early = rc[rc["status"] == "terminated_early"]
    assert early["actual_end_date"].notna().all() and (early["actual_end_date"] <= cfg.as_of).all()
    replaced = rc[rc["status"] == "replaced"]
    repl_contracts = rc[rc["replaces_contract_id"].notna()]
    assert len(replaced) == len(repl_contracts)
    assert set(repl_contracts["replaces_contract_id"]) == set(replaced["contract_id"])
    joined = repl_contracts.merge(replaced, left_on="replaces_contract_id", right_on="contract_id", suffixes=("_new", "_old"))
    assert (joined["customer_id_new"] == joined["customer_id_old"]).all()
    assert (joined["end_date_new"] == joined["end_date_old"]).all()
    assert (joined["start_date_new"] <= joined["actual_end_date_old"]).all()
    spare_serials = set(repl_contracts["serial"])
    assert all(dev.loc[s, "contract_id"] in set(repl_contracts["contract_id"]) for s in spare_serials)


def test_event_semantics(cfg, frames):
    ev = frames["events"]
    dmg = ev[ev["event_type"] == "damage"]
    assert dmg["resolved"].notna().all()
    assert (dmg.loc[dmg["resolved"] == True, "cost"] == 0).all()  # noqa: E712
    open_q = dmg[dmg["resolved"] == False]  # noqa: E712
    assert (open_q["cost"] > 0).all()
    assert set(dmg["damage_type"].unique()) <= {"screen", "battery", "housing", "water", "other"}
    rep = ev[ev["event_type"] == "repair"]
    assert (rep["cost"] > 0).all()
    repl = ev[ev["event_type"] == "replacement"]
    assert (repl["cost"] == 18.0).all()
    assert repl["replacement_serial"].notna().all()
    ret = ev[ev["event_type"] == "return"]
    assert ret["return_date"].notna().all()
    assert (ret["return_date"] == ret["event_date"]).all()
    assert (ret["cost"] == 9.5).all()
    assert ret["grade_pre_return"].notna().all() and ret["grade_inspected"].notna().all()
    assert ret["wipe_certificate"].notna().all()
    assert not ret["serial"].duplicated().any()
    rc = frames["rental_contracts"]
    ended_serials = set(rc.loc[(rc["status"] != "active"), "serial"])
    assert set(ret["serial"]) == ended_serials
    replaced_serials = set(rc.loc[rc["status"] == "replaced", "serial"])
    assert set(repl["serial"]) == replaced_serials
    assert set(repl["replacement_serial"]) <= set(rc.loc[rc["replaces_contract_id"].notna(), "serial"])
    ins = ret[["grade_pre_return", "grade_inspected"]]
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    steps = ins["grade_inspected"].map(order) - ins["grade_pre_return"].map(order)
    assert set(steps.unique()) <= {-1, 0, 1}


def test_refurbishment_follows_returns(cfg, frames):
    rf = frames["refurbishment"]
    ev = frames["events"]
    ret = ev[ev["event_type"] == "return"].set_index("serial")
    returned_by_as_of = set(ret.index[ret["return_date"] <= cfg.as_of])
    assert set(rf["serial"]) == returned_by_as_of
    lag = [(s - ret.loc[ser, "return_date"]).days for s, ser in zip(rf["start_date"], rf["serial"])]
    assert min(lag) >= 1 and max(lag) <= 5
    # cost and days are drawn on the inspected grade (grade_out may be one grade better)
    grade_in = ret.loc[rf["serial"], "grade_inspected"].values
    for g in cfg.refurb_days:
        sub = rf[grade_in == g]
        if len(sub):
            lo_d, hi_d = cfg.refurb_days[g]
            lo_c, hi_c = cfg.refurb_cost[g]
            assert sub["days"].min() >= lo_d and sub["days"].max() <= hi_d
            assert sub["cost"].min() >= lo_c - 0.011 and sub["cost"].max() <= hi_c + 0.011
            order = "ABCD"
            assert all(order.index(o) in (order.index(g), max(order.index(g) - 1, 0)) for o in sub["grade_out"])


def test_purchase_orders_and_benchmarks(cfg, frames):
    po = frames["purchase_orders"]
    dev = frames["devices"]
    counts = dev.groupby("po_number").size()
    assert (po.set_index("po_number")["qty_ordered"].loc[counts.index].values == counts.values).all()
    assert (po["qty_delivered"] <= po["qty_ordered"]).all() and (po["qty_delivered"] >= 1).all()
    assert (po["promised_date"] > po["order_date"]).all()
    assert (po["delivered_date"] >= po["promised_date"]).all()
    late = (po["delivered_date"] > po["promised_date"]).mean()
    assert 0.05 < late < 0.50
    cat = frames["model_catalogue"].set_index("model")
    assert np.allclose(po["benchmark_price"].astype(float), (cat.loc[po["model"], "list_price"].values.astype(float) * 0.85).round(2), atol=0.011)
    dropped = po[po["price_drop_date"].notna()]
    assert (dropped["price_drop_date"] > dropped["delivered_date"]).all()
    assert np.allclose(dropped["price_drop_amount"].astype(float), (dropped["unit_price"] * cfg.price_drop_pct).round(2), atol=0.011)
    sc = frames["supplier_contracts"].set_index("supplier_contract_id")
    linked = po[po["supplier_contract_id"].notna()]
    assert (sc.loc[linked["supplier_contract_id"], "supplier"].values == linked["supplier"].values).all()
    assert (sc.loc[linked["supplier_contract_id"], "start_date"].values <= linked["order_date"].values).all()
    assert (sc.loc[linked["supplier_contract_id"], "end_date"].values >= linked["order_date"].values).all()
    bm = frames["benchmarks"]
    assert len(bm) == len(cat)
    assert np.allclose(bm["landed_cost_benchmark"].astype(float), (cat.loc[bm["model"], "list_price"].values.astype(float) * 0.85 * 1.03).round(2), atol=0.011)


def test_supplier_contracts_and_indirect_spend(cfg, frames):
    sc = frames["supplier_contracts"]
    hw = sc[sc["category"] == "hardware"]
    assert len(hw) == len(cfg.suppliers_hardware) and set(hw["supplier"]) == set(cfg.suppliers_hardware)
    assert hw["price_protection"].sum() == 4
    assert hw.loc[hw["price_protection"], "price_protection_days"].isin([30, 45, 60]).all()
    assert hw.loc[~hw["price_protection"], "price_protection_days"].isna().all()
    assert sc["payment_terms_days"].isin([30, 45, 60]).all()
    ind = sc[sc["category"] != "hardware"]
    assert set(ind["category"]) == set(cfg.indirect_categories) and set(ind["supplier"]) <= set(cfg.suppliers_indirect)
    assert (sc["start_date"] <= cfg.as_of).all()
    assert (sc["end_date"] > sc["start_date"]).all()
    from restwert.dates import add_months

    horizon = add_months(cfg.as_of, 6)
    soon = ((sc["end_date"] > cfg.as_of) & (sc["end_date"] <= horizon)).sum()
    expired = (sc["end_date"] < cfg.as_of).sum()
    assert 2 <= soon <= 6 and 1 <= expired <= 5
    assert 2 <= ((hw["end_date"] > cfg.as_of) & (hw["end_date"] <= horizon)).sum() <= 4

    isp = frames["indirect_spend"]
    n_months = (cfg.as_of.year - cfg.history_start.year) * 12 + cfg.as_of.month - cfg.history_start.month + 1
    assert len(isp) == n_months * cfg.indirect_rows_per_month
    assert (isp["invoice_date"] >= cfg.history_start).all() and (isp["invoice_date"] <= cfg.as_of).all()
    assert set(isp["category"]) <= set(cfg.indirect_categories)
    assert (isp["amount"] > 0).all()
    assert abs(isp["has_po"].mean() - cfg.indirect_has_po) < 0.06
    assert abs(isp["has_contract"].mean() - cfg.indirect_has_contract) < 0.08
    saving_rows = isp[isp["saving"] > 0]
    assert (isp.loc[isp["saving"] == 0, "saving_confirmed_by_controlling"] == False).all()  # noqa: E712
    assert abs(len(saving_rows) / len(isp) - cfg.indirect_saving_share) < 0.05
    ratio = saving_rows["saving"] / saving_rows["amount"]
    assert ratio.min() >= 0.03 - 0.01 and ratio.max() <= 0.12 + 0.01


def test_resale_prices_follow_hidden_truth_curve(cfg, frames_large):
    """Realised prices average close to the noise-free truth ratio (median of the lognormal noise)."""
    rs = frames_large["resale"].merge(frames_large["devices"], on="serial")
    cat = frames_large["model_catalogue"]
    launches = {fam: sorted(grp["launch_date"]) for fam, grp in cat.groupby("model_family")}
    truth = []
    for _, r in rs.iterrows():
        msl = months_between_float(r["launch_date"], r["sale_date"])
        n_l = sum(1 for l in launches[r["model_family"]] if r["launch_date"] < l <= r["sale_date"])
        truth.append(true_rv_ratio(r["model_family"], msl, n_l, r["grade_at_sale"], int(r["storage_gb"]), r["channel"], cfg))
    rs = rs.assign(truth=truth, ratio=rs["price"].astype(float) / rs["purchase_price"].astype(float))
    log_err = np.log(rs["ratio"]) - np.log(rs["truth"])
    assert abs(log_err.mean()) < 0.03
    assert 0.05 < log_err.std() < 0.20


def test_true_rv_ratio_properties(cfg):
    a = true_rv_ratio("iphone_like", 12, 1, "A", 128, "marketplace", cfg)
    b = true_rv_ratio("iphone_like", 24, 1, "A", 128, "marketplace", cfg)
    c = true_rv_ratio("iphone_like", 24, 2, "A", 128, "marketplace", cfg)
    d = true_rv_ratio("iphone_like", 24, 2, "B", 128, "marketplace", cfg)
    e = true_rv_ratio("iphone_like", 24, 2, "B", 256, "marketplace", cfg)
    f = true_rv_ratio("iphone_like", 24, 2, "B", 256, "b2b_wholesale", cfg)
    assert a > b > c > d and e > d and f < e
    assert 0.02 <= true_rv_ratio("android_like", 400, 10, "D", 128, "as_is", cfg) <= 1.10
    assert true_rv_ratio("iphone_like", 0, 0, "A", 128, "marketplace", cfg, noise=5.0) == 1.10


# --------------------------------------------------------------------------- files


def test_write_csvs_and_synthetic_md(cfg, frames, tmp_path: Path):
    out = tmp_path / "raw_csv"
    paths = write_csvs(frames, out, seed=42)
    assert len(paths) == len(schema.SOURCE_TABLES)
    for p in paths:
        raw = p.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{p.name} has a BOM"
        first = raw.split(b"\n", 1)[0].decode("utf-8")
        assert first.startswith("# SYNTHETIC DATA"), p.name
        assert "seed=42" in first
        second = raw.split(b"\n", 2)[1].decode("utf-8")
        assert "is_synthetic" in second.split(",")
    dev_text = (out / "devices.csv").read_text(encoding="utf-8").splitlines()
    assert re.search(r",\d{4}-\d{2}-\d{2},", dev_text[2])
    counts = {t: len(df) for t, df in frames.items()}
    md = write_synthetic_md(tmp_path, cfg, 42, counts)
    assert md == tmp_path / "SYNTHETIC.md"
    text = md.read_text(encoding="utf-8")
    assert "seed: `42`" in text
    assert "No market benchmark, no customer, no supplier and no employer is real." in text
    assert "| `devices` | 420 |" in text or f"| `devices` | {counts['devices']} |" in text
    assert "0.2.0" in text  # the package version named in SYNTHETIC.md (bumped by v0.2)
    assert chr(0x2014) not in text  # no em dash in generated prose


def test_load_csv_dir_roundtrip_with_validation(frames, tmp_path: Path):
    out = tmp_path / "raw_csv"
    write_csvs(frames, out, seed=42)
    con = db.connect(":memory:")
    db.create_schema(con)
    counts = db.load_csv_dir(con, out)
    for table, df in frames.items():
        assert counts[table] == len(df), table
        assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == len(df)
    assert db.is_synthetic(con) is True
    back = db.read_df(con, "SELECT serial, purchase_date, purchase_price, contract_id FROM devices ORDER BY serial")
    src = frames["devices"].sort_values("serial")
    assert list(back["serial"]) == list(src["serial"])
    assert np.allclose(back["purchase_price"].astype(float), src["purchase_price"].astype(float))
    assert [pd.Timestamp(d).date() for d in back["purchase_date"]] == list(src["purchase_date"])
    assert back["contract_id"].isna().sum() == src["contract_id"].isna().sum()
    ev = db.read_df(con, "SELECT count(*) FILTER (WHERE resolved = false) AS open_q, count(*) FILTER (WHERE resolved IS NULL) AS nulls FROM events").iloc[0]
    src_ev = frames["events"]
    assert int(ev["open_q"]) == int((src_ev["resolved"] == False).sum())  # noqa: E712
    assert int(ev["nulls"]) == int(src_ev["resolved"].isna().sum())


def test_load_csv_dir_refuses_missing_is_synthetic(frames, tmp_path: Path):
    out = tmp_path / "raw_csv"
    write_csvs({"model_catalogue": frames["model_catalogue"]}, out, seed=42)
    p = out / "model_catalogue.csv"
    lines = p.read_text(encoding="utf-8").splitlines()
    header = lines[1].split(",")
    idx = header.index("is_synthetic")
    new_lines = [lines[0]] + [",".join(v for i, v in enumerate(l.split(",")) if i != idx) for l in lines[1:]]
    p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    con = db.connect(":memory:")
    db.create_schema(con)
    with pytest.raises(ValueError, match="is_synthetic"):
        db.load_csv_dir(con, out)


def test_load_csv_dir_referential_check_and_mixed_flag(frames, tmp_path: Path):
    out = tmp_path / "raw_csv"
    sub = {"model_catalogue": frames["model_catalogue"], "devices": frames["devices"], "purchase_orders": frames["purchase_orders"]}
    write_csvs(sub, out, seed=42)
    con = db.connect(":memory:")
    db.create_schema(con)
    assert db.load_csv_dir(con, out)["devices"] == len(frames["devices"])
    # break a foreign key
    broken = frames["devices"].copy()
    broken.loc[broken.index[0], "model"] = "Z-Gen99"
    write_csvs({"devices": broken}, out, seed=42)
    with pytest.raises(ValueError, match="referential check failed: devices.model"):
        db.load_csv_dir(con, out)
    # mixed synthetic flags across files
    write_csvs({"devices": frames["devices"]}, out, seed=42)
    real_cat = frames["model_catalogue"].copy()
    real_cat["is_synthetic"] = False
    write_csvs({"model_catalogue": real_cat}, out, seed=42)
    with pytest.raises(ValueError, match="differs across files"):
        db.load_csv_dir(con, out)
    counts = db.load_csv_dir(con, out, allow_mixed=True)
    assert counts["model_catalogue"] == len(real_cat)


def test_run_generate_writes_everything(cfg, tmp_path: Path):
    out = tmp_path / "data" / "raw_csv"
    summary = run_generate(cfg, seed=3, n_devices=120, out_dir=out)
    assert summary.command == "generate"
    assert summary.counts["devices"] >= 120
    assert (tmp_path / "data" / "SYNTHETIC.md").exists()
    assert (out / "devices.csv").exists()
    assert summary.seconds < 5.0


def test_launch_slip_stays_inside_bounds_and_monotone(cfg, frames):
    from restwert.dates import add_months

    cat = frames["model_catalogue"]
    slip_max = int(cfg.launch_slip_months_max)
    slipped_total = 0
    for fam, fc in cfg.families.items():
        sub = cat[cat["model_family"] == fam].sort_values("generation")
        dates = list(sub["launch_date"])
        assert dates == sorted(dates) and len(set(dates)) == len(dates)
        assert dates[0] == fc.first_launch
        if fc.launch_cadence_months is None:
            continue
        slipped = 0
        for gen, d in zip(sub["generation"], dates):
            rule = add_months(fc.first_launch, (int(gen) - 1) * fc.launch_cadence_months)
            off = (d.year - rule.year) * 12 + (d.month - rule.month)
            assert abs(off) <= slip_max, (fam, gen, d, rule)
            slipped += off != 0
        slipped_total += slipped
    if slip_max > 0:
        assert slipped_total > 0, "with slip enabled some launch should deviate from the calendar"
    exact = cfg.model_copy(update={"launch_slip_months_max": 0})
    cat0 = generate_all(exact, seed=42, n_devices=SMALL_N)["model_catalogue"]
    for fam, fc in exact.families.items():
        sub = cat0[cat0["model_family"] == fam].sort_values("generation")
        if fc.launch_cadence_months is None:
            continue
        for gen, d in zip(sub["generation"], sub["launch_date"]):
            assert d == add_months(fc.first_launch, (int(gen) - 1) * fc.launch_cadence_months)


def test_indirect_savings_are_typed_with_a_baseline(cfg, frames):
    isp = frames["indirect_spend"]
    saving_rows = isp[isp["saving"] > 0]
    none_rows = isp[isp["saving"] == 0]
    assert set(saving_rows["saving_type"]) <= {"hard_price_reduction", "cost_avoidance", "rebate"}
    assert saving_rows["saving_type"].notna().all()
    assert none_rows["saving_type"].isna().all() and none_rows["baseline_amount"].isna().all()
    assert np.allclose(saving_rows["baseline_amount"].astype(float), saving_rows["amount"].astype(float) + saving_rows["saving"].astype(float), atol=0.011)
    hard = (saving_rows["saving_type"] == "hard_price_reduction").mean()
    assert abs(hard - cfg.indirect_saving_type_mix["hard_price_reduction"]) < 0.12


def test_rental_monthly_rate_scales_with_the_term_rate_factor(cfg):
    """The monthly rate is landed cost x the family rate x ``term_rate_factor[term]`` (24 months = 1.0).

    A flat rate per month made every 12-month contract lose money by construction (rent for a
    year cannot cover a year of depreciation plus the fixed costs to sale); the factor is the
    business owner's placeholder for the term pricing, validated in GeneratorConfig.
    """
    import pytest

    from restwert.config import GeneratorConfig
    from restwert.generate.rentals import build_rental_contracts

    n = 2000
    fam = next(iter(cfg.families))
    devices = pd.DataFrame(
        {
            "serial": [f"D-{i:06d}" for i in range(n)],
            "model_family": [fam] * n,
            "purchase_date": [date(2023, 1, 1)] * n,
            "landed_cost": [1000.0] * n,
        }
    )
    rc = build_rental_contracts(cfg, devices, np.random.default_rng(7))
    base = 1000.0 * cfg.families[fam].monthly_rate_pct_of_landed
    for term, factor in cfg.term_rate_factor.items():
        rows = rc[rc["term_months"] == term]
        if rows.empty:
            continue
        assert (rows["monthly_rate"] == round(base * factor, 2)).all(), (term, factor, rows["monthly_rate"].unique())
    assert cfg.term_rate_factor[24] == 1.0
    assert cfg.term_rate_factor[12] > cfg.term_rate_factor[24] > cfg.term_rate_factor[36] > cfg.term_rate_factor[48]
    # validation: every term named, 24 is the base, nothing non-positive
    raw = cfg.model_dump()
    for bad in ({12: 1.5, 24: 1.1, 36: 0.9, 48: 0.8}, {24: 1.0, 36: 0.9}, {12: 0.0, 24: 1.0, 36: 0.9, 48: 0.8}, {12: 1.5, 24: 1.0, 36: 0.9, 48: 0.8, 60: 0.7}):
        with pytest.raises(ValueError):
            GeneratorConfig(**{**raw, "term_rate_factor": bad})
