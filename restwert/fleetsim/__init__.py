"""fleetsim: the device cycle at fleet scale.

One synthetic fleet of n serials, each with its history as an event log and its current
place: at a customer (first or second rental) or in one warehouse station. Everything a
warehouse KPI needs (availability, capital tied up, cover, aging, write-down risk, the
return calendar, the repair queue) is derived from those two tables, never typed in.

Scale and split are the business owner's instruction (config/fleetsim.yaml); the module
reports what the split implies instead of hiding it. No side effects: it reads the public
catalogue and the fitted public curves, and writes one DuckDB file plus CSV and Markdown
under outputs/fleetsim/.
"""

from restwert.fleetsim.sim import FleetSim, load_config, run

__all__ = ["FleetSim", "load_config", "run"]
