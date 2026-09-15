"""Restwert Engine: Asset P&L and residual-value tool for a DaaS provider.

Implements SPEC.md section 2 (foundation) at package level; v0.2 (docs/SPEC_v0.2.md)
adds the layered data lake and the closed device cycle on top of it. Every other module
of the package imports only from ``restwert.{paths,enums,config,schema,db,dates,records}``.

Governance principle (appears verbatim in README, CLI banner and dashboard footer):

    THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Nothing in this package sends, orders, lists or emails. All data shipped with
v0.1 and v0.2 is synthetic and labelled as such; the catalogue and the anchor curves are public.
"""

__version__ = "0.3.0"

GOVERNANCE_PRINCIPLE = (
    "THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD."
)

__all__ = ["__version__", "GOVERNANCE_PRINCIPLE"]
