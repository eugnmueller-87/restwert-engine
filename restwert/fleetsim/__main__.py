"""python -m restwert.fleetsim [--circulation N] [--warehouse N] [--as-of YYYY-MM-DD] [--seed S] [--config path] [--out dir] [--db path]"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from restwert.fleetsim.sim import load_config, run


def main() -> None:
    ap = argparse.ArgumentParser(description="Synthetic device fleet at scale: first rental, second rental, final sale, repairs, the warehouse.")
    ap.add_argument("--circulation", type=int, help="devices at customers (default from config/fleetsim.yaml)")
    ap.add_argument("--warehouse", type=int, help="devices in the warehouse (default from config)")
    ap.add_argument("--as-of", help="Stichtag YYYY-MM-DD (default from config)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--out", type=Path, help="output directory (default outputs/fleetsim)")
    ap.add_argument("--db", type=Path, help="DuckDB file (default data/fleet_mock.duckdb)")
    a = ap.parse_args()
    cfg = load_config(a.config)
    if a.circulation is not None:
        cfg["n_in_circulation"] = a.circulation
    if a.warehouse is not None:
        cfg["n_in_warehouse"] = a.warehouse
    if a.as_of:
        cfg["as_of"] = a.as_of
    t0 = time.perf_counter()
    res = run(cfg, out_dir=a.out, db_path=a.db, seed=a.seed)
    t = res["tables"]
    print(f"fleetsim: {len(t['devices']):,} devices, {len(t['contracts']):,} contracts, {len(t['events']):,} events in {time.perf_counter() - t0:.1f} s")
    print(f"  db: {res['db']}\n  out: {res['out_dir']}")
    for n in res["notes"]:
        print(f"  note: {n}")


if __name__ == "__main__":
    main()
