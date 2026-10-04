"""fleetsim: the fleet at scale holds its chronology and its counts (small fleet, fast)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from restwert.fleetsim import FleetSim, load_config
from restwert.fleetsim.sim import STATIONS, load_catalogue, load_curves


@pytest.fixture(scope="module")
def tables():
    cfg = load_config()
    cfg["n_in_circulation"] = 3000
    cfg["n_in_warehouse"] = 1000
    sim = FleetSim(cfg, load_catalogue(), load_curves(), seed=7)
    t = sim.build()
    t["_sim"] = sim
    return t


def test_counts_match_the_instruction(tables):
    d = tables["devices"]
    assert (d["location"] == "kunde").sum() == 3000
    assert (d["location"] == "lager").sum() == 1000
    assert d["serial"].is_unique
    assert set(d.loc[d["location"] == "lager", "state"]).issubset(set(STATIONS))
    assert set(d.loc[d["location"] == "kunde", "state"]) == {"erstmiete", "zweitmiete"}


def test_chronology_per_serial(tables):
    d = tables["devices"]
    c = tables["contracts"].merge(d[["serial", "purchase_date", "launch_date"]], on="serial")
    assert (pd.to_datetime(d["launch_date"]) <= pd.to_datetime(d["purchase_date"])).all()
    assert (pd.to_datetime(c["start_date"]) >= pd.to_datetime(c["purchase_date"])).all()
    ended = c[c["status"] == "ended"]
    assert (pd.to_datetime(ended["actual_end"]) >= pd.to_datetime(ended["start_date"])).all()
    running = c[c["status"] == "running"]
    assert (pd.to_datetime(running["planned_end"]) >= pd.Timestamp("2026-09-21")).all()
    # a second-cycle device has exactly two contracts, a first-cycle one exactly one
    per = tables["contracts"].groupby("serial").size()
    two = d.loc[d["state"] == "zweitmiete", "serial"]
    assert (per.loc[two] == 2).all()


def test_no_fake_zeros_and_no_guessed_money(tables):
    d = tables["devices"]
    assert (d["purchase_price_net_eur"] > 0).all()
    assert (d["book_value_eur"] > 0).all()
    assert (d["residual_forecast_eur"] > 0).all()
    assert (d["book_value_eur"] <= d["purchase_price_net_eur"] + 0.01).all()


def test_return_calendar_covers_every_running_contract(tables):
    cal = tables["return_calendar"]
    d = tables["devices"]
    kunde = d[d["location"] == "kunde"]
    periods = pd.to_datetime(kunde["expected_return_date"]).dt.to_period("M")
    in_window = periods < pd.Period("2026-09", "M") + 48
    assert cal["summe_rueckgaben"].sum() == int(in_window.sum())
    assert (cal["naechster_schritt_zweitmiete"] + cal["naechster_schritt_reparatur_c"] + cal["naechster_schritt_restverkauf"] + cal["naechster_schritt_recycling"] == cal["summe_rueckgaben"]).all()


def test_the_flow_finding_is_reported(tables):
    sim = tables["_sim"]
    assert sim.notes, "the report must say what the warehouse count implies"
    assert "Liegezeit" in sim.notes[0]
    assert abs(sum(sim.flow_stock.values()) - float(np.nansum(list(sim.flow_stock.values())))) < 1e-6


def test_no_company_name_in_outputs(tables):
    import base64

    denied = [base64.b64decode(x).decode() for x in ("RXZlcnBob25l", "R3JvdmVy", "VGVhbVZpZXdlcg==")]
    text = " ".join(tables["kpis"].astype(str).values.ravel().tolist()) + " ".join(tables["_sim"].notes)
    for name in denied:
        assert name.lower() not in text.lower()
