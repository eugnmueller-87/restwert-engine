"""Public market anchors: catalogue models with launch RRP and today's used prices.

THE MODEL ADVISES, DETERMINISTIC CODE DECIDES, A NAMED HUMAN OWNS EVERY THRESHOLD.

Everything under ``data/catalogue`` and ``data/anchors`` is public information
(manufacturer press releases, trade press, refurbished marketplaces, trade-in
pages), each row with a URL and the date it was seen. Nothing here is a number
of any DaaS provider. The rental price is the one figure that is not public and
therefore stays a parameter, never a fact.
"""

from restwert.market.anchors import load_anchors, normalise_grade
from restwert.market.curves import fit_curves
from restwert.market.run import run_market

__all__ = ["load_anchors", "normalise_grade", "fit_curves", "run_market"]
