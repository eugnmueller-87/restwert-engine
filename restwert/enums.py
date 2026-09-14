"""Closed lists as string enums (SPEC.md section 2.1).

Every categorical column in the data model is validated against one of these
lists by ``restwert.schema.validate_frame``.
"""

from __future__ import annotations

from enum import StrEnum


class Family(StrEnum):
    IPHONE_LIKE = "iphone_like"
    ANDROID_LIKE = "android_like"
    LAPTOP_LIKE = "laptop_like"
    TABLET_LIKE = "tablet_like"  # v0.2


class Grade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class ChannelIn(StrEnum):
    DISTRIBUTOR = "distributor"
    OEM_DIRECT = "oem_direct"
    REFURB_BUYBACK = "refurb_buyback"


class ContractStatus(StrEnum):
    ACTIVE = "active"
    ENDED = "ended"
    TERMINATED_EARLY = "terminated_early"
    REPLACED = "replaced"


class EventType(StrEnum):
    DAMAGE = "damage"
    REPAIR = "repair"
    REPLACEMENT = "replacement"
    RETURN = "return"


class DamageType(StrEnum):
    SCREEN = "screen"
    BATTERY = "battery"
    HOUSING = "housing"
    WATER = "water"
    OTHER = "other"


class RefurbOutcome(StrEnum):
    SELLABLE = "sellable"
    AS_IS = "as_is"
    SCRAP = "scrap"


class Channel(StrEnum):
    EMPLOYEE_BUYOUT = "employee_buyout"
    MARKETPLACE = "marketplace"
    B2B_WHOLESALE = "b2b_wholesale"
    AS_IS = "as_is"


class BuyerType(StrEnum):
    EMPLOYEE = "employee"
    CONSUMER = "consumer"
    TRADER = "trader"
    RECYCLER = "recycler"


class DeviceStatus(StrEnum):
    NOT_DEPLOYED = "not_deployed"
    RENTED = "rented"
    AWAITING_RETURN = "awaiting_return"
    WIP = "wip"
    IN_STOCK = "in_stock"
    SOLD = "sold"
    SCRAPPED = "scrapped"


class SubjectType(StrEnum):
    DEVICE = "device"
    PURCHASE_ORDER = "purchase_order"
    SUPPLIER_CONTRACT = "supplier_contract"
    RENTAL_CONTRACT = "rental_contract"


class SpendCategory(StrEnum):
    HARDWARE = "hardware"
    LOGISTICS = "logistics"
    REPAIR = "repair"
    REFURBISHMENT = "refurbishment"
    SOFTWARE = "software"
    MARKETING = "marketing"
    FACILITIES = "facilities"
    CONSULTING = "consulting"
    PACKAGING = "packaging"
    # v0.2 additions (contracts register v2 and the indirect feed)
    CONNECTIVITY = "connectivity"
    FINANCING = "financing"
    RESALE_CHANNEL = "resale_channel"
    SECURITY_SOFTWARE = "security_software"


class SavingType(StrEnum):
    """How a claimed indirect saving was earned. Only hard price reductions count
    against the savings plan; cost avoidance and rebates are reported separately."""

    HARD_PRICE_REDUCTION = "hard_price_reduction"
    COST_AVOIDANCE = "cost_avoidance"
    REBATE = "rebate"


# --------------------------------------------------------------------------- v0.2 lake enums


class LineType(StrEnum):
    """The 15 ledger line types of ``silver.ledger_lines`` (SPEC_v0.2.md section 6.1)."""

    PURCHASE_PRICE = "purchase_price"
    FREIGHT = "freight"
    DUTY = "duty"
    STAGING = "staging"
    OUTBOUND_SHIPPING = "outbound_shipping"
    RENTAL_REVENUE = "rental_revenue"
    REPAIR = "repair"
    REPLACEMENT_LOGISTICS = "replacement_logistics"
    RETURN_LOGISTICS = "return_logistics"
    WIPE_GRADING = "wipe_grading"
    REFURBISHMENT = "refurbishment"
    HOLDING_COST = "holding_cost"
    RESALE_GROSS = "resale_gross"
    CHANNEL_FEE = "channel_fee"
    PRICE_PROTECTION_CREDIT = "price_protection_credit"


class LineClass(StrEnum):
    REVENUE = "revenue"
    COST = "cost"


class SupplierRole(StrEnum):
    MANUFACTURER = "manufacturer"
    RESELLER = "reseller"


class CounterpartyRole(StrEnum):
    MANUFACTURER = "manufacturer"
    RUGGED_OEM = "rugged_oem"
    RESELLER = "reseller"
    CARRIER = "carrier"
    REFURB_REPAIR = "refurb_repair"
    LOGISTICS = "logistics"
    MARKETPLACE = "marketplace"
    FINANCING = "financing"
    MTD = "mtd"


class TimelineStep(StrEnum):
    """The ten timestamps of the device cycle in ``silver.serial_timeline``."""

    ORDERED = "ordered_at"
    RECEIVED = "received_at"
    STAGED = "staged_at"
    SHIPPED = "shipped_at"
    RETURNED = "returned_at"
    WIPED = "wiped_at"
    GRADED = "graded_at"
    SELLABLE = "sellable_at"
    SOLD = "sold_at"
    CREDITED = "credited_at"


FAMILIES: tuple[str, ...] = ("iphone_like", "android_like", "laptop_like", "tablet_like")
GRADES: tuple[str, ...] = ("A", "B", "C", "D")
CHANNELS: tuple[str, ...] = ("employee_buyout", "marketplace", "b2b_wholesale", "as_is")
LINE_TYPES: tuple[str, ...] = tuple(m.value for m in LineType)
TIMELINE_STEPS: tuple[str, ...] = tuple(m.value for m in TimelineStep)

__all__ = [
    "Family",
    "Grade",
    "ChannelIn",
    "ContractStatus",
    "EventType",
    "DamageType",
    "RefurbOutcome",
    "Channel",
    "BuyerType",
    "DeviceStatus",
    "SubjectType",
    "SpendCategory",
    "SavingType",
    "LineType",
    "LineClass",
    "SupplierRole",
    "CounterpartyRole",
    "TimelineStep",
    "FAMILIES",
    "GRADES",
    "CHANNELS",
    "LINE_TYPES",
    "TIMELINE_STEPS",
]
