"""API schemas.

Business rules that used to live in the Apps Script form now live here, so any
client (the HTML form, a script, another app) gets the same validation.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from src.enums import (
    CleaningResult,
    ContainerReeferType,
    ContainerSize,
    ContainerStatus,
    ContainerType,
    CrossStuffTarget,
    Customer,
    Destination,
    EventKind,
    Generator,
    Hauler,
    PlugPurpose,
    PTIStatus,
    Role,
    ShippingLine,
    Sticker,
    TemperatureRemark,
    TimeSlot,
    UnitManufacturer,
)
from src.models import validate_container_number

PLATE_RE = re.compile(r"^S\d+$")
SEAL_RE = re.compile(r"^(?:[A-Z]|MLSC)\d{7}$")
COMMENTS_REQUIRED_AFTER = timedelta(days=3)

ContainerNumber = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True)]
Plate = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True)]


# --------------------------------------------------------------------------- #
# Shared pieces
# --------------------------------------------------------------------------- #


class _EventRequest(BaseModel):
    """Fields every event carries. `at` is when it happened in the yard."""

    at: datetime
    comments: str = ""

    @field_validator("at")
    @classmethod
    def _aware_and_not_future(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("at must include a timezone offset")
        if v > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("at cannot be in the future")
        return v

    @model_validator(mode="after")
    def _comments_for_backdated_entries(self):
        if datetime.now(UTC) - self.at > COMMENTS_REQUIRED_AFTER and not self.comments.strip():
            raise ValueError("comments are required for entries older than 3 days")
        return self


class _HaulerMixin(BaseModel):
    hauler: Hauler
    hauler_plate: Plate | None = None

    @field_validator("hauler_plate")
    @classmethod
    def _plate_format(cls, v: str | None) -> str | None:
        if v and not PLATE_RE.match(v):
            raise ValueError("license plate must be S followed by digits, no spaces")
        return v or None


# --------------------------------------------------------------------------- #
# Containers
# --------------------------------------------------------------------------- #


def _check_reefer_rules(
    container_type: ContainerType,
    reefer_type: ContainerReeferType | None,
    unit_manufacturer: UnitManufacturer | None,
) -> None:
    """Shared by ContainerIn and ContainerUpdate: reefer type / manufacturer rules."""
    if container_type is ContainerType.DRY:
        if reefer_type is not None or unit_manufacturer is not None:
            raise ValueError("dry containers have no reefer type or unit manufacturer")
        return

    if reefer_type is None:
        raise ValueError("reefer type is required for reefers")
    if unit_manufacturer is None:
        raise ValueError("unit manufacturer is required for reefers")

    allowed = {
        ContainerReeferType.S_FREEZER: {UnitManufacturer.THERMOKING, UnitManufacturer.STARCOOL},
        ContainerReeferType.MAGNUM: {UnitManufacturer.THERMOKING},
        ContainerReeferType.MAGNUM_PLUS: {UnitManufacturer.THERMOKING},
    }.get(reefer_type)
    if allowed and unit_manufacturer not in allowed:
        raise ValueError(
            f"{reefer_type} units are only made by " + " or ".join(str(m) for m in sorted(allowed))
        )


class ContainerIn(BaseModel):
    number: ContainerNumber
    shipping_line: ShippingLine
    container_type: ContainerType
    size: ContainerSize
    reefer_type: ContainerReeferType | None = None
    unit_manufacturer: UnitManufacturer | None = None

    @field_validator("number")
    @classmethod
    def _iso6346(cls, v: str) -> str:
        return validate_container_number(v)

    @model_validator(mode="after")
    def _reefer_rules(self):
        _check_reefer_rules(self.container_type, self.reefer_type, self.unit_manufacturer)
        return self


class ContainerOut(ContainerIn):
    model_config = ConfigDict(from_attributes=True)
    created_at: datetime


class ContainerUpdate(BaseModel):
    """Full replacement of the editable registry fields (PATCH body)."""

    shipping_line: ShippingLine
    container_type: ContainerType
    size: ContainerSize
    reefer_type: ContainerReeferType | None = None
    unit_manufacturer: UnitManufacturer | None = None

    @model_validator(mode="after")
    def _reefer_rules(self):
        # Same rule as ContainerIn._reefer_rules; kept separate because that
        # one is keyed to a real container number and this body has none.
        _check_reefer_rules(self.container_type, self.reefer_type, self.unit_manufacturer)
        return self


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


class GateInRequest(_EventRequest, _HaulerMixin):
    """Gate in and, if the container is new, register it in one call."""

    container: ContainerIn
    cargo_status: ContainerStatus
    pti_status: PTIStatus | None = None

    @model_validator(mode="after")
    def _status_rules(self):
        if self.cargo_status is ContainerStatus.COMPLETED:
            raise ValueError("Completed is a plug-in status, not an arrival status")
        is_dry = self.container.container_type is ContainerType.DRY
        if is_dry:
            if self.cargo_status is ContainerStatus.PARTIAL:
                raise ValueError("dry containers cannot be Partial")
            if self.pti_status not in (None, PTIStatus.NA):
                raise ValueError("PTI status does not apply to dry containers")
            self.pti_status = None
            return self

        if self.pti_status in (None, PTIStatus.NA):
            raise ValueError("PTI status is required for reefers")
        if self.cargo_status is not ContainerStatus.EMPTY and self.pti_status is PTIStatus.NON_PTI:
            raise ValueError("a loaded reefer cannot be NON PTI")
        return self


class GateOutRequest(_EventRequest, _HaulerMixin):
    container_number: ContainerNumber
    destination: Destination
    cargo_status: ContainerStatus


class PlugInRequest(_EventRequest):
    container_number: ContainerNumber
    purpose: PlugPurpose
    generator: Generator | None = None
    set_point_c: float = Field(le=30, ge=-70)
    supply_temp_c: float | None = None
    return_temp_c: float | None = None
    seal_number: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True)] | None = None
    tare_weight_kg: int | None = Field(default=None, ge=1000, le=9999)
    cargo_status: ContainerStatus | None = None

    @field_validator("seal_number")
    @classmethod
    def _seal_format(cls, v: str | None) -> str | None:
        if v and not SEAL_RE.match(v):
            raise ValueError("seal must be a letter + 7 digits (L0059326) or MLSC + 7 digits")
        return v or None

    @model_validator(mode="after")
    def _storage_needs_seal(self):
        if self.purpose is PlugPurpose.STORAGE and not self.seal_number:
            raise ValueError("a seal number is required when plugging in loaded cargo")
        if self.purpose is PlugPurpose.PTI and self.generator is None:
            raise ValueError("a generator is required for a PTI")
        if self.cargo_status is ContainerStatus.EMPTY:
            raise ValueError("an empty reefer isn't plugged in for storage")
        return self


class PlugOutRequest(_EventRequest):
    container_number: ContainerNumber
    supply_temp_c: float | None = None
    return_temp_c: float | None = None
    sticker: Sticker | None = None


class CleaningRequest(_EventRequest):
    container_number: ContainerNumber
    result: CleaningResult
    cross_stuffed: bool = False

    @model_validator(mode="after")
    def _other_needs_comment(self):
        if self.result is CleaningResult.OTHER and not self.comments.strip():
            raise ValueError("say what happened in comments when the result is Other")
        return self


class TemperatureRequest(_EventRequest):
    container_number: ContainerNumber
    time_slot: TimeSlot
    set_point_c: float = Field(le=30, ge=-70)
    supply_temp_c: float = Field(le=60, ge=-80)
    return_temp_c: float = Field(le=60, ge=-80)
    remark: TemperatureRemark

    @field_validator("at")
    @classmethod
    def _aware_and_not_future(cls, v: datetime) -> datetime:
        # Slot times are nominal (PM = 16:00), so a PM reading typed at 14:00 is
        # "in the future" by the clock. Allow the rest of the day.
        if v.tzinfo is None:
            raise ValueError("at must include a timezone offset")
        if v > datetime.now(UTC) + timedelta(hours=24):
            raise ValueError("at cannot be in the future")
        return v

    @model_validator(mode="after")
    def _alarm_needs_comment(self):
        if self.remark is TemperatureRemark.ALARM and not self.comments.strip():
            raise ValueError("describe the alarm in comments")
        return self


class TemperatureReadingOut(BaseModel):
    """A temperature-round reading (its own table, not the event log)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    container_number: str
    at: datetime
    created_at: datetime
    voided_at: datetime | None
    time_slot: TimeSlot
    set_point_c: float
    supply_temp_c: float
    return_temp_c: float
    temperature_remark: TemperatureRemark
    comments: str


