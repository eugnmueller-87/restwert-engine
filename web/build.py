"""Build the Restwert Engine page: web/dist/index.html from the shell, the motors and the data of nine tabs.

    python web/build.py                # embed web/data/*.json into web/dist/ (no engine run needed)
    python web/build.py --generate     # first regenerate web/data/*.json from outputs/ and data/restwert.duckdb
    python web/build.py --generate --today 2026-09-16

Steps:
  1. (--generate) run web/tools/gen/make_<tab>_data.py for every tab against the repository root; each reads the
     tables and files that `python -m restwert all` wrote (outputs/*.csv, data/restwert.duckdb, config/*.yaml,
     data/catalogue/*.csv) and writes web/data/<tab>.json. No number is typed anywhere in the page.
  2. web/data/config.json from config/assumptions.yaml (purchase discount: value and owner), plus two derived
     fields the motors read (cycle.json n_models, term.json sim_discount_min/max).
  3. web/dist/index.html from web/index.template.html: the data of every tab inline as
     <script type="application/json" id="data-<tab>">; styles.css, app.js and engine/*.js copied next to it.

The page is the multi-file artifact published on claude.ai; the same files serve from any static host.
Encoding: UTF-8 without BOM, LF. The build refuses en and em dashes in the page (project rule).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent
REPO = WEB.parent
DIST = WEB / "dist"
DATA = WEB / "data"
ENGINE = WEB / "engine"
TEMPLATE = WEB / "index.template.html"
ASSUMPTIONS = REPO / "config" / "assumptions.yaml"
LAKE = REPO / "config" / "lake.yaml"

TABS = ["report", "device", "market", "forecast", "tco", "cycle", "levers", "term", "lake"]
GENERATOR_INPUTS = [REPO / "outputs" / "kpi_values.csv", REPO / "outputs" / "rv_forecast_error_monthly.csv", REPO / "data" / "restwert.duckdb"]


def write_utf8(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def generate(today: str) -> None:
    missing = [p for p in GENERATOR_INPUTS if not p.exists()]
    if missing:
        raise SystemExit("--generate needs an engine run first (python -m restwert all); missing: " + ", ".join(str(p) for p in missing))
    for tab in TABS:
        script = WEB / "tools" / "gen" / f"make_{tab}_data.py"
        out = DATA / f"{tab}.json"
        cmd = [sys.executable, str(script), str(REPO), str(out), today]
        print(f"generate {tab}: {script.name}")
        subprocess.run(cmd, check=True, cwd=str(WEB / "tools" / "gen"))


def build_config() -> dict:
    import yaml

    with open(ASSUMPTIONS, encoding="utf-8") as fh:
        y = yaml.safe_load(fh)
    pd_ = (y.get("blocks") or {}).get("purchase_discount_pct")
    if not pd_ or "value" not in pd_:
        raise SystemExit("assumptions.yaml: block purchase_discount_pct without value")
    entry = {"value": float(pd_["value"]), "owner": str(pd_.get("owner", "")), "note": str(pd_.get("note", "")), "min": None, "max": None}
    bounds = pd_.get("bounds") or pd_.get("range")
    if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
        entry["min"], entry["max"] = float(bounds[0]), float(bounds[1])
    return {"source": "config/assumptions.yaml", "version": y.get("version"), "purchase_discount_pct": entry}


def set_keys(path: Path, patch: dict) -> None:
    """Slot keys into a JSON object file without rewriting anything else."""
    obj = json.loads(path.read_text(encoding="utf-8"))
    if all(k in obj and obj[k] == v for k, v in patch.items()):
        return
    obj.update(patch)
    write_utf8(path, json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def augment_data() -> None:
    """Two derived fields the motors read: computed, never typed."""
    dev = json.loads((DATA / "device.json").read_text(encoding="utf-8"))
    slugs = {x["slug"] for x in dev.get("devices", []) if x.get("rrp") and x.get("launch") and x.get("launch_kind") == "verfuegbarkeit"}
    set_keys(DATA / "cycle.json", {"n_models": len(slugs)})
    import yaml

    with open(LAKE, encoding="utf-8") as fh:
        y = yaml.safe_load(fh)
    rng = (y.get("discount_by_oem") or {}).get("Apple")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        set_keys(DATA / "term.json", {"sim_discount_min": float(rng[0]), "sim_discount_max": float(rng[1])})


def json_for_script(text: str) -> str:
    return text.replace("</", "<\\/")


def embed() -> Path:
    cfg = build_config()
    cfg_text = json.dumps(cfg, ensure_ascii=False, indent=2)
    write_utf8(DATA / "config.json", cfg_text + "\n")
    augment_data()
    DIST.mkdir(parents=True, exist_ok=True)
    (DIST / "engine").mkdir(exist_ok=True)
    for src in sorted(ENGINE.glob("*.js")):
        shutil.copyfile(src, DIST / "engine" / src.name)
    for name in ("styles.css", "app.js", "index.template.html"):
        shutil.copyfile(WEB / name, DIST / name)
    blocks = []
    total = 0
    for tab in TABS:
        p = DATA / f"{tab}.json"
        if not p.exists():
            raise SystemExit(f"data missing: {p} (run with --generate)")
        raw = p.read_text(encoding="utf-8")
        json.loads(raw)
        total += len(raw)
        blocks.append(f'<script type="application/json" id="data-{tab}">{json_for_script(raw)}</script>')
    blocks.append(f'<script type="application/json" id="data-config">{json_for_script(cfg_text)}</script>')
    tpl = TEMPLATE.read_text(encoding="utf-8")
    if "<!--DATA-->" not in tpl:
        raise SystemExit("index.template.html without the <!--DATA--> marker")
    html = tpl.replace("<!--DATA-->", "\n".join(blocks))
    if "–" in html or "—" in html:
        raise SystemExit("en or em dash in index.html; the page must not carry one")
    out = DIST / "index.html"
    write_utf8(out, html)
    print(f"{out}: {len(html):,} characters, {total:,} of them data")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--generate", action="store_true", help="regenerate web/data/*.json from the engine outputs first")
    ap.add_argument("--today", default=dt.date.today().isoformat(), help="date shown as 'Stand' on the page (YYYY-MM-DD)")
    args = ap.parse_args()
    if args.generate:
        generate(args.today)
    embed()
    return 0


if __name__ == "__main__":
    sys.exit(main())
