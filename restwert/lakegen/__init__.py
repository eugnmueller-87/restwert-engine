"""Lake generator: the synthetic fleet on the real catalogue, as landing files (SPEC_v0.2 section 5).

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

``generate_world`` builds, in memory, what nineteen source systems would export about
a fleet of several thousand serials drawn from ``data/catalogue`` (real models, real
launch dates, real launch RRPs, the ten catalogue manufacturers), priced with a truth
calibrated to the public anchor curves (``outputs/market_curves.csv``), and
``run_generate_lake`` writes them as landing files under ``data/lake/raw``.

Everything produced here is synthetic and says so: every synthetic row carries
``is_synthetic = true``, every synthetic landing file starts with ``# SYNTHETIC DATA``,
the three reference feeds are public copies with ``is_synthetic = false`` and a
``# PUBLIC DATA`` line, and ``data/lake/SYNTHETIC.md`` documents seed, counts, truth
sources, caps, exclusions, injected defects and the counterparty allowlist. Supplier
and counterparty names are the exact catalogue manufacturers or role-only names.
No market benchmark is quoted; no customer, employer or partner is real.

One ``numpy.random.default_rng(seed)`` is passed through the builders in this fixed
order: catalogue pool (no draws) -> fleet -> contracts register (dates) -> purchase ->
v0.1 rentals -> v0.1 events -> v0.1 refurbishment -> resale and credit notes ->
indirect spend -> defects -> writer (no draws). The register precedes the purchase
step because purchase orders reference its contract ids; its fleet-dependent spend
is filled afterwards by deterministic arithmetic. Same seed, same files, byte for byte.

Nothing here orders, lists, emails or writes to a partner; the only output is files
under the directory the caller names.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd

from restwert.lake.common import MANUFACTURERS
from restwert.lakegen import writer as _writer
from restwert.lakegen.calibrate import TruthCurve, load_curves, truth_curve
from restwert.lakegen.catalogue import FleetCatalogue, load_fleet_catalogue
from restwert.lakegen.config import CATALOGUE_DIR, LAKE_RAW_DIR, MARKET_CURVES_CSV, LakeConfig, load_lake_config
from restwert.lakegen.contracts import build_indirect, build_register, fill_register_spend
from restwert.lakegen.defects import inject
from restwert.lakegen.fleet import build_fleet
from restwert.lakegen.operations import build_operations
from restwert.lakegen.purchase import build_purchase
from restwert.lakegen.resale import build_resale
from restwert.lakegen.writer import (
    FEED_KEYS,
    FEEDS,
    SYNTHETIC_SENTENCE,
    delivery_periods,
    render_synthetic_md,
    write_landing_files,
    write_manifest,
    write_synthetic_md,
)
from restwert.records import RunSummary

DEFAULT_VAT_RATE: float = 0.19


@dataclass
class World:
    """Everything the generator built, in memory: landing frames by feed key plus helpers."""

    frames: dict[str, pd.DataFrame]
    v01: dict[str, pd.DataFrame]                 # the v0.1-shaped devices, rental_contracts, events, refurbishment, resale
    truth_sources: dict[str, str]                # "<Family> / <oem>" -> family_oem | family | default
    excluded_slugs: list[str]
    defects_injected: dict[str, int]
    seed: int
    n_serials: int
    # helpers filled on the way (not part of the section 5.4 contract, used by the writer and SYNTHETIC.md)
    fleet: pd.DataFrame = field(default_factory=pd.DataFrame)
    register: pd.DataFrame = field(default_factory=pd.DataFrame)
    excluded: pd.DataFrame = field(default_factory=pd.DataFrame)
    truth_curves: dict[str, TruthCurve] = field(default_factory=dict)
    written: dict[str, tuple[int, int]] = field(default_factory=dict)
    n_spares: int = 0
    n_usable_slugs: int = 0
    n_catalogue_slugs: int = 0
    n_pool_slugs: int = 0
    oem_redraws: int = 0
    vat_rate: float = DEFAULT_VAT_RATE
    role_only_names: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {k: int(len(v)) for k, v in self.frames.items()}


def _public_copy(path: Path, key: str) -> pd.DataFrame:
    """A public CSV as plain strings in the landing column order (missing file -> empty frame)."""
    cols = list(FEEDS[key].column_names)
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path, comment="#", dtype=str, keep_default_na=False, encoding="utf-8")
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df[cols].reset_index(drop=True)


def _role_only_names(world: World) -> tuple[str, ...]:
    names: set[str] = set()
    for key, cols in _writer.SERIAL_NAME_COLUMNS.items():
        df = world.frames.get(key)
        if df is None:
            continue
        for c in cols:
            if c in df.columns:
                names.update(str(v) for v in df[c].dropna().unique() if str(v) not in MANUFACTURERS)
    return tuple(sorted(names))


def generate_world(
    cfg: LakeConfig,
    cat: FleetCatalogue,
    curves: pd.DataFrame,
    seed: int | None = None,
    n_serials: int | None = None,
    *,
    catalogue_dir: Path = CATALOGUE_DIR,
    curves_path: Path = MARKET_CURVES_CSV,
) -> World:
    """Build every landing frame for ``n_serials`` serials (default ``cfg.n_devices``) with ``seed``."""
    seed = cfg.seed if seed is None else int(seed)
    n = cfg.n_devices if n_serials is None else int(n_serials)
    if n < 1:
        raise ValueError("n_serials must be >= 1")
    cfg = cfg.model_copy(update={"seed": seed, "n_devices": n}, deep=True)
    rng = np.random.default_rng(seed)

    world = World(
        frames={},
        v01={},
        truth_sources={},
        excluded_slugs=[str(s) for s in cat.excluded["slug"]],
        defects_injected={},
        seed=seed,
        n_serials=n,
        excluded=cat.excluded.copy(),
        n_usable_slugs=cat.n_usable,
        n_catalogue_slugs=int(len(cat.models)),
        n_pool_slugs=int(cat.pool["slug"].nunique()),
        vat_rate=float(getattr(cat, "vat_rate", DEFAULT_VAT_RATE)),
    )

    # reference feeds: public copies
    world.frames["catalogue/models"] = _public_copy(Path(catalogue_dir) / "models.csv", "catalogue/models")
    world.frames["catalogue/variants"] = _public_copy(Path(catalogue_dir) / "variants.csv", "catalogue/variants")
    world.frames["market/curves"] = _public_copy(Path(curves_path), "market/curves")

    # fleet -> register (dates) -> purchase -> register spend
    fleet = build_fleet(cfg, cat, rng, n)
    world.n_spares = int(fleet.attrs.get("n_spares", 0))
    world.oem_redraws = int(fleet.attrs.get("oem_redraws", 0))
    register = build_register(cfg, rng, cfg.as_of)
    purchase_frames, fleet = build_purchase(cfg, fleet, register, rng)
    world.frames.update(purchase_frames)
    world.fleet = fleet
    register = fill_register_spend(register, purchase_frames["erp/purchase_orders"], purchase_frames["erp/po_lines"], n)
    world.register = register
    world.frames["contracts/register"] = register

    # v0.1 rentals, events, refurbishment and the operational feeds; then resale on the calibrated truth
    build_operations(cfg, world, rng)
    build_resale(cfg, world, cat, curves, rng)
    for group in sorted(world.truth_sources):
        fam, oem = group.split(" / ", 1)
        world.truth_curves[group] = truth_curve(curves, fam, oem, cfg.truth_v2)

    # indirect spend, defects
    world.frames["finance/indirect_spend"] = build_indirect(cfg, register, rng)
    inject(world, cfg, rng)
    world.role_only_names = _role_only_names(world)

    missing = [k for k in FEED_KEYS if k not in world.frames]
    assert not missing, f"generator produced no frame for {missing}"
    return world


#: First lines that mark a landing file as written by this package (synthetic fleet feeds and
#: the public reference copies). Anything else in a landing folder is a real export.
GENERATED_HEADERS: tuple[str, ...] = ("# SYNTHETIC DATA", "# PUBLIC DATA")


def landing_file_is_generated(path: Path) -> bool:
    """True when the file's first line starts with ``# SYNTHETIC DATA`` or ``# PUBLIC DATA``."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return False
    return first.startswith(GENERATED_HEADERS)


