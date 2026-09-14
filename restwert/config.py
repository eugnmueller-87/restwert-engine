"""Configuration loaders and models for the four YAML files (SPEC.md section 2.4).

* ``config/generator.yaml``   -> ``GeneratorConfig``  (synthetic design parameters)
* ``config/thresholds.yaml``  -> ``Thresholds``       (every decision threshold with an owner)
* ``config/assumptions.yaml`` -> ``Assumptions``      (forward-looking assumptions with an owner)
* ``config/kpi_targets.yaml`` -> ``KpiTargets``       (savings plan and KPI targets with an owner)

A threshold without ``owner`` or ``valid_from`` is refused at load time. That is
the code-level form of "a named human owns every threshold". ``valid_from`` is not
decoration: ``Thresholds.get(key, sub, as_of)`` refuses a threshold whose
``valid_from`` lies after the ``as_of`` of the decision, so a rule can never fire
on a value that, by its own metadata, was not yet in force.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from restwert.paths import CONFIG_DIR
from restwert.records import ResolvedThreshold

# --------------------------------------------------------------------------- generator

#: The closed list of contract terms the simulation knows (months). The set is the business
#: owner's instruction; the shares per family (``term_mix``) are placeholders in the YAML.
TERM_MONTHS: tuple[int, ...] = (12, 24, 36, 48)
_TERM_MIX_TOL: float = 1e-6


class FamilyConfig(BaseModel):
    """Design parameters of one device family (synthetic, see generator.yaml)."""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    first_launch: date
    launch_cadence_months: int | None = None
    launch_month: int | None = None
    list_price_min: float
    list_price_max: float
    storage_options: list[int]
    base_storage_gb: int
    discount_min: float
    discount_max: float
    freight_duty_pct: float
    truth: dict[str, float]
    damage_rate_pa: float
    repair_share: float
    repair_cost_min: float
    repair_cost_max: float
    share_of_fleet: float
    # contract term mix ``{term_months: share}``: keys from TERM_MONTHS only, no negative share,
    # shares sum to 1 (tolerance 1e-6). Backward compatible: when ``term_mix`` is absent it is
    # derived from the v0.1 scalar ``term_mix_36`` as ``{24: 1 - term_mix_36, 36: term_mix_36}``;
    # when both are given, ``term_mix`` wins and ``term_mix_36`` is ignored.
    term_mix: dict[int, float] | None = None
    term_mix_36: float | None = None
    monthly_rate_pct_of_landed: float

    @model_validator(mode="after")
    def _check(self) -> "FamilyConfig":
        required = {
            "base", "lambda", "lambda_after_24", "step", "noise", "storage_exp",
            "grade_A", "grade_B", "grade_C", "grade_D",
        }
        missing = required - set(self.truth)
        if missing:
            raise ValueError(f"family {self.name or '?'}: truth block misses {sorted(missing)}")
        if self.list_price_min > self.list_price_max:
            raise ValueError(f"family {self.name or '?'}: list_price_min > list_price_max")
        self.term_mix = self._resolved_term_mix()
        return self

    def _resolved_term_mix(self) -> dict[int, float]:
        """``term_mix`` validated, or derived from ``term_mix_36`` when it is absent."""
        label = f"family {self.name or '?'}"
        mix = self.term_mix
        if mix is None:
            if self.term_mix_36 is None:
                raise ValueError(f"{label}: term_mix (or the legacy term_mix_36) is required")
            p36 = float(self.term_mix_36)
            if not (0.0 <= p36 <= 1.0):
                raise ValueError(f"{label}: term_mix_36 must be in [0, 1], got {p36}")
            mix = {24: 1.0 - p36, 36: p36}
        if not mix:
            raise ValueError(f"{label}: term_mix must not be empty")
        unknown = sorted(set(mix) - set(TERM_MONTHS))
        if unknown:
            raise ValueError(f"{label}: term_mix keys must be from {list(TERM_MONTHS)}, got {unknown}")
        out = {int(t): float(mix[t]) for t in sorted(mix)}
        negative = sorted(t for t, v in out.items() if v < 0.0)
        if negative:
            raise ValueError(f"{label}: term_mix shares must not be negative (terms {negative})")
        total = sum(out.values())
        if abs(total - 1.0) > _TERM_MIX_TOL:
            raise ValueError(f"{label}: term_mix shares must sum to 1 (tolerance {_TERM_MIX_TOL}), got {total}")
        return out

    def term_draw(self) -> tuple[list[int], list[float]]:
        """``(terms ascending, probabilities)`` for a weighted draw; probabilities renormalised exactly."""
        mix = self.term_mix if self.term_mix is not None else self._resolved_term_mix()
        terms = sorted(mix)
        total = float(sum(mix[t] for t in terms))
        return terms, [float(mix[t]) / total for t in terms]


class GeneratorConfig(BaseModel):
    """Everything the synthetic generator needs; all values are design parameters."""

    model_config = ConfigDict(extra="ignore")

    seed: int
    n_devices: int
    history_start: date
    purchase_end: date
    as_of: date
    families: dict[str, FamilyConfig]
    grade_pre_return_mix: dict[str, float]
    grade_drift_worse: float
    grade_drift_better: float
    channel_mix_by_grade: dict[str, dict[str, float]]
    channel_mult: dict[str, float]
    fee_pct: dict[str, float]
    fee_fixed_eur: dict[str, float]
    days_to_sale: dict[str, list[int]]
    refurb_days: dict[str, list[int]]
    refurb_cost: dict[str, list[float]]
    scrap_share_of_d: float
    early_termination_rate: float
    # monthly rate factor per contract term relative to the 24-month base (24 == 1.0): a short
    # term must recover more of the landed cost per month, a long term less. Placeholders with
    # an owner in config/lake.yaml; the default keeps the v0.1 files (one flat rate) valid.
    term_rate_factor: dict[int, float] = Field(default_factory=lambda: {t: 1.0 for t in TERM_MONTHS})
    replacement_share_of_damage: float
    open_damage_share: float
    otif_on_time_share: float
    po_short_delivery_share: float
    price_drop_share: float
    price_drop_pct: float
    suppliers_hardware: list[str]
    suppliers_indirect: list[str]
    indirect_categories: list[str]
    indirect_rows_per_month: int
    indirect_has_po: float
    indirect_has_contract: float
    indirect_saving_share: float
    indirect_confirmed_share: float
    # share of sellable devices that do not sell inside the channel's normal window but
    # linger 90 to 400 days (design parameter; gives the aging KPIs and rule R03 subjects)
    slow_mover_share: float = 0.04
    slow_mover_days: list[int] = Field(default_factory=lambda: [90, 400])
    # launch slip: each generation's real launch date deviates from the calendar rule by a
    # seeded integer offset in [-launch_slip_months_max, +launch_slip_months_max] months
    # (0 = exact calendar). With slip, the forecaster's calendar assumption for launches
    # after as_of is wrong by construction, so the backtest carries launch-timing risk.
    launch_slip_months_max: int = 0
    # how claimed indirect savings split by type (hard price reduction, cost avoidance, rebate)
    indirect_saving_type_mix: dict[str, float] = Field(
        default_factory=lambda: {"hard_price_reduction": 0.60, "cost_avoidance": 0.30, "rebate": 0.10}
    )

    @model_validator(mode="after")
    def _check_term_rate_factor(self) -> "GeneratorConfig":
        unknown = sorted(set(self.term_rate_factor) - set(TERM_MONTHS))
        if unknown:
            raise ValueError(f"term_rate_factor keys must be from {list(TERM_MONTHS)}, got {unknown}")
        missing = sorted(set(TERM_MONTHS) - set(self.term_rate_factor))
        if missing:
            raise ValueError(f"term_rate_factor must name every term of {list(TERM_MONTHS)}, missing {missing}")
        if any(float(v) <= 0 for v in self.term_rate_factor.values()):
            raise ValueError("term_rate_factor values must be positive")
        if abs(float(self.term_rate_factor[24]) - 1.0) > 1e-9:
            raise ValueError("term_rate_factor[24] is the base and must be 1.0")
        return self

    @model_validator(mode="after")
    def _fill_names(self) -> "GeneratorConfig":
        for name, fc in self.families.items():
            if not fc.name:
                fc.name = name
        if self.history_start > self.purchase_end or self.purchase_end > self.as_of:
            raise ValueError("generator config requires history_start <= purchase_end <= as_of")
        return self


# --------------------------------------------------------------------------- thresholds


class ThresholdSpec(BaseModel):
    """One threshold: exactly one of ``value`` / ``values``, plus unit, owner, rationale."""

    model_config = ConfigDict(extra="forbid")

    value: float | int | str | bool | None = None
    values: dict[str, float | int | str | bool] | None = None
    unit: str
    owner: str
    rationale: str
    valid_from: date
    placeholder_default: bool = True
    rule_ids: list[str]

    @model_validator(mode="after")
    def _exactly_one(self) -> "ThresholdSpec":
        if (self.value is None) == (self.values is None):
            raise ValueError("exactly one of value / values must be set")
        if not self.owner or not str(self.owner).strip():
            raise ValueError("owner must be non-empty")
        return self


class Thresholds(BaseModel):
    """All thresholds of ``config/thresholds.yaml``."""

    version: int
    thresholds: dict[str, ThresholdSpec]

    def get(self, key: str, sub: str | None = None, as_of: date | None = None) -> ResolvedThreshold:
        """Resolve a threshold; ``sub`` (family or channel) is required when ``values`` is set.

        Raises ``KeyError`` naming the key when it is missing, ``ValueError`` when a
        per-family threshold is asked for without ``sub``, and ``ValueError`` when
        ``as_of`` is given and the threshold's ``valid_from`` lies after it (the
        threshold was not in force on the decision date). Without ``as_of`` no
        validity check is made (documentation rendering, tests).
        """
        if key not in self.thresholds:
            raise KeyError(f"threshold {key!r} not found in thresholds.yaml")
        spec = self.thresholds[key]
        if as_of is not None and spec.valid_from > as_of:
            raise ValueError(
                f"threshold {key!r} is valid from {spec.valid_from.isoformat()} but the decision "
                f"as_of is {as_of.isoformat()}; a threshold cannot fire before it is in force"
            )
        if spec.values is not None:
            if sub is None:
                raise ValueError(f"threshold {key!r} has per-key values; sub is required")
            if sub not in spec.values:
                raise KeyError(f"threshold {key!r} has no value for {sub!r}")
            return ResolvedThreshold(
                key=key,
                resolved_key=f"{key}[{sub}]",
                value=spec.values[sub],
                unit=spec.unit,
                owner=spec.owner,
                rule_ids=list(spec.rule_ids),
                valid_from=spec.valid_from,
            )
        assert spec.value is not None
        return ResolvedThreshold(
            key=key,
            resolved_key=key,
            value=spec.value,
            unit=spec.unit,
            owner=spec.owner,
            rule_ids=list(spec.rule_ids),
            valid_from=spec.valid_from,
        )


# --------------------------------------------------------------------------- assumptions


class AssumptionBlock(BaseModel):
    """One forward-looking assumption with an owner."""

    model_config = ConfigDict(extra="ignore")

    owner: str
    value: float | int | str | bool | None = None
    values: dict[str, Any] | None = None
    note: str = ""


class Assumptions(BaseModel):
    """All blocks of ``config/assumptions.yaml``."""

    version: int
    blocks: dict[str, AssumptionBlock]

    def get(self, key: str, sub: str | None = None) -> Any:
        """Return ``values[sub]`` when ``sub`` is given, else ``value`` (or the whole ``values`` map)."""
        if key not in self.blocks:
            raise KeyError(f"assumption {key!r} not found in assumptions.yaml")
        block = self.blocks[key]
        if sub is not None:
            if block.values is None or sub not in block.values:
                raise KeyError(f"assumption {key!r} has no value for {sub!r}")
            return block.values[sub]
        if block.values is not None:
            return block.values
        return block.value

    def owner(self, key: str) -> str:
        if key not in self.blocks:
            raise KeyError(f"assumption {key!r} not found in assumptions.yaml")
        return self.blocks[key].owner


# --------------------------------------------------------------------------- kpi targets


class KpiTargets(BaseModel):
    """Savings plan and KPI targets, each with an owner."""

    model_config = ConfigDict(extra="ignore")

    version: int
    savings_plan_eur: dict[int, float]
    savings_plan_owner: str
    targets: dict[str, float] = Field(default_factory=dict)
    targets_owner: str
    # minimum sample size per gold KPI (v0.2): below it the value is stored but flagged not_measurable
    min_n: dict[str, int] = Field(default_factory=dict)
    min_n_owner: str = ""


# --------------------------------------------------------------------------- loaders


def _read_yaml(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return raw


def load_generator_config(path: Path = CONFIG_DIR / "generator.yaml") -> GeneratorConfig:
    """Load and validate ``config/generator.yaml``."""
    return GeneratorConfig.model_validate(_read_yaml(path))


def load_thresholds(path: Path = CONFIG_DIR / "thresholds.yaml") -> Thresholds:
    """Load ``config/thresholds.yaml``; refuses a threshold without owner or valid_from."""
    raw = _read_yaml(path)
    specs = raw.get("thresholds")
    if not isinstance(specs, dict) or not specs:
        raise ValueError(f"{path}: 'thresholds' mapping missing or empty")
    for key, spec in specs.items():
        if not isinstance(spec, dict):
            raise ValueError(f"threshold {key} is not a mapping")
        owner = spec.get("owner")
        if owner is None or not str(owner).strip():
            raise ValueError(f"threshold {key} has no owner")
        if spec.get("valid_from") is None:
            raise ValueError(f"threshold {key} has no valid_from")
        if ("value" in spec) == ("values" in spec):
            raise ValueError(f"threshold {key} must set exactly one of value / values")
    return Thresholds.model_validate(raw)


def load_assumptions(path: Path = CONFIG_DIR / "assumptions.yaml") -> Assumptions:
    """Load ``config/assumptions.yaml``; every block needs an owner."""
    raw = _read_yaml(path)
    blocks = raw.get("blocks")
    if not isinstance(blocks, dict) or not blocks:
        raise ValueError(f"{path}: 'blocks' mapping missing or empty")
    for key, block in blocks.items():
        if not isinstance(block, dict) or not str(block.get("owner", "")).strip():
            raise ValueError(f"assumption block {key} has no owner")
    return Assumptions.model_validate(raw)


def load_kpi_targets(path: Path = CONFIG_DIR / "kpi_targets.yaml") -> KpiTargets:
    """Load ``config/kpi_targets.yaml``."""
    raw = _read_yaml(path)
    for owner_key in ("savings_plan_owner", "targets_owner"):
        if not str(raw.get(owner_key, "")).strip():
            raise ValueError(f"kpi_targets.yaml: {owner_key} missing")
    return KpiTargets.model_validate(raw)


def file_sha256(path: Path) -> str:
    """sha256 hex digest of a file (used to stamp the thresholds version onto a run)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


__all__ = [
    "TERM_MONTHS",
    "FamilyConfig",
    "GeneratorConfig",
    "ThresholdSpec",
    "Thresholds",
    "AssumptionBlock",
    "Assumptions",
    "KpiTargets",
    "load_generator_config",
    "load_thresholds",
    "load_assumptions",
    "load_kpi_targets",
    "file_sha256",
]
