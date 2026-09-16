"""Tests of the lake generator (SPEC_v0.2 section 5.6, module 2).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

A session-scoped world of 1200 serials is generated once into a temporary lake and
shared by the file-level tests; the reproducibility and timing tests generate their
own smaller fleets.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from restwert.lake.common import MANUFACTURERS, ROLE_ONLY_SUFFIX, allocate_cents
from restwert.lakegen import World, generate_world, load_curves, load_fleet_catalogue, load_lake_config, run_generate_lake
from restwert.lakegen.calibrate import TruthCurve, default_curve, q_public, true_price, truth_curve
from restwert.lakegen.catalogue import latest_slugs
from restwert.lakegen.config import LAKE_CONFIG, LakeConfig
from restwert.lakegen.defects import DEFECT_KINDS
from restwert.lakegen.writer import (
    FEED_KEYS,
    FEEDS,
    PLANNED_FUTURE_COLUMNS,
    SERIAL_NAME_COLUMNS,
    SYNTHETIC_SENTENCE,
    delivery_periods,
)
from restwert.pnl.lifecycle import months_billed
from tests.fixtures.denylist import find_denylisted

SESSION_SERIALS = 1200
EM_DASH = chr(0x2014)  # built from its code point so this file does not trip the em-dash scan


@dataclass(frozen=True)
class Lake:
    cfg: LakeConfig
    world: World
    raw_dir: Path
    lake_dir: Path
    files: list[Path]
    seconds: float


def _read_landing(path: Path) -> tuple[str, pd.DataFrame]:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        first = fh.readline().rstrip("\n")
        df = pd.read_csv(fh, dtype=str, keep_default_na=False)
    return first, df


def _feed_key_of(path: Path) -> str:
    return f"{path.parent.parent.name}/{path.parent.name}"


def _delivered_on(path: Path) -> date:
    return date.fromisoformat(path.name[:10])


def _originals(df: pd.DataFrame) -> pd.DataFrame:
    """A world frame without the injected duplicate copies (hidden ``_delivery_shift`` column)."""
    if "_delivery_shift" in df.columns:
        return df[df["_delivery_shift"] == 0]
    return df


def _all_rows(files: list[Path], key: str) -> pd.DataFrame:
    frames = [_read_landing(p)[1] for p in files if _feed_key_of(p) == key]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


@pytest.fixture(scope="session")
def lake_cfg() -> LakeConfig:
    return load_lake_config()


@pytest.fixture(scope="session")
def session_lake(tmp_path_factory, lake_cfg) -> Lake:
    """One 1200-serial lake per session, generated through the public entry point."""
    base = tmp_path_factory.mktemp("lakegen")
    raw_dir = base / "lake" / "raw"
    t0 = time.perf_counter()
    cat = load_fleet_catalogue(vat_rate=0.19)
    curves = load_curves()
    world = generate_world(lake_cfg, cat, curves, seed=7, n_serials=SESSION_SERIALS)
    from restwert.lakegen.writer import render_synthetic_md, write_landing_files, write_manifest, write_synthetic_md

    cfg_run = lake_cfg.model_copy(update={"seed": 7, "n_devices": SESSION_SERIALS}, deep=True)
    files = write_landing_files(world, raw_dir, cfg_run, 7)
    write_manifest(raw_dir, files, 7)
    write_synthetic_md(raw_dir.parent, render_synthetic_md(world, cfg_run, files))
    return Lake(cfg=cfg_run, world=world, raw_dir=raw_dir, lake_dir=raw_dir.parent, files=files,
                seconds=time.perf_counter() - t0)


# --------------------------------------------------------------------------- config and catalogue


def test_lake_config_loads_and_validates(lake_cfg, assumptions):
    assert LAKE_CONFIG.name == "lake.yaml"
    assert tuple(sorted(lake_cfg.families)) == ("android_like", "iphone_like", "laptop_like", "tablet_like")
    assert abs(sum(lake_cfg.fleet_mix.values()) - 1.0) < 1e-9
    for fam, shares in lake_cfg.oem_share.items():
        assert abs(sum(shares.values()) - 1.0) < 1e-9, fam
        assert set(shares) <= set(MANUFACTURERS)
    assert abs(sum(lake_cfg.supplier_route.values()) - 1.0) < 1e-9
    fees = assumptions.get("channel_fees")
    for channel, block in fees.items():
        assert lake_cfg.fee_pct[channel] == pytest.approx(block["fee_pct"])
        assert lake_cfg.fee_fixed_eur[channel] == pytest.approx(block["fee_fixed_eur"])
        assert lake_cfg.days_to_cash[channel] == int(block["days_to_cash"])
    assert set(lake_cfg.discount_by_oem) == set(MANUFACTURERS)
    assert lake_cfg.families["iphone_like"].launch_cadence_months == 4
    assert lake_cfg.families["android_like"].launch_cadence_months == 1
    assert lake_cfg.families["tablet_like"].launch_cadence_months == 2
    assert lake_cfg.families["laptop_like"].launch_cadence_months == 2
    assert all(s.endswith(ROLE_ONLY_SUFFIX) for s in lake_cfg.suppliers_indirect)
    assert all(r.endswith(ROLE_ONLY_SUFFIX) for r in lake_cfg.resellers)
    text = LAKE_CONFIG.read_text(encoding="utf-8")
    assert EM_DASH not in text
    assert "DESIGN PARAMETER" in text


def test_lake_config_rejects_bad_mixes(lake_cfg):
    raw = lake_cfg.model_dump()
    raw["fleet_mix"] = {"Smartphone": 0.5, "Tablet": 0.5, "Laptop": 0.5}
    with pytest.raises(ValueError):
        LakeConfig.model_validate(raw)
    raw = lake_cfg.model_dump()
    raw["oem_share"]["Tablet"] = {"Apple": 0.5, "Some other brand": 0.5}
    with pytest.raises(ValueError):
        LakeConfig.model_validate(raw)
    raw = lake_cfg.model_dump()
    raw["families"].pop("tablet_like")
    with pytest.raises(ValueError):
        LakeConfig.model_validate(raw)


def test_fleet_catalogue_uses_210_usable_slugs_and_lists_excluded():
    # catalogue round 4 (2026-09-14) appended 42 models of 2020 to 2022 (data/catalogue/README.md):
    # 191 -> 233 rows, 167 -> 208 usable (the two HP 2021 generations have no priced variant).
    # Laptop research 2026-09-16 priced the Dell Latitude 5430 (i7) and the HP ProBook 450 G8 (16 GB / 512 GB): 208 -> 210 usable
    cat = load_fleet_catalogue(vat_rate=0.19)
    assert len(cat.models) == 233
    assert cat.n_usable == 210
    reasons = set(cat.excluded["reason"])
    assert reasons <= {"no_launch_date", "no_priced_variant", "no_storage_on_priced_variant"}
    n_hard = int((cat.excluded["reason"] != "no_storage_on_priced_variant").sum())
    assert n_hard == 233 - 210
    assert set(cat.pool["slug"]) <= set(cat.models.loc[cat.models["usable"], "slug"])
    assert cat.pool["storage_gb"].notna().all()
    assert (cat.pool["rrp_net"] < cat.pool["rrp_gross"]).all()
    row = cat.pool.iloc[0]
    assert row["rrp_net"] == pytest.approx(round(row["rrp_gross"] / 1.19, 2))
    assert set(cat.pool["fleet_family"]) == {"iphone_like", "android_like", "tablet_like", "laptop_like"}
    newest = latest_slugs(cat.pool, "Smartphone", "Apple", date(2024, 1, 1))
    assert newest and newest[0].startswith("iphone-15")
    assert latest_slugs(cat.pool, "Laptop", "Samsung", date(2022, 1, 1)) == []


# --------------------------------------------------------------------------- fleet: model generation rule


def _mini_pool(rows: list[tuple[str, str, str, date]]) -> pd.DataFrame:
    """A catalogue pool of ``(slug, family, oem, launch_date)`` rows, one 128 GB variant each."""
    from restwert.lakegen.catalogue import POOL_COLUMNS

    out = []
    for slug, family, oem, launch in rows:
        out.append({
            "slug": slug, "model_name": slug.replace("-", " ").title(), "oem": oem, "family": family, "series": None,
            "launch_date": launch, "successor_launch_date": None, "spec": "128 GB", "storage_gb": 128,
            "rrp_gross": 1190.0, "rrp_net": 1000.0, "fleet_family": "iphone_like", "base_storage_gb": 128,
        })
    return pd.DataFrame(out, columns=list(POOL_COLUMNS))


def test_slug_index_generations_by_launch_window():
    """The window is anchored on the newest launch: current = (newest - window, newest], previous = the window before, older = the rest."""
    from restwert.lakegen.fleet import _SlugIndex

    pool = _mini_pool([
        ("old-1", "Smartphone", "Apple", date(2020, 3, 1)),
        ("prev-1", "Smartphone", "Apple", date(2021, 9, 24)),
        ("cur-pro", "Smartphone", "Apple", date(2022, 9, 16)),
        ("cur-pro-max", "Smartphone", "Apple", date(2022, 9, 16)),
        ("cur-plus", "Smartphone", "Apple", date(2022, 10, 7)),   # three weeks after the other two
    ])
    idx = _SlugIndex(pool)
    # inside the year after the launches: all three of the generation are current, the 2021 slug previous, 2020 older
    cur, prev, older = idx.generations("Smartphone", "Apple", date(2023, 1, 15), 12)
    assert set(cur) == {"cur-pro", "cur-pro-max", "cur-plus"} and prev == ["prev-1"] and older == ["old-1"]
    # nothing launched inside the last 12 months before the order: the sets do NOT change, the window hangs on the
    # newest launch (2022-10-07), not on the order date; the newest slug never stands alone
    cur, prev, older = idx.generations("Smartphone", "Apple", date(2024, 6, 1), 12)
    assert set(cur) == {"cur-pro", "cur-pro-max", "cur-plus"} and prev == ["prev-1"] and older == ["old-1"]
    cur, prev, older = idx.generations("Smartphone", "Apple", date(2025, 1, 1), 12)
    assert set(cur) == {"cur-pro", "cur-pro-max", "cur-plus"} and prev == ["prev-1"] and older == ["old-1"]
    # before the 2022 launches: newest is 2021-09-24, the 2020 slug lies in the window before it
    cur, prev, older = idx.generations("Smartphone", "Apple", date(2022, 3, 1), 12)
    assert cur == ["prev-1"] and prev == ["old-1"] and older == []
    # two slugs launched on the same day are one generation, whatever the order date
    twins = _SlugIndex(_mini_pool([("a-1", "Tablet", "Apple", date(2022, 10, 26)), ("a-2", "Tablet", "Apple", date(2022, 10, 26))]))
    assert twins.generations("Tablet", "Apple", date(2025, 1, 1), 12) == (["a-2", "a-1"], [], [])
    # only one slug launched: current alone, nothing else
    cur, prev, older = idx.generations("Smartphone", "Apple", date(2020, 6, 1), 12)
    assert cur == ["old-1"] and prev == [] and older == []
    # nothing launched yet, or an unknown manufacturer
    assert idx.generations("Smartphone", "Apple", date(2020, 1, 1), 12) == ([], [], [])
    assert idx.generations("Smartphone", "Samsung", date(2024, 1, 1), 12) == ([], [], [])
    # newest first inside every set
    cur, prev, older = idx.generations("Smartphone", "Apple", date(2023, 10, 1), 12)
    assert cur == ["cur-plus", "cur-pro-max", "cur-pro"] and prev == ["prev-1"] and older == ["old-1"]
    # an empty previous window stays empty (no next-older fallback): a 2-year cadence with a 12-month window
    gap = _SlugIndex(_mini_pool([
        ("g-2019", "Laptop", "Lenovo", date(2019, 6, 1)),
        ("g-2021", "Laptop", "Lenovo", date(2021, 6, 1)),
        ("g-2023", "Laptop", "Lenovo", date(2023, 6, 1)),
    ]))
    assert gap.generations("Laptop", "Lenovo", date(2024, 1, 1), 12) == (["g-2023"], [], ["g-2021", "g-2019"])
    # the newest launch lies 20 months before the order date and three slugs launched within 3 months of it:
    # all three ARE the current generation (an order-date anchor would have left only the latest one)
    late = _SlugIndex(_mini_pool([
        ("older-1", "Smartphone", "Samsung", date(2019, 2, 1)),
        ("prev-a", "Smartphone", "Samsung", date(2021, 1, 20)),
        ("gen-a", "Smartphone", "Samsung", date(2022, 7, 1)),
        ("gen-b", "Smartphone", "Samsung", date(2022, 8, 15)),
        ("gen-c", "Smartphone", "Samsung", date(2022, 10, 1)),   # the newest launch; the order comes 20 months later
    ]))
    cur, prev, older = late.generations("Smartphone", "Samsung", date(2024, 6, 1), 12)
    assert cur == ["gen-c", "gen-b", "gen-a"], "three slugs inside 3 months of the newest launch are one current generation"
    assert prev == ["prev-a"] and older == ["older-1"]
    # the same pool with a 3-month window: gen-a (3 months before gen-c) falls out into the previous window
    cur, prev, older = late.generations("Smartphone", "Samsung", date(2024, 6, 1), 3)
    assert cur == ["gen-c", "gen-b"] and prev == ["gen-a"] and older == ["prev-a", "older-1"]


def test_build_fleet_buys_the_whole_generation_not_only_the_latest_slug(lake_cfg):
    """Three slugs of one generation (one launched three weeks later) are bought alike; the run is seeded."""
    from restwert.lakegen.catalogue import FleetCatalogue
    from restwert.lakegen.fleet import build_fleet

    pool = _mini_pool([
        ("prev-1", "Smartphone", "Apple", date(2021, 9, 24)),
        ("cur-pro", "Smartphone", "Apple", date(2022, 9, 16)),
        ("cur-pro-max", "Smartphone", "Apple", date(2022, 9, 16)),
        ("cur-plus", "Smartphone", "Apple", date(2022, 10, 7)),
    ])
    cat = FleetCatalogue(models=pd.DataFrame(), variants=pd.DataFrame(), pool=pool, excluded=pd.DataFrame(), vat_rate=0.19)
    raw = lake_cfg.model_dump()
    raw.update({
        "history_start": date(2023, 1, 1), "purchase_end": date(2023, 8, 31),   # the three 2022 slugs are current throughout
        "fleet_mix": {"Smartphone": 1.0, "Tablet": 0.0, "Laptop": 0.0},
        "oem_share": {"Smartphone": {"Apple": 1.0}, "Tablet": {"Apple": 1.0}, "Laptop": {"Apple": 1.0}},
        "newest_model_share": 0.70, "previous_generation_share": 0.20, "generation_window_months": 12,
    })
    cfg = LakeConfig.model_validate(raw)
    p_prev = float(cfg.previous_generation_share)
    n = 3000
    fleet = build_fleet(cfg, cat, np.random.default_rng(11), n)
    bought = fleet[~fleet["_spare"].astype(bool)]
    assert len(bought) == n and set(bought["slug"]) == {"prev-1", "cur-pro", "cur-pro-max", "cur-plus"}
    share = bought["slug"].value_counts(normalize=True)
    for slug in ("cur-pro", "cur-pro-max", "cur-plus"):
        assert abs(share[slug] - 0.70 / 3) <= 0.03, (slug, share[slug])
    assert share["cur-plus"] < 0.35, "the latest slug must not take the whole newest_model_share"
    # the 2021 slug is the previous generation (previous_generation_share) and, the older pool being empty,
    # also takes the older draw (1 - newest_model_share - previous_generation_share)
    assert abs(share["prev-1"] - (p_prev + (1.0 - 0.70 - p_prev))) <= 0.03
    assert fleet.attrs["oem_redraws"] == 0
    # the share is read from the config, not from a module constant: with an older slug in the pool the
    # previous and the older draw land on different slugs, so a changed share moves the split
    pool_old = _mini_pool([("old-0", "Smartphone", "Apple", date(2020, 3, 1))] + [
        (s, "Smartphone", "Apple", d) for s, d in (("prev-1", date(2021, 9, 24)), ("cur-pro", date(2022, 9, 16)),
                                                   ("cur-pro-max", date(2022, 9, 16)), ("cur-plus", date(2022, 10, 7)))
    ])
    cat_old = FleetCatalogue(models=pd.DataFrame(), variants=pd.DataFrame(), pool=pool_old, excluded=pd.DataFrame(), vat_rate=0.19)
    for p_prev_cfg in (0.20, 0.05):
        f = build_fleet(LakeConfig.model_validate({**raw, "previous_generation_share": p_prev_cfg}), cat_old, np.random.default_rng(11), n)
        s = f[~f["_spare"].astype(bool)]["slug"].value_counts(normalize=True)
        assert abs(s["prev-1"] - p_prev_cfg) <= 0.03, (p_prev_cfg, s["prev-1"])
        assert abs(s["old-0"] - (1.0 - 0.70 - p_prev_cfg)) <= 0.03, (p_prev_cfg, s["old-0"])
    # the two shares must leave room for the older pool
    with pytest.raises(ValueError, match="previous_generation_share"):
        LakeConfig.model_validate({**raw, "previous_generation_share": 0.35})
    with pytest.raises(ValueError, match="previous_generation_share"):
        LakeConfig.model_validate({**raw, "previous_generation_share": -0.1})
    # determinism with the seed
    again = build_fleet(cfg, cat, np.random.default_rng(11), n)
    pd.testing.assert_frame_equal(fleet, again)


# --------------------------------------------------------------------------- truth calibration


def test_truth_curve_selection(lake_cfg):
    curves = load_curves()
    t = lake_cfg.truth_v2
    apple = truth_curve(curves, "Smartphone", "Apple", t)
    assert apple.source == "family_oem" and apple.fit_quality == "ok" and apple.group == "Smartphone / Apple"
    hp = truth_curve(curves, "Laptop", "HP", t)
    assert hp.source == "family" and hp.group == "Laptop"
    fair = truth_curve(curves, "Smartphone", "Fairphone", t)   # thin row -> family
    assert fair.source == "family"
    missing = truth_curve(load_curves(Path("does/not/exist.csv")), "Tablet", "Apple", t)
    assert missing.source == "default" and missing.intercept == t.default_curve["intercept"]
    assert apple.grade_offsets["B"] == 0.0 and apple.grade_offsets["D"] == t.grade_d_offset_default
    assert q_public(apple, 36.0, "B") == pytest.approx(math.exp(apple.intercept + apple.slope_per_month * 36.0))


def test_true_price_inside_caps(lake_cfg):
    t = lake_cfg.truth_v2
    curve = TruthCurve(group="x", source="default", fit_quality="default", n=0, intercept=0.5, slope_per_month=0.0,
                       age_min=0, age_max=0, grade_offsets={"A": 0.0, "B": 0.0, "C": 0.0, "D": -0.6})
    mult = {"marketplace": 1.0, "as_is": 0.55}
    young = true_price(1000.0, curve, 1.0, "A", "marketplace", mult, t, noise=0.0)
    assert young == pytest.approx(round(1000.0 * t.q_young_cap * t.ask_to_realised, 2))   # capped at q_young_cap
    huge = true_price(1000.0, curve, 1.0, "A", "marketplace", mult, t, noise=5.0)
    assert huge == pytest.approx(1000.0 * t.ratio_max)
    tiny = true_price(1000.0, curve, 1.0, "D", "as_is", mult, t, noise=-9.0)
    assert tiny == pytest.approx(1000.0 * t.ratio_min)
    assert default_curve(t).source == "default"


# --------------------------------------------------------------------------- reproducibility


def _digests(raw_dir: Path) -> dict[str, str]:
    out = {}
    for p in sorted(raw_dir.rglob("*.csv")):
        out[p.relative_to(raw_dir).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_generate_world_is_reproducible(tmp_path, lake_cfg):
    a = run_generate_lake(lake_cfg, seed=11, n_serials=150, raw_dir=tmp_path / "a" / "raw")
    b = run_generate_lake(lake_cfg, seed=11, n_serials=150, raw_dir=tmp_path / "b" / "raw")
    da, db = _digests(tmp_path / "a" / "raw"), _digests(tmp_path / "b" / "raw")
    assert da and da == db
    assert a.counts == b.counts
    assert (tmp_path / "a" / "SYNTHETIC.md").read_text(encoding="utf-8") == (tmp_path / "b" / "SYNTHETIC.md").read_text(encoding="utf-8")
    assert (tmp_path / "a" / "raw" / "_manifest.json").read_text(encoding="utf-8") == (tmp_path / "b" / "raw" / "_manifest.json").read_text(encoding="utf-8")


def test_different_seed_differs(tmp_path, lake_cfg):
    run_generate_lake(lake_cfg, seed=11, n_serials=150, raw_dir=tmp_path / "a" / "raw")
    run_generate_lake(lake_cfg, seed=12, n_serials=150, raw_dir=tmp_path / "b" / "raw")
    da, db = _digests(tmp_path / "a" / "raw"), _digests(tmp_path / "b" / "raw")
    transactional = [k for k in da if not k.startswith(("catalogue/", "market/"))]
    assert any(da[k] != db.get(k) for k in transactional)
    for k in da:
        if k.startswith(("catalogue/", "market/")):
            assert da[k] == db[k], "public copies do not depend on the seed"


# --------------------------------------------------------------------------- landing files


def test_every_landing_file_has_synthetic_line_and_column(session_lake):
    keys = {_feed_key_of(p) for p in session_lake.files}
    assert keys == set(FEED_KEYS), sorted(set(FEED_KEYS) - keys)
    assert len(FEED_KEYS) == 19
    for p in session_lake.files:
        first, df = _read_landing(p)
        key = _feed_key_of(p)
        spec = FEEDS[key]
        assert "is_synthetic" in df.columns and df.columns[-1] == "is_synthetic"
        assert list(df.columns) == list(spec.column_names) + ["is_synthetic"]
        assert len(df) > 0, p
        if spec.is_reference:
            assert first.startswith("# PUBLIC DATA - copy of ")
            assert set(df["is_synthetic"]) == {"false"}
        else:
            assert first == f"# SYNTHETIC DATA - restwert generate-lake seed=7 feed={key} delivery={_delivered_on(p).isoformat()}"
            assert set(df["is_synthetic"]) == {"true"}
        assert p.name == f"{_delivered_on(p).isoformat()}_{spec.feed}_001.csv"
        assert p.parent.parent.name == spec.source_system
        raw = p.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in raw
        assert EM_DASH not in first


def test_reference_files_are_public_false(session_lake):
    for key in ("catalogue/models", "catalogue/variants", "market/curves"):
        files = [p for p in session_lake.files if _feed_key_of(p) == key]
        assert len(files) == 1
        first, df = _read_landing(files[0])
        assert _delivered_on(files[0]) == session_lake.cfg.as_of
        assert set(df["is_synthetic"]) == {"false"}
        assert first.startswith("# PUBLIC DATA")
    models = _all_rows(session_lake.files, "catalogue/models")
    assert len(models) == 233     # catalogue round 4: 191 + 42 models
    variants = _all_rows(session_lake.files, "catalogue/variants")
    assert len(variants) == 628   # catalogue round 4: 494 + 128 variants; laptop research 2026-09-16: + 5, HP RRPs + 2, one duplicate Latitude 5440 row removed
    assert (variants["storage_gb"].str.contains(r"\.", regex=True) == False).all()   # copied as text, not re-typed
    curves = _all_rows(session_lake.files, "market/curves")
    assert set(curves["fit_quality"]) <= {"ok", "thin", "no_fit"}


def test_no_leakage_per_delivery(session_lake):
    """Every timestamp of every row in a file dated delivered_on is <= delivered_on (planned dates excepted)."""
    checked = 0
    for p in session_lake.files:
        key = _feed_key_of(p)
        spec = FEEDS[key]
        if spec.is_reference:
            continue
        delivered_on = _delivered_on(p)
        _, df = _read_landing(p)
        for name, dtype in spec.columns:
            if dtype not in ("date", "datetime") or name in PLANNED_FUTURE_COLUMNS:
                continue
            values = pd.to_datetime(df[name].mask(df[name] == ""), errors="coerce").dropna()
            if values.empty:
                continue
            assert values.max().date() <= delivered_on, (p.name, name, values.max())
            checked += 1
    assert checked > 50
    periods = delivery_periods(session_lake.cfg)
    assert periods[-1][1] == session_lake.cfg.as_of
    assert all(b >= a for a, b in periods)


def test_rows_are_delivered_by_their_order_column_and_never_after_as_of(session_lake):
    as_of = session_lake.cfg.as_of
    for key in ("erp/purchase_orders", "portal/rental_invoices", "recommerce/orders"):
        spec = FEEDS[key]
        for p in [f for f in session_lake.files if _feed_key_of(f) == key]:
            _, df = _read_landing(p)
            values = pd.to_datetime(df[spec.order_column], errors="coerce")
            assert values.max().date() <= _delivered_on(p) <= as_of


# --------------------------------------------------------------------------- arithmetic against v0.1


def test_rental_invoices_match_months_billed(session_lake):
    contracts = session_lake.world.v01["rental_contracts"]
    invoices = _originals(session_lake.world.frames["portal/rental_invoices"])
    counts = invoices.groupby("contract_id").size()
    as_of = session_lake.cfg.as_of
    checked = 0
    for r in contracts.itertuples(index=False):
        actual_end = None if pd.isna(r.actual_end_date) else r.actual_end_date
        expected = months_billed(r.start_date, r.end_date, actual_end, as_of)
        assert int(counts.get(r.contract_id, 0)) == expected, r.contract_id
        checked += 1
    assert checked >= SESSION_SERIALS
    assert (invoices["invoice_date"] <= as_of).all()
    assert (invoices["amount_eur"] > 0).all()
    # invoices per contract are numbered 1..n and dated on the billing date of that month
    one = invoices[invoices["contract_id"] == contracts["contract_id"].iloc[0]].sort_values("period_no")
    assert list(one["period_no"]) == list(range(1, len(one) + 1))


def test_freight_allocation_sums_to_invoice_line(session_lake):
    fleet = session_lake.world.fleet
    inv = _originals(session_lake.world.frames["erp/supplier_invoices"])
    freight = inv[inv["line_kind"] == "freight"]
    # orphan defects moved some lines to po_line + 90; the untouched ones must allocate to the cent
    by_line = fleet.groupby(["po_number", "po_line"])
    checked = 0
    for r in freight.itertuples(index=False):
        key = (r.po_number, int(r.po_line))
        if key not in by_line.groups:
            continue
        serials = by_line.get_group(key)
        shares = allocate_cents(float(r.amount_eur), len(serials))
        assert round(sum(shares), 2) == pytest.approx(round(float(r.amount_eur), 2))
        assert sorted(serials["freight_share_eur"].round(2)) == sorted(shares)
        checked += 1
    assert checked > 100
    assert allocate_cents(10.01, 2) == [5.0, 5.01]
    landed = (fleet["unit_price_eur"] + fleet["freight_share_eur"] + fleet["duty_share_eur"]).round(2)
    assert (landed == fleet["landed_cost_eur"].round(2)).all()
    duty = inv[inv["line_kind"] == "duty"]
    assert set(duty["supplier_id"]) <= {"SUP-RSL-B"}
    unit = inv[inv["line_kind"] == "unit"]
    assert unit["serial"].notna().all() and (unit["qty"] == 1).all()
    assert (inv["amount_eur"] > 0).all()


def test_purchase_prices_are_below_net_rrp_and_pos_reference_the_register(session_lake):
    fleet = session_lake.world.fleet
    assert (fleet["unit_price_eur"] <= fleet["rrp_net"]).all()
    assert (fleet["discount_pct"] >= 0).all()
    assert fleet["serial"].is_unique
    assert fleet["serial"].str.match(r"^SN-[A-Z]{3}-[0-9A-F]{8}$").all()
    pos = _originals(session_lake.world.frames["erp/purchase_orders"])
    register = session_lake.world.frames["contracts/register"]
    linked = pos[pos["contract_ref"].notna()]
    assert set(linked["contract_ref"]) <= set(register["contract_id"])
    reseller = pos[pos["supplier_role"] == "reseller"]
    assert reseller["contract_ref"].notna().all()
    manufacturer = pos[pos["supplier_role"] == "manufacturer"]
    assert 0.6 < manufacturer["contract_ref"].notna().mean() < 1.0
    lines = _originals(session_lake.world.frames["erp/po_lines"])
    assert lines.groupby("po_number")["qty_ordered"].sum().sum() == len(fleet)
    receipts = _originals(session_lake.world.frames["erp/goods_receipts"])
    assert set(receipts["serial"]) == set(fleet["serial"]) and len(receipts) == len(fleet)


def test_every_supplier_and_counterparty_name_is_allowed(session_lake):
    def allowed(name: str) -> bool:
        return name in MANUFACTURERS or name.endswith(ROLE_ONLY_SUFFIX)

    seen = 0
    for key, cols in SERIAL_NAME_COLUMNS.items():
        df = _all_rows(session_lake.files, key)
        for c in cols:
            names = set(df[c].unique()) - {""}
            assert names, (key, c)
            bad = sorted(n for n in names if not allowed(n))
            assert not bad, (key, c, bad)
            seen += len(names)
    assert seen > 12
    register = _all_rows(session_lake.files, "contracts/register")
    assert len(register) >= 20
    assert set(register[register["counterparty_is_public"] == "true"]["counterparty_name"]) == set(MANUFACTURERS)
    assert (register["terms_note"].str.startswith("synthetic placeholder terms")).all()
    assert set(session_lake.world.role_only_names) and all(n.endswith(ROLE_ONLY_SUFFIX) for n in session_lake.world.role_only_names)


def test_no_denylisted_name_in_landing_files_or_synthetic_md(session_lake):
    offenders = {}
    for p in session_lake.files + [session_lake.lake_dir / "SYNTHETIC.md"]:
        hits = find_denylisted(p.read_text(encoding="utf-8"))
        if hits:
            offenders[p.name] = hits
    assert not offenders


def test_realisation_sanity(session_lake):
    """Grade B marketplace sales at 30 to 40 months: mean gross / rrp_net within 0.12 of q_36 x haircut per family."""
    world = session_lake.world
    cfg = session_lake.cfg
    orders = _originals(world.frames["recommerce/orders"])
    fleet = world.fleet.set_index("serial")
    curves = load_curves()
    checked = 0
    for fam in ("Smartphone", "Tablet", "Laptop"):
        fam_curve = truth_curve(curves, fam, "no such manufacturer", cfg.truth_v2)
        assert fam_curve.source == "family"
        q36 = math.exp(fam_curve.intercept + fam_curve.slope_per_month * 36.0)
        expected = min(q36, cfg.truth_v2.q_young_cap) * cfg.truth_v2.ask_to_realised
        ratios = []
        for r in orders.itertuples(index=False):
            d = fleet.loc[r.serial]
            if d["catalogue_family"] != fam or r.channel != "marketplace" or r.grade_at_sale != "B":
                continue
            age = (r.sold_at.date() - d["launch_date"]).days / 30.4375
            if 30 <= age <= 40:
                ratios.append(float(r.gross_price_eur) / float(d["rrp_net"]))
        if len(ratios) >= 5:
            assert abs(float(np.mean(ratios)) - expected) <= 0.12, (fam, np.mean(ratios), expected, len(ratios))
            checked += 1
    assert checked >= 1
    assert (orders["gross_price_eur"] > 0).all()
    assert set(world.truth_sources.values()) <= {"family_oem", "family"}


def test_credit_notes_follow_orders_and_fees(session_lake, lake_cfg):
    orders = _all_rows(session_lake.files, "recommerce/orders").drop_duplicates("order_id")
    notes = _all_rows(session_lake.files, "recommerce/credit_notes").drop_duplicates("credit_note_id")
    assert set(notes["order_id"]) <= set(orders["order_id"])
    merged = notes.merge(orders, on="order_id", suffixes=("", "_o"))
    for r in merged.itertuples(index=False):
        gross = float(r.gross_price_eur)
        assert float(r.gross_eur) == pytest.approx(gross)
        assert float(r.fee_pct_eur) == pytest.approx(round(gross * lake_cfg.fee_pct[r.channel], 2))
        assert float(r.fee_fixed_eur) == pytest.approx(lake_cfg.fee_fixed_eur[r.channel])
        assert float(r.net_eur) == pytest.approx(round(gross - float(r.fee_pct_eur) - float(r.fee_fixed_eur), 2))
        credited = pd.Timestamp(r.credited_at).date()
        sold = pd.Timestamp(r.sold_at).date()
        assert credited == sold + timedelta(days=lake_cfg.days_to_cash[r.channel])


def test_v01_frames_are_carried(session_lake):
    v01 = session_lake.world.v01
    assert set(v01) == {"devices", "rental_contracts", "events", "refurbishment", "resale"}
    assert len(v01["devices"]) == SESSION_SERIALS + session_lake.world.n_spares
    assert set(v01["devices"]["model_family"]) == {"iphone_like", "android_like", "tablet_like", "laptop_like"}
    assert v01["resale"]["sale_id"].str.startswith("RO-").all()


# --------------------------------------------------------------------------- defects and provenance


def test_defects_are_injected_and_counted(tmp_path, lake_cfg):
    cat = load_fleet_catalogue(vat_rate=0.19)
    curves = load_curves()
    world = generate_world(lake_cfg, cat, curves, seed=3, n_serials=800)
    counts = world.defects_injected
    assert set(counts) == set(DEFECT_KINDS)
    assert all(v > 0 for v in counts.values()), counts

    from restwert.lakegen.writer import write_landing_files

    cfg_run = lake_cfg.model_copy(update={"seed": 3, "n_devices": 800}, deep=True)
    files = write_landing_files(world, tmp_path / "raw", cfg_run, 3)

    serials = set(_all_rows(files, "erp/goods_receipts")["serial"])
    shipments = _all_rows(files, "wms/shipments")
    unknown = shipments[~shipments["serial"].isin(serials)]
    assert len(unknown) == counts["unknown_serial"]
    assert (unknown["direction"] == "outbound").all()

    # identical duplicates: same business key, identical content, in two files
    identical = 0
    conflicting = 0
    for key, spec in FEEDS.items():
        if spec.is_reference or key == "contracts/register":
            continue
        rows = []
        for p in [f for f in files if _feed_key_of(f) == key]:
            _, df = _read_landing(p)
            df = df.assign(_file=p.name)
            rows.append(df)
        df = pd.concat(rows, ignore_index=True)
        business_key = {
            "erp/po_lines": ["po_number", "po_line"],
            "erp/supplier_invoices": ["invoice_number", "invoice_line"],
            "erp/goods_receipts": ["serial"],
        }.get(key, [spec.column_names[0]])
        content_cols = [c for c in df.columns if c != "_file"]
        dup = df[df.duplicated(business_key, keep=False)]
        for _, grp in dup.groupby(business_key):
            distinct = grp[content_cols].drop_duplicates()
            first_file = grp["_file"].min()
            assert (grp["_file"] == first_file).sum() == 1, "the original is alone in the earliest delivery"
            assert grp["_file"].nunique() >= 2, "a duplicate always lands in a later delivery"
            identical += len(grp) - len(distinct)
            conflicting += len(distinct) - 1
    assert identical == counts["identical_duplicate"]
    assert conflicting == counts["conflicting_duplicate"]

    orders = _all_rows(files, "recommerce/orders").drop_duplicates("order_id")
    notes = _all_rows(files, "recommerce/credit_notes")
    due = orders[[pd.Timestamp(s).date() + timedelta(days=lake_cfg.days_to_cash[c]) <= lake_cfg.as_of
                  for s, c in zip(orders["sold_at"], orders["channel"])]]
    missing = set(due["order_id"]) - set(notes["order_id"])
    assert len(missing) == counts["missing_credit_note"]

    invoices = _all_rows(files, "erp/supplier_invoices").drop_duplicates(["invoice_number", "invoice_line"])
    lines = _all_rows(files, "erp/po_lines").drop_duplicates(["po_number", "po_line"])
    line_keys = set(zip(lines["po_number"], lines["po_line"].astype(int)))
    orphan = invoices[[(p, int(l)) not in line_keys for p, l in zip(invoices["po_number"], invoices["po_line"])]]
    assert len(orphan) == counts["orphan_freight"]
    assert (orphan["line_kind"] == "freight").all()

    register = _all_rows(files, "contracts/register")
    assert len(register) == len(world.register) and register["contract_id"].is_unique


def test_synthetic_md_lists_provenance(session_lake, lake_cfg):
    text = (session_lake.lake_dir / "SYNTHETIC.md").read_text(encoding="utf-8")
    assert EM_DASH not in text
    assert text.startswith("# SYNTHETIC DATA")
    assert "seed: `7`" in text
    assert f"n_serials: `{SESSION_SERIALS}`" in text
    for key in FEED_KEYS:
        assert f"`{key}`" in text
    for group, source in session_lake.world.truth_sources.items():
        assert f"| {group} | {source} |" in text
    t = lake_cfg.truth_v2
    assert str(t.q_young_cap) in text and str(t.ask_to_realised) in text and str(t.grade_d_offset_default) in text
    assert t.owner in text
    for slug in session_lake.world.excluded_slugs:
        assert f"`{slug}`" in text
    for kind in DEFECT_KINDS:
        assert f"`{kind}`" in text
    for m in MANUFACTURERS:
        assert f"`{m}`" in text
    assert "(role-only)" in text
    assert "VAT" in text and "0.19" in text
    assert SYNTHETIC_SENTENCE in text
    manifest = session_lake.raw_dir / "_manifest.json"
    assert manifest.exists()
    import json

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["seed"] == 7 and payload["n_files"] == len(session_lake.files)
    by_path = {e["path"]: e for e in payload["files"]}
    for p in session_lake.files:
        rel = p.relative_to(session_lake.raw_dir).as_posix()
        assert by_path[rel]["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
        _, df = _read_landing(p)
        assert by_path[rel]["rows"] == len(df)


def test_generate_lake_small_under_5_seconds(tmp_path, lake_cfg):
    t0 = time.perf_counter()
    summary = run_generate_lake(lake_cfg, seed=42, n_serials=500, raw_dir=tmp_path / "raw")
    seconds = time.perf_counter() - t0
    assert summary.command == "generate-lake"
    assert summary.counts["serials"] == 500
    assert summary.counts["files"] > 100
    assert (tmp_path / "SYNTHETIC.md").exists()
    assert seconds < 5.0, f"generate-lake on 500 serials took {seconds:.2f} s"


def test_regenerate_replaces_landing_files(tmp_path, lake_cfg):
    run_generate_lake(lake_cfg, seed=1, n_serials=120, raw_dir=tmp_path / "raw")
    stale = tmp_path / "raw" / "erp" / "po_lines" / "1999-12-31_po_lines_001.csv"
    stale.write_text("# SYNTHETIC DATA - stale\n", encoding="utf-8")
    run_generate_lake(lake_cfg, seed=2, n_serials=120, raw_dir=tmp_path / "raw")
    assert not stale.exists()
    seeds = {_read_landing(p)[0] for p in (tmp_path / "raw").rglob("*_po_lines_*.csv")}
    assert all("seed=2" in s for s in seeds)


def test_landing_columns_match_lake_feeds_when_available():
    """When module 1's feeds.py is present, the generator's column lists must agree with it."""
    feeds = pytest.importorskip("restwert.lake.feeds")
    theirs = getattr(feeds, "FEEDS", None)
    if not theirs:
        pytest.skip("restwert.lake.feeds.FEEDS not defined yet")
    assert tuple(theirs) == FEED_KEYS
    for key, spec in theirs.items():
        their_cols = [c.name for c in spec.columns if c.name != "is_synthetic"]
        mine = [c for c in FEEDS[key].column_names]
        assert mine == their_cols, key
        assert spec.order_column == FEEDS[key].order_column, key
        assert spec.source_system == FEEDS[key].source_system and spec.feed == FEEDS[key].feed


def test_regenerate_refuses_to_remove_a_real_export(tmp_path, lake_cfg):
    """A landing file without the generated header is a real export: named, never removed, unless forced."""
    from restwert.lakegen import clear_landing_files, scan_landing_files

    run_generate_lake(lake_cfg, seed=1, n_serials=120, raw_dir=tmp_path / "raw")
    real = tmp_path / "raw" / "wms" / "shipments" / "2026-05-31_shipments_009.csv"
    real.write_text("shipment_id,serial,is_synthetic\nSH-REAL,SN-1,false\n", encoding="utf-8")
    generated, blocking = scan_landing_files(tmp_path / "raw")
    assert blocking == [real] and len(generated) > 100
    with pytest.raises(ValueError, match="2026-05-31_shipments_009.csv"):
        run_generate_lake(lake_cfg, seed=2, n_serials=120, raw_dir=tmp_path / "raw")
    assert real.exists() and real.read_text(encoding="utf-8").startswith("shipment_id")
    with pytest.raises(ValueError, match="wipe-raw"):
        clear_landing_files(tmp_path / "raw")
    run_generate_lake(lake_cfg, seed=2, n_serials=120, raw_dir=tmp_path / "raw", wipe_raw=True)
    assert not real.exists()
