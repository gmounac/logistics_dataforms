"""Enumerations used across the domain.

Stored in SQLite as their string values.
"""

from enum import Enum


class _StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value

    @classmethod
    def list_all(cls) -> list[str]:
        return [m.value for m in cls]


class ContainerType(_StrEnum):
    DRY = "Dry"
    REEFER = "Reefer"


class ContainerReeferType(_StrEnum):
    STANDARD = "Standard"
    MAGNUM = "Magnum"
    MAGNUM_PLUS = "Magnum +"
    S_FREEZER = "S Freezer"

    @property
    def min_temperature_c(self) -> int:
        """Lowest set point the unit is rated for, in Celsius."""
        match self:
            case ContainerReeferType.STANDARD:
                return -25
            case ContainerReeferType.MAGNUM | ContainerReeferType.MAGNUM_PLUS:
                return -35
            case ContainerReeferType.S_FREEZER:
                return -60


class ContainerSize(_StrEnum):
    TEU = "TEU"
    FEU = "FEU"

    @property
    def feet(self) -> int:
        return 20 if self is ContainerSize.TEU else 40


class ShippingLine(_StrEnum):
    MAERSK = "MAERSK"
    CMA_CGM = "CMA CGM"
    IOT = "IOT"


class UnitManufacturer(_StrEnum):
    DAIKIN = "Daikin"
    CARRIER = "Carrier"
    STARCOOL = "StarCool"
    THERMOKING = "Thermoking"


class ContainerStatus(_StrEnum):
    """Cargo status of the box."""

    EMPTY = "Empty"
    PARTIAL = "Partial"
    FULL = "Full"
    COMPLETED = "Completed"


class TimeSlot(_StrEnum):
    """Temperature rounds are done three times a day, not at a clock time."""

    AM = "AM"
    NOON = "NOON"
    PM = "PM"

    @property
    def hour(self) -> int:
        """Nominal local hour used to place the reading on the timeline."""
        return {"AM": 8, "NOON": 12, "PM": 16}[self.value]


class PTIStatus(_StrEnum):
    NON_PTI = "NON PTI"
    PTI = "PTI"
    NA = "NA"
    DAMAGED = "Damaged"
    MALFUNCTION = "Malfunction"


class Destination(_StrEnum):
    LML = "LML"
    IOT = "IOT"
    FISHING_PORT = "Fishing Port"
    ZONE_14 = "Zone 14"
    HD_YARD = "HD Yard"
    JHL = "JHL"


class CleaningResult(_StrEnum):
    CLEAN = "Clean"
    REWASH = "Rewash"
    UNCLEAN = "Unclean"
    OTHER = "Other"


class TemperatureRemark(_StrEnum):
    IN_RANGE = "In Range"
    DEFROST = "Defrost"
    IN_RANGE_DEFROST = "In Range / Defrost"
    ALARM = "Alarm"


class Generator(_StrEnum):
    K2 = "K2"
    K3 = "K3"
    K6 = "K6"
    K7 = "K7"
    K8 = "K8"
    K9 = "K9"
    AKSA = "AKSA"


class PlugPurpose(_StrEnum):
    PTI = "PTI"
    STORAGE = "Storage"


class Sticker(_StrEnum):
    PASS = "PASS"
    RED = "RED"
    TBR = "TBR"
    NA = "NA"

    @property
    def pti_status(self) -> PTIStatus:
        return PTIStatus.PTI if self is Sticker.PASS else PTIStatus.NON_PTI


class Customer(_StrEnum):
    """Who a shifting job is done for."""
    MAERSK = "MAERSK"
    CMA_CGM = "CMA CGM"
    IOT = "IOT"
    SAPMER = "SAPMER"
    CCCS = "CCCS"
    OTHER = "Other"


class Hauler(_StrEnum):
    HD = "HD"
    LML = "LML"
    MAHE_DESIGN = "Mahe Design & Build"
    ACL = "ACL"
    IPHS = "IPHS"
    UCPS = "UCPS"
    FEROX_FEED = "Ferox Feed"


class CrossStuffTarget(_StrEnum):
    """Where the cargo stripped from a container goes."""

    CONTAINER = "Container"
    COLD_STORAGE = "Cold Storage"
    CARGO_VESSEL = "Cargo Vessel"


class Role(_StrEnum):
    """Who may do what. Hierarchical: each role includes the ones before it."""

    VIEWER = "viewer"
    """Read-only: every GET."""
    OPERATOR = "operator"
    """Viewer + record new events, temperature readings, shifting, unmatched."""
    ADMIN = "admin"
    """Operator + edit/void records, the container registry, and user accounts."""

    @property
    def rank(self) -> int:
        return {"viewer": 0, "operator": 1, "admin": 2}[self.value]


class EventKind(_StrEnum):
    GATE_IN = "gate_in"
    GATE_OUT = "gate_out"
    PLUG_IN = "plug_in"
    PLUG_OUT = "plug_out"
    PTI_PLUG_IN = "pti_plug_in"
    """A plug-in whose purpose is a pre-trip inspection."""
    PTI_PLUG_OUT = "pti_plug_out"
    """The unplug that closes a PTI."""
    CLEANING = "cleaning"
    TEMPERATURE = "temperature"
    CROSS_STUFF = "cross_stuff"
