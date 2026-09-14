"""Lake generator configuration (SPEC_v0.2 sections 3.5 and 5.1).

``LakeConfig`` is a ``GeneratorConfig`` (every v0.1 field is present, so the v0.1
builders and ``run_forecast`` accept it wherever they take ``cfg``) plus the v0.2
blocks: fleet mix, manufacturer shares, supplier route, discount bands, cost bands,
the calibrated truth (``truth_v2``) and the injected defects. Every value is a
synthetic design parameter; the file says so in its header.

Path constants: ``restwert.paths`` gains ``LAKE_DIR``, ``LAKE_RAW_DIR``,
``CATALOGUE_DIR``, ``MARKET_CURVES_CSV`` and ``LAKE_CONFIG`` in v0.2 (module 1 owns
that edit). This module re-exports them and derives the same values from the v0.1
constants when the edit is not in place yet, so the generator never depends on the
order in which the v0.2 modules land.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from restwert import paths as _paths
from restwert.config import GeneratorConfig, _read_yaml
from restwert.lake.common import CATALOGUE_FAMILIES, FLEET_FAMILIES, MANUFACTURERS

LAKE_DIR: Path = getattr(_paths, "LAKE_DIR", _paths.DATA_DIR / "lake")
LAKE_RAW_DIR: Path = getattr(_paths, "LAKE_RAW_DIR", LAKE_DIR / "raw")
CATALOGUE_DIR: Path = getattr(_paths, "CATALOGUE_DIR", _paths.DATA_DIR / "catalogue")
MARKET_CURVES_CSV: Path = getattr(_paths, "MARKET_CURVES_CSV", _paths.OUTPUTS_DIR / "market_curves.csv")
LAKE_CONFIG: Path = getattr(_paths, "LAKE_CONFIG", _paths.CONFIG_DIR / "lake.yaml")

_SUM_TOL = 1e-6


class TruthV2(BaseModel):
    """The calibrated truth: public ask curve, hair-cut, cap, noise, clip (decision D12)."""

    model_config = ConfigDict(extra="forbid")

    q_young_cap: float
    ask_to_realised: float
    grade_d_offset_default: float
    noise_sigma: float
    ratio_min: float
    ratio_max: float
    default_curve: dict[str, float]
    owner: str

    @model_validator(mode="after")
    def _check(self) -> "TruthV2":
        if not (0.0 < self.ratio_min < self.ratio_max <= 1.0):
            raise ValueError("truth_v2 requires 0 < ratio_min < ratio_max <= 1")
        if not (0.0 < self.ask_to_realised <= 1.0) or not (0.0 < self.q_young_cap <= 1.0):
            raise ValueError("truth_v2 ask_to_realised and q_young_cap must be in (0, 1]")
        for key in ("intercept", "slope_per_month"):
            if key not in self.default_curve:
                raise ValueError(f"truth_v2.default_curve misses {key!r}")
        if not str(self.owner).strip():
            raise ValueError("truth_v2 needs an owner")
        return self


class DefectsBlock(BaseModel):
    """Shares of deliberately broken rows, so that ingest has something to refuse."""

    model_config = ConfigDict(extra="forbid")

    unknown_serial_share: float
    identical_duplicate_share: float
    conflicting_duplicate_share: float
    missing_credit_note_share: float
    orphan_freight_share: float

    @model_validator(mode="after")
    def _check(self) -> "DefectsBlock":
        for name, value in self.model_dump().items():
            if not (0.0 <= float(value) < 1.0):
                raise ValueError(f"defects.{name} must be in [0, 1)")
        return self


def _check_band(name: str, band: list[float]) -> None:
    if len(band) != 2 or band[0] > band[1]:
        raise ValueError(f"{name} must be [min, max] with min <= max, got {band}")


class LakeConfig(GeneratorConfig):
    """``config/lake.yaml``: every v0.1 generator field plus the v0.2 blocks."""

    model_config = ConfigDict(extra="ignore")

    delivery_cadence: Literal["yearly", "quarterly", "monthly"] = "quarterly"
    fleet_mix: dict[str, float]
    oem_share: dict[str, dict[str, float]]
    supplier_route: dict[str, float]
    resellers: list[str]
    discount_by_oem: dict[str, list[float]]
    reseller_markup_pct: list[float]
    newest_model_share: float
    # p(a slug of the PREVIOUS generation); newest_model_share + previous_generation_share <= 1,
    # the remainder goes to the older pool (fleet.build_fleet)
    previous_generation_share: float
    # a slug launched inside this many months up to the NEWEST launch of its (family, oem) on or
    # before the order date belongs to the current generation; the window before it is the
    # previous generation (fleet._SlugIndex.generations)
    generation_window_months: int
    freight_per_unit_eur: list[float]
    duty_pct_reseller_b: float
    staging_cost_eur: list[float]
    outbound_cost_eur: list[float]
    wipe_grading_cost_eur: list[float]
    mdm_enrolled_share: float
    wipe_certificate_share: float
    price_protection_claim_share: float
    # days from sale to credit note per channel; must equal assumptions.channel_fees (tested)
    days_to_cash: dict[str, int]
    truth_v2: TruthV2
    defects: DefectsBlock

    @model_validator(mode="after")
    def _check_lake(self) -> "LakeConfig":
        if tuple(sorted(self.families)) != tuple(sorted(FLEET_FAMILIES)):
            raise ValueError(f"lake.yaml families must be exactly {FLEET_FAMILIES}, got {tuple(self.families)}")
        if set(self.fleet_mix) != set(CATALOGUE_FAMILIES):
            raise ValueError(f"fleet_mix keys must be {CATALOGUE_FAMILIES}, got {tuple(self.fleet_mix)}")
        if abs(sum(self.fleet_mix.values()) - 1.0) > _SUM_TOL:
            raise ValueError("fleet_mix must sum to 1")
        if set(self.oem_share) != set(CATALOGUE_FAMILIES):
            raise ValueError(f"oem_share needs one map per catalogue family {CATALOGUE_FAMILIES}")
        for family, shares in self.oem_share.items():
            unknown = sorted(set(shares) - set(MANUFACTURERS))
            if unknown:
                raise ValueError(f"oem_share[{family}] names non-catalogue manufacturers {unknown}")
            if abs(sum(shares.values()) - 1.0) > _SUM_TOL:
                raise ValueError(f"oem_share[{family}] must sum to 1")
            missing = sorted(set(shares) - set(self.discount_by_oem))
            if missing:
                raise ValueError(f"discount_by_oem misses {missing} used in oem_share[{family}]")
        if set(self.supplier_route) != {"manufacturer", "reseller"}:
            raise ValueError("supplier_route needs exactly the keys manufacturer and reseller")
        if abs(sum(self.supplier_route.values()) - 1.0) > _SUM_TOL:
            raise ValueError("supplier_route must sum to 1")
        if not self.resellers:
            raise ValueError("resellers must not be empty")
        for oem, band in self.discount_by_oem.items():
            if oem not in MANUFACTURERS:
                raise ValueError(f"discount_by_oem names non-catalogue manufacturer {oem!r}")
            _check_band(f"discount_by_oem[{oem}]", band)
        for name in ("reseller_markup_pct", "freight_per_unit_eur", "staging_cost_eur", "outbound_cost_eur",
                     "wipe_grading_cost_eur"):
            _check_band(name, getattr(self, name))
        for name in ("newest_model_share", "previous_generation_share", "mdm_enrolled_share", "wipe_certificate_share",
                     "price_protection_claim_share", "duty_pct_reseller_b"):
            if not (0.0 <= float(getattr(self, name)) <= 1.0):
                raise ValueError(f"{name} must be in [0, 1]")
        if float(self.newest_model_share) + float(self.previous_generation_share) > 1.0 + _SUM_TOL:
            raise ValueError("newest_model_share + previous_generation_share must be <= 1 (the remainder is the older pool)")
        if int(self.generation_window_months) < 1:
            raise ValueError("generation_window_months must be >= 1")
        channels = set(self.channel_mult)
        for name in ("fee_pct", "fee_fixed_eur", "days_to_cash", "days_to_sale"):
            if set(getattr(self, name)) != channels:
                raise ValueError(f"{name} must cover exactly the channels of channel_mult {sorted(channels)}")
        return self


def load_lake_config(path: Path = LAKE_CONFIG) -> LakeConfig:
    """Load and validate ``config/lake.yaml``."""
    return LakeConfig.model_validate(_read_yaml(Path(path)))


__all__ = [
    "TruthV2",
    "DefectsBlock",
    "LakeConfig",
    "load_lake_config",
    "LAKE_DIR",
    "LAKE_RAW_DIR",
    "CATALOGUE_DIR",
    "MARKET_CURVES_CSV",
    "LAKE_CONFIG",
]
