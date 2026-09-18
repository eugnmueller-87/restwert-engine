"""SYNTHETIC test rows for the interface tests (``tests/test_api.py``).

Every value here is invented for the test: serials, order numbers, prices and
dates are design choices, not figures of any house. Supplier and partner names
follow the allowlist rule (a catalogue manufacturer or ``(role-only)``); the
denylist test scans this file like every other ``.py`` file.
"""

from __future__ import annotations

CAT_MODELS: list[dict] = [
    {"slug": "phone-t1", "model_name": "Phone T1", "oem": "Samsung", "family": "Smartphone", "series": "T",
     "launch_date_de": "2024-02-01", "launch_date_kind": "verfuegbarkeit", "launch_source_url": "https://example.org/t1"},
]

CAT_VARIANTS: list[dict] = [
    {"slug": "phone-t1", "spec": "256 GB", "storage_gb": 256, "rrp_eur_launch_de": 899,
     "rrp_source_url": "https://example.org/t1", "rrp_source_date": "2024-02-01"},
]

ERP_PURCHASE_ORDERS: list[dict] = [
    {"po_number": "PO-T-1001", "supplier_id": "SUP-01", "supplier_name": "Samsung", "supplier_role": "manufacturer",
     "order_date": "2025-03-03", "promised_date": "2025-03-20", "currency": "EUR"},
    {"po_number": "PO-T-1002", "supplier_id": "SUP-02", "supplier_name": "IT reseller A (role-only)", "supplier_role": "reseller",
     "order_date": "2025-04-01", "promised_date": "2025-04-15", "currency": "EUR"},
]

ERP_PO_LINES: list[dict] = [
    # resolves: PO known, variant priced
    {"po_number": "PO-T-1001", "po_line": 1, "slug": "phone-t1", "storage_gb": 256, "qty_ordered": 10,
     "unit_price_eur": 640.5, "order_date": "2025-03-03"},
    # unknown_po: the header was never delivered
    {"po_number": "PO-T-9999", "po_line": 1, "slug": "phone-t1", "storage_gb": 256, "qty_ordered": 1,
     "unit_price_eur": 640.5, "order_date": "2025-03-03"},
]

SD_TICKETS: list[dict] = [
    # unknown_serial: no goods receipt minted the serial (the door rule)
    {"ticket_id": "T-0001", "serial": "SN-T-0001", "contract_id": None, "opened_at": "2026-09-01T09:00:00",
     "closed_at": "2026-09-03T15:30:00", "damage_type": "screen", "resolution": "repair", "quote_eur": 120.0,
     "repair_cost_eur": 95.5, "replacement_serial": None, "repair_partner_ref": "Refurbishment and repair partner (role-only)"},
    # bad_enum on damage_type and negative_amount on quote_eur, in one row
    {"ticket_id": "T-0002", "serial": "SN-T-0002", "contract_id": None, "opened_at": "2026-09-02T09:00:00",
     "closed_at": None, "damage_type": "glass", "resolution": "open", "quote_eur": -5,
     "repair_cost_eur": None, "replacement_serial": None, "repair_partner_ref": "Refurbishment and repair partner (role-only)"},
]

__all__ = ["CAT_MODELS", "CAT_VARIANTS", "ERP_PURCHASE_ORDERS", "ERP_PO_LINES", "SD_TICKETS"]