def scan_landing_files(raw_dir: Path) -> tuple[list[Path], list[Path]]:
    """``(generated, real)`` landing CSVs under the nineteen feed folders of ``raw_dir``."""
    raw_dir = Path(raw_dir)
    generated: list[Path] = []
    real: list[Path] = []
    for spec in FEEDS.values():
        folder = raw_dir / spec.source_system / spec.feed
        if folder.is_dir():
            for p in sorted(folder.glob("*.csv")):
                (generated if landing_file_is_generated(p) else real).append(p)
    return generated, real


def clear_landing_files(raw_dir: Path, *, force: bool = False) -> int:
    """Remove the GENERATED landing files of the nineteen feeds (and the manifest) under ``raw_dir``; returns the count.

    A regenerated synthetic lake replaces the previous one; landing files are never modified
    in place, they are removed as a whole and written again. Only files whose first line is
    ``# SYNTHETIC DATA`` or ``# PUBLIC DATA`` are removed: a real export lands in the same
    folders (README section 10) and is never touched. When such a file is present the call
    raises ``ValueError`` naming it, unless ``force`` is given (the CLI flag ``--wipe-raw``).
    """
    raw_dir = Path(raw_dir)
    generated, real = scan_landing_files(raw_dir)
    if real and not force:
        shown = ", ".join(str(p.relative_to(raw_dir)) for p in real[:5])
        more = f" (+{len(real) - 5} more)" if len(real) > 5 else ""
        raise ValueError(
            f"refusing to remove {len(real)} landing file(s) under {raw_dir} whose first line is not "
            f"'# SYNTHETIC DATA' or '# PUBLIC DATA' (real exports are never modified after landing): "
            f"{shown}{more}; pass --wipe-raw to remove them or use another --lake-dir"
        )
    removed = 0
    for p in generated + (real if force else []):
        p.unlink()
        removed += 1
    manifest = raw_dir / _writer.MANIFEST_NAME
    if manifest.exists():
        manifest.unlink()
    return removed


