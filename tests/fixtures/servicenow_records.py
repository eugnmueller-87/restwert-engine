"""SYNTHETIC ServiceNow-shaped records for ``tests/test_connector_servicenow.py`` (no network).

The shape follows the Table API (``result`` rows with ``sys_updated_on`` and
reference fields as ``{"value": ..., "link": ...}``); every value is invented.
``RECORDS`` is also written as ``tests/fixtures/servicenow_sample.json`` for the
``--fixture`` flag of the connector.
"""

from __future__ import annotations

RECORDS: list[dict] = [
    {
        "sys_id": "aaaa0001", "number": "INC0010001", "sys_updated_on": "2026-09-01 10:15:00",
        "opened_at": "2026-09-01 09:00:00", "closed_at": "2026-09-03 15:30:00",
        "u_device_serial": "SN-T-0001", "u_rental_contract": {"value": "RC-T-01", "link": "https://example.org/rc/1"},
        "u_damage_type": "Display", "u_resolution": "Repaired", "u_quote_eur": "120.00", "u_repair_cost_eur": "95,50",
        "u_replacement_serial": "", "u_repair_partner": "",
    },
    {
        "sys_id": "aaaa0002", "number": "INC0010002", "sys_updated_on": "2026-09-02 08:00:00",
        "opened_at": "2026-09-02 07:45:00", "closed_at": "",
        "u_device_serial": "SN-T-0002", "u_rental_contract": "",
        "u_damage_type": "Battery", "u_resolution": "In progress", "u_quote_eur": "80", "u_repair_cost_eur": "",
        "u_replacement_serial": "", "u_repair_partner": "",
    },
    {
        "sys_id": "aaaa0003", "number": "INC0010003", "sys_updated_on": "2026-09-05 12:00:00",
        "opened_at": "2026-09-04 11:00:00", "closed_at": "2026-09-05 12:00:00",
        "u_device_serial": "SN-T-0003", "u_rental_contract": {"value": "RC-T-03", "link": "https://example.org/rc/3"},
        "u_damage_type": "Cracked case", "u_resolution": "Replaced", "u_quote_eur": "310.00", "u_repair_cost_eur": "0",
        "u_replacement_serial": "SN-T-0099", "u_repair_partner": "",
    },
]

__all__ = ["RECORDS"]
