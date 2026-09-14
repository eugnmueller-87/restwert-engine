"""Honesty gates extended to the data lake (SPEC_v0.2 9.5 and acceptance item 5).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

* No denylisted name in any landing file of the session lake nor in any VARCHAR
  column of any bronze table.
* Every supplier and counterparty name is a catalogue manufacturer or ends with
  ``(role-only)``: purchase order headers, the contracts register, indirect
  spend, carrier and repair partner references.
* No em dash in the v0.2 docs, ``config/lake.yaml``, the README, every ``#``
  header line of every landing file, ``SYNTHETIC.md``.
* Every landing file starts with ``# SYNTHETIC DATA`` or ``# PUBLIC DATA`` and
  carries an ``is_synthetic`` column (reference feeds ``false``, fleet feeds ``true``).
* No network, mail or shell import in the new packages.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from restwert import db
from tests.conftest import lake_packages_available
from tests.fixtures.allowlist import MANUFACTURERS, ROLE_ONLY_SUFFIX, is_allowed, scan_frame_names
from tests.fixtures.denylist import find_denylisted

ROOT = Path(__file__).resolve().parents[1]
EM_DASH = "\u2014"  # written as an escape so this file does not trip its own check
NEW_PACKAGES = ("lake", "lakegen", "ledger", "levers", "gold")
FORBIDDEN_MODULES = ("smtplib", "requests", "httpx", "urllib.request", "boto3", "paramiko", "subprocess")
_IMPORT_RE = re.compile(r"^\s*(?:import\s+(?P<mod>[\w.]+)|from\s+(?P<frm>[\w.]+)\s+import\b)", re.MULTILINE)
NAME_COLUMNS = {
    "bronze.erp_purchase_orders": ("supplier_name",),
    "bronze.ctr_register": ("counterparty_name",),
    "bronze.fin_indirect_spend": ("supplier_name",),
    "bronze.wms_shipments": ("carrier_ref",),
    "bronze.sd_tickets": ("repair_partner_ref",),
    "bronze.rf_work_orders": ("partner_ref",),
}
needs_lake = pytest.mark.skipif(not lake_packages_available(), reason="v0.2 lake packages not present in this checkout")


def _landing_files(lake_dir: Path) -> list[Path]:
    files = sorted((lake_dir / "raw").rglob("*.csv"))
    assert files, f"no landing file under {lake_dir / 'raw'}"
    return files


def _bronze_tables(con) -> list[str]:
    rows = con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'bronze' ORDER BY 1").fetchall()
    return [f"bronze.{r[0]}" for r in rows]


def _varchar_columns(con, table: str) -> list[str]:
    schema_name, name = table.split(".", 1)
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = ? AND table_name = ? AND data_type LIKE '%VARCHAR%'",
        [schema_name, name],
    ).fetchall()
    return [r[0] for r in rows]


# --------------------------------------------------------------------------- allowlist fixture itself


def test_allowlist_helpers():
    assert "Apple" in MANUFACTURERS and "HMD Global (Nokia)" in MANUFACTURERS and len(MANUFACTURERS) == 10
    assert ROLE_ONLY_SUFFIX == " (role-only)"
    assert is_allowed("Dell") and is_allowed("IT reseller A (role-only)") and is_allowed("Carrier partner (role-only)")
    assert not is_allowed("Supplier-A") and not is_allowed("") and not is_allowed(None) and not is_allowed("apple")
    import pandas as pd

    df = pd.DataFrame({"supplier_name": ["Apple", "Some Corp", None], "other": ["x", "y", "z"]})
    assert scan_frame_names(df, ["supplier_name", "missing"]) == ["supplier_name=Some Corp"]


# --------------------------------------------------------------------------- lake files and bronze


@needs_lake
def test_no_denylisted_names_in_lake_files_and_bronze(lake_pipeline_paths, lake_pipeline_db):
    offenders: dict[str, list[str]] = {}
    for path in _landing_files(lake_pipeline_paths.lake_dir):
        hits = find_denylisted(path.read_text(encoding="utf-8", errors="replace"))
        if hits:
            offenders[str(path.relative_to(lake_pipeline_paths.lake_dir))] = hits
    assert not offenders, f"denylisted names in landing files: {offenders}"
    for table in _bronze_tables(lake_pipeline_db):
        cols = _varchar_columns(lake_pipeline_db, table)
        if not cols:
            continue
        schema_name, name = table.split(".", 1)
        for col in cols:
            values = lake_pipeline_db.execute(f'SELECT DISTINCT "{col}" FROM "{schema_name}"."{name}" WHERE "{col}" IS NOT NULL').fetchall()
            text = "\n".join(str(v[0]) for v in values)
            hits = find_denylisted(text)
            if hits:
                offenders[f"{table}.{col}"] = hits
    assert not offenders, f"denylisted names in bronze: {offenders}"
    md = lake_pipeline_paths.lake_dir / "SYNTHETIC.md"
    assert not find_denylisted(md.read_text(encoding="utf-8")), "denylisted name in SYNTHETIC.md"


@needs_lake
def test_every_supplier_and_counterparty_is_allowed(lake_pipeline_db):
    offenders: dict[str, list[str]] = {}
    for table, cols in NAME_COLUMNS.items():
        schema_name, name = table.split(".", 1)
        df = db.read_df(lake_pipeline_db, f'SELECT * FROM "{schema_name}"."{name}"')
        assert len(df) > 0, f"{table} is empty"
        hits = scan_frame_names(df, cols)
        if hits:
            offenders[table] = hits
    assert not offenders, f"names outside the allowlist: {offenders}"
    ctr = db.read_df(lake_pipeline_db, 'SELECT counterparty_name, counterparty_is_public FROM "bronze"."ctr_register"')
    for _, row in ctr.iterrows():
        public = str(row["counterparty_name"]) in MANUFACTURERS
        assert bool(row["counterparty_is_public"]) == public, row["counterparty_name"]


@needs_lake
def test_every_landing_file_first_line_and_is_synthetic_column(lake_pipeline_paths):
    for path in _landing_files(lake_pipeline_paths.lake_dir):
        with path.open("r", encoding="utf-8", newline="") as fh:
            first = fh.readline().rstrip("\r\n")
            assert first.startswith("# SYNTHETIC DATA") or first.startswith("# PUBLIC DATA"), f"{path.name}: {first!r}"
            header = fh.readline().rstrip("\r\n").split(",")
            assert "is_synthetic" in header, f"{path.name}: no is_synthetic column"
            idx = header.index("is_synthetic")
            reader = csv.reader(fh)
            values = {row[idx].strip().lower() for row in reader if row}
        expected = {"false"} if first.startswith("# PUBLIC DATA") else {"true"}
        assert values <= expected, f"{path.name}: is_synthetic values {values}, expected {expected}"
        rel = path.relative_to(lake_pipeline_paths.lake_dir / "raw")
        if first.startswith("# PUBLIC DATA"):
            assert rel.parts[0] in ("catalogue", "market"), f"public header on a fleet feed: {rel}"
        else:
            assert rel.parts[0] not in ("catalogue", "market"), f"synthetic header on a reference feed: {rel}"
    with (lake_pipeline_paths.lake_dir / "raw" / "_manifest.json").open(encoding="utf-8") as fh:
        assert fh.read(1) == "{"


@needs_lake
def test_bronze_rows_carry_the_synthetic_flag_of_their_feed(lake_pipeline_db):
    for table in _bronze_tables(lake_pipeline_db):
        schema_name, name = table.split(".", 1)
        flags = {r[0] for r in lake_pipeline_db.execute(f'SELECT DISTINCT is_synthetic FROM "{schema_name}"."{name}"').fetchall()}
        if name in ("cat_models", "cat_variants", "mkt_curves"):
            assert flags <= {False}, f"{table}: reference rows must be public (false), got {flags}"
        elif name in ("deliveries", "unresolved"):
            continue  # registry rows carry the flag of their file: public reference files land as false
        elif flags:
            assert flags == {True}, f"{table}: fleet rows must be synthetic, got {flags}"


# --------------------------------------------------------------------------- prose


def _em_dash_offenders(paths: list[Path]) -> list[str]:
    out = []
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if EM_DASH in text:
            out.append(f"{path.name}:{text[: text.index(EM_DASH)].count(chr(10)) + 1}")
    return out


def test_no_em_dash_in_v02_docs_configs_and_readme():
    paths = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "config" / "lake.yaml", ROOT / "README.md", ROOT / "data" / "SYNTHETIC.md"]
    assert not _em_dash_offenders(paths), _em_dash_offenders(paths)
    for name in ("README.md", "docs/SPEC_v0.2.md"):
        assert (ROOT / name).exists(), name


@needs_lake
def test_no_em_dash_in_landing_headers_and_synthetic_md(lake_pipeline_paths):
    offenders = []
    for path in _landing_files(lake_pipeline_paths.lake_dir):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.startswith("#"):
                    break
                if EM_DASH in line:
                    offenders.append(path.name)
    assert not offenders, offenders
    assert not _em_dash_offenders([lake_pipeline_paths.lake_dir / "SYNTHETIC.md", lake_pipeline_paths.csv_dir.parent / "SYNTHETIC.md"])


def test_readme_v02_names_the_cycle_and_governance():
    from restwert import GOVERNANCE_PRINCIPLE

    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert GOVERNANCE_PRINCIPLE in text
    for needle in ("0 Data", "1 Purchase", "2 TCO", "3 Residual", "4 Resale", "5 Result", "6 Levers", "7 Contracts", "python -m restwert all", "--dry-run", "(role-only)"):
        assert needle in text, needle
    assert "v0.2" in text


# --------------------------------------------------------------------------- side effects


def test_no_side_effects_in_new_packages():
    offenders: dict[str, list[str]] = {}
    scanned = 0
    for pkg in NEW_PACKAGES:
        folder = ROOT / "restwert" / pkg
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            scanned += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            mods = set()
            for m in _IMPORT_RE.finditer(text):
                mods.add(m.group("mod") or m.group("frm"))
            hits = sorted(mod for mod in mods for bad in FORBIDDEN_MODULES if mod == bad or mod.startswith(bad + "."))
            if hits:
                offenders[str(path.relative_to(ROOT))] = hits
            assert not re.search(r"\bos\.(system|exec[lv]p?e?|popen|spawn\w*)\s*\(", text), path
    for extra in (ROOT / "restwert" / "contracts" / "register_v2.py", ROOT / "restwert" / "contracts" / "counterparties.py",
                  ROOT / "restwert" / "dashboard" / "views" / "cycle"):
        paths = [extra] if extra.is_file() else (sorted(extra.rglob("*.py")) if extra.exists() else [])
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            mods = {m.group("mod") or m.group("frm") for m in _IMPORT_RE.finditer(text)}
            hits = sorted(mod for mod in mods for bad in FORBIDDEN_MODULES if mod == bad or mod.startswith(bad + "."))
            if hits:
                offenders[str(path.relative_to(ROOT))] = hits
    assert not offenders, f"side-effect imports: {offenders}"
    if lake_packages_available():
        assert scanned > 0


# --------------------------------------------------------------------------- design parameter owners


def test_lake_yaml_design_parameters_carry_an_owner():
    """Every top-level design parameter block of config/lake.yaml is owned (README section 2)."""
    import yaml

    raw = yaml.safe_load((ROOT / "config" / "lake.yaml").read_text(encoding="utf-8"))
    owners = raw.get("design_parameter_owners")
    assert isinstance(owners, dict) and owners, "config/lake.yaml has no design_parameter_owners block"
    run_keys = {"version", "seed", "n_devices", "history_start", "purchase_end", "as_of", "delivery_cadence",
                "launch_slip_months_max", "suppliers_hardware", "design_parameter_owners"}
    unowned = sorted(k for k in raw if k not in run_keys and k not in owners)
    assert not unowned, f"design parameter blocks without an owner in design_parameter_owners: {unowned}"
    families = raw.get("families") or {}
    for key, owner in owners.items():
        # a top-level block, or a per-family field carried by every family block (term_mix has its own owner)
        nested = bool(families) and all(isinstance(f, dict) and key in f for f in families.values())
        assert key in raw or nested, f"design_parameter_owners names {key}, which is not a block of lake.yaml"
        assert isinstance(owner, str) and owner.strip() and "(name)" in owner, (key, owner)
    # the README makes the claim; keep the sentence and the file in step
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "design_parameter_owners" in readme