def run_generate_lake(
    cfg: LakeConfig,
    seed: int,
    n_serials: int,
    raw_dir: Path = LAKE_RAW_DIR,
    catalogue_dir: Path = CATALOGUE_DIR,
    curves_path: Path = MARKET_CURVES_CSV,
    *,
    vat_rate: float | None = None,
    wipe_raw: bool = False,
) -> RunSummary:
    """Generate the world and write landing files, ``_manifest.json`` and ``SYNTHETIC.md`` (one level above ``raw_dir``).

    Earlier GENERATED landing files under ``raw_dir`` are removed first and the count is
    printed; a real export in a feed folder blocks the run (``ValueError``) unless ``wipe_raw``.
    """
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    raw_dir = Path(raw_dir)
    if vat_rate is None:
        vat_rate = _vat_rate_from_assumptions()
    cat = load_fleet_catalogue(Path(catalogue_dir), vat_rate=vat_rate)
    curves = load_curves(Path(curves_path))
    world = generate_world(cfg, cat, curves, seed=seed, n_serials=n_serials, catalogue_dir=catalogue_dir, curves_path=curves_path)
    world.vat_rate = float(vat_rate)
    removed = clear_landing_files(raw_dir, force=wipe_raw)
    if removed:
        print(f"  removed {removed} earlier generated landing file(s) under {raw_dir}")
    cfg_run = cfg.model_copy(update={"seed": int(seed), "n_devices": int(n_serials)}, deep=True)
    files = write_landing_files(world, raw_dir, cfg_run, int(seed))
    manifest = write_manifest(raw_dir, files, int(seed))
    md_path = write_synthetic_md(raw_dir.parent, render_synthetic_md(world, cfg_run, files))
    counts = {k: int(world.written.get(k, (0, 0))[1]) for k in FEED_KEYS}
    counts["files"] = len(files)
    counts["serials"] = int(n_serials)
    counts["spares"] = int(world.n_spares)
    for k, v in world.defects_injected.items():
        counts[f"defect_{k}"] = int(v)
    notes = [
        f"raw_dir={raw_dir}",
        f"manifest={manifest}",
        f"synthetic_md={md_path}",
        f"seed={seed}",
        f"n_serials={n_serials}",
        f"cadence={cfg_run.delivery_cadence} periods={len(delivery_periods(cfg_run))}",
        f"catalogue: {world.n_usable_slugs} usable of {world.n_catalogue_slugs} slugs, {world.n_pool_slugs} drawn from",
        f"replaced_landing_files={removed}",
    ]
    if world.oem_redraws:
        notes.append(f"oem_redraws={world.oem_redraws} (no slug of the drawn manufacturer launched by the order date)")
    defaults = sorted(g for g, s in world.truth_sources.items() if s == "default")
    if defaults:
        notes.append("truth default curve used (market_curves.csv missing) for: " + ", ".join(defaults))
    finished = datetime.now(timezone.utc)
    return RunSummary(
        command="generate-lake",
        run_id=f"generate-lake-{started:%Y%m%d%H%M%S}-{uuid4().hex[:4]}",
        started_at=started,
        finished_at=finished,
        seconds=round(time.perf_counter() - t0, 3),
        counts=counts,
        notes=notes,
    )


def _vat_rate_from_assumptions() -> float:
    """``assumptions.vat_rate`` when the block exists (v0.2), else the documented default."""
    try:
        from restwert.config import load_assumptions

        a = load_assumptions()
        if "vat_rate" in a.blocks:
            return float(a.get("vat_rate"))
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        pass
    return DEFAULT_VAT_RATE


__all__ = [
    "World",
    "generate_world",
    "run_generate_lake",
    "clear_landing_files",
    "scan_landing_files",
    "landing_file_is_generated",
    "GENERATED_HEADERS",
    "load_lake_config",
    "load_fleet_catalogue",
    "load_curves",
    "delivery_periods",
    "write_landing_files",
    "write_manifest",
    "render_synthetic_md",
    "write_synthetic_md",
    "SYNTHETIC_SENTENCE",
    "DEFAULT_VAT_RATE",
]