class TemperatureEdit(BaseModel):
    """Partial correction of a reading. `at` and `container_number` are fixed —
    changing those is a delete-and-re-enter."""

    time_slot: TimeSlot | None = None
    set_point_c: float | None = Field(default=None, le=30, ge=-70)
    supply_temp_c: float | None = Field(default=None, le=60, ge=-80)
    return_temp_c: float | None = Field(default=None, le=60, ge=-80)
    temperature_remark: TemperatureRemark | None = None
    comments: str | None = None


class CrossStuffRequest(_EventRequest):
    """Cargo stripped from `container_number` into another container, cold
    storage or a vessel. `at` is the start; `ended_at` the finish."""

    container_number: ContainerNumber
    ended_at: datetime
    target: CrossStuffTarget
    new_container_number: ContainerNumber | None = None
    original_emptied: bool = False

    @field_validator("ended_at")
    @classmethod
    def _end_aware_not_future(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("ended_at must include a timezone offset")
        if v > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("ended_at cannot be in the future")
        return v

    @model_validator(mode="after")
    def _target_rules(self):
        if self.ended_at < self.at:
            raise ValueError("end time is before the start time")
        if self.target is CrossStuffTarget.CONTAINER:
            if not self.new_container_number:
                raise ValueError("a new container number is required when transferring to a container")
            self.new_container_number = validate_container_number(self.new_container_number)
            if self.new_container_number == self.container_number:
                raise ValueError("the new container is the same as the original")
        elif self.new_container_number:
            raise ValueError("new container number only applies when transferring to a container")
        return self


class EventOut(BaseModel):
    """Flat view of any event; unused fields for a kind are null."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: EventKind
    container_number: str
    at: datetime
    created_at: datetime
    comments: str
    hauler: Hauler | None
    hauler_plate: str | None
    cargo_status: ContainerStatus | None
    pti_status: PTIStatus | None
    destination: Destination | None
    purpose: PlugPurpose | None
    generator: Generator | None
    set_point_c: float | None
    supply_temp_c: float | None
    return_temp_c: float | None
    seal_number: str | None
    tare_weight_kg: int | None
    sticker: Sticker | None
    plug_in_id: int | None
    cleaning_result: CleaningResult | None
    cross_stuffed: bool | None
    ended_at: datetime | None
    cross_stuff_target: CrossStuffTarget | None
    new_container_number: str | None
    original_emptied: bool | None
    voided_at: datetime | None


class EventEdit(BaseModel):
    """Partial correction of an event. Only set fields are applied.

    `at`, `kind`, and `container_number` are deliberately absent — changing
    those is a delete-and-re-enter, not a correction.
    """

    comments: str | None = None
    hauler: Hauler | None = None
    hauler_plate: Plate | None = None
    cargo_status: ContainerStatus | None = None
    pti_status: PTIStatus | None = None
    destination: Destination | None = None
    purpose: PlugPurpose | None = None
    generator: Generator | None = None
    set_point_c: float | None = None
    supply_temp_c: float | None = None
    return_temp_c: float | None = None
    seal_number: str | None = None
    tare_weight_kg: int | None = None
    sticker: Sticker | None = None
    cleaning_result: CleaningResult | None = None
    cross_stuffed: bool | None = None
    cross_stuff_target: CrossStuffTarget | None = None
    new_container_number: str | None = None
    original_emptied: bool | None = None


class StateOut(BaseModel):
    container: ContainerOut
    on_site: bool
    arrived_at: datetime | None
    cargo_status: ContainerStatus | None
    pti_status: PTIStatus | None
    is_plugged: bool
    plugged_in: EventOut | None
    last_cleaning: CleaningResult | None
    cleaned_this_visit: CleaningResult | None
    cleaning_done: bool
    visit_count: int
    last_event: EventOut | None


class OptionsOut(BaseModel):
    """Dropdown values for forms, so the UI never hard-codes them."""

    shipping_lines: list[str]
    container_types: list[str]
    sizes: list[str]
    reefer_types: list[str]
    unit_manufacturers: list[str]
    cargo_statuses: list[str]
    pti_statuses: list[str]
    haulers: list[str]
    destinations: list[str]
    generators: list[str]
    plug_purposes: list[str]
    stickers: list[str]
    cleaning_results: list[str]
    temperature_remarks: list[str]
    customers: list[str]
    time_slots: list[str]
    cross_stuff_targets: list[str]


# --------------------------------------------------------------------------- #
# Unmatched records and shifting
# --------------------------------------------------------------------------- #


class UnmatchedIn(_EventRequest):
    kind: EventKind
    container_number: ContainerNumber
    details: dict = Field(default_factory=dict)


class UnmatchedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: EventKind
    container_number: str
    check_digit_ok: bool
    at: datetime
    created_at: datetime
    details: dict
    comments: str
    resolved_at: datetime | None


class ShiftingIn(_EventRequest):
    customer: Customer
    container_numbers: list[ContainerNumber] = Field(min_length=1)
    remarks: str = Field(min_length=1)


class ShiftingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    at: datetime
    created_at: datetime
    customer: Customer
    container_numbers: list[str]
    remarks: str


class ShiftingEdit(BaseModel):
    """Full replacement of a shifting job's editable fields (PATCH body)."""

    at: datetime
    customer: Customer
    container_numbers: list[ContainerNumber] = Field(min_length=1)
    remarks: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #

Username = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=40)]


class LoginRequest(BaseModel):
    username: Username
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: Role
    disabled: bool
    created_at: datetime
    updated_at: datetime


class MeOut(BaseModel):
    """The signed-in account plus the flags the UI uses to show/hide controls."""

    id: int
    username: str
    role: Role
    can_record: bool
    can_admin: bool

    @classmethod
    def of(cls, user):
        return cls(
            id=user.id,
            username=user.username,
            role=user.role,
            can_record=user.role.rank >= Role.OPERATOR.rank,
            can_admin=user.role is Role.ADMIN,
        )


class UserCreate(BaseModel):
    username: Username
    password: str = Field(min_length=8)
    role: Role


class UserUpdate(BaseModel):
    """Only the set fields change. `username` is fixed once created."""

    role: Role | None = None
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=8)
