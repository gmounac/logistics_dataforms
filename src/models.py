"""Persistent models.

Design: `containers` is the registry; `events` is an append-only log with one
row per thing that happened to a container (gate in/out, plug in/out,
cleaning). Current status is *derived* from the log, never stored — see
services.YardService.state().
"""

import re
from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from src.db import Base, UTCDateTime, utcnow
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
    ShippingLine,
    Sticker,
    TemperatureRemark,
    TimeSlot,
    UnitManufacturer,
)

# --------------------------------------------------------------------------- #
# ISO 6346 container numbers
# --------------------------------------------------------------------------- #

CONTAINER_NUMBER_RE = re.compile(r"[A-Z]{3}[UJZ]\d{7}")

_LETTER_VALUES = {
    c: v
    for c, v in zip(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        # A=10 ... skipping multiples of 11 (11, 22, 33)
        [10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24, 25, 26,
         27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38],
    )
}


def iso6346_check_digit(number: str) -> int:
    total = sum(
        (_LETTER_VALUES[c] if c.isalpha() else int(c)) * 2**i
        for i, c in enumerate(number[:10])
    )
    return total % 11 % 10


def validate_container_number(number: str) -> str:
    number = number.strip().upper()
    if not CONTAINER_NUMBER_RE.fullmatch(number):
        raise ValueError(f"invalid container number format: {number!r}")
    if int(number[10]) != iso6346_check_digit(number):
        raise ValueError(f"invalid container number check digit: {number!r}")
    return number


def is_valid_number_format(number: str) -> bool:
    return bool(CONTAINER_NUMBER_RE.fullmatch(number))


def _enum(enum_cls, **kw):
    """Store enums by their string value rather than member name."""
    return SAEnum(enum_cls, values_callable=lambda e: [m.value for m in e], **kw)


# --------------------------------------------------------------------------- #
# Container registry
# --------------------------------------------------------------------------- #


class Container(Base):
    __tablename__ = "containers"

    number: Mapped[str] = mapped_column(String(11), primary_key=True)
    shipping_line: Mapped[ShippingLine] = mapped_column(_enum(ShippingLine))
    container_type: Mapped[ContainerType] = mapped_column(_enum(ContainerType))
    size: Mapped[ContainerSize] = mapped_column(_enum(ContainerSize))
    reefer_type: Mapped[ContainerReeferType | None] = mapped_column(_enum(ContainerReeferType))
    unit_manufacturer: Mapped[UnitManufacturer | None] = mapped_column(_enum(UnitManufacturer))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    events: Mapped[list[Event]] = relationship(
        back_populates="container", order_by="Event.at", cascade="all, delete-orphan"
    )

    @property
    def is_reefer(self) -> bool:
        return self.container_type is ContainerType.REEFER

    @validates("number")
    def _validate_number(self, _key, value: str) -> str:
        return validate_container_number(value)

    def validate(self) -> None:
        """Cross-field checks; call before persisting."""
        if self.is_reefer:
            if self.reefer_type is None:
                raise ValueError("reefer containers need a reefer_type")
        elif self.reefer_type is not None or self.unit_manufacturer is not None:
            raise ValueError("reefer_type/unit_manufacturer only apply to reefers")

    def __repr__(self) -> str:
        return f"Container({self.number}, {self.container_type}, {self.size})"


# --------------------------------------------------------------------------- #
# Event log (single-table inheritance keyed on `kind`)
# --------------------------------------------------------------------------- #


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[EventKind] = mapped_column(_enum(EventKind), index=True)
    container_number: Mapped[str] = mapped_column(
        ForeignKey("containers.number"), index=True
    )
    at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    """When it happened in the yard (user-supplied)."""
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    """When the row was written (audit)."""
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    """Set when this event is deleted. Voided events are excluded from state
    derivation and from the yard's chronology, but kept for the audit trail."""
    comments: Mapped[str] = mapped_column(Text, default="")

    # Gate in / out
    hauler: Mapped[Hauler | None] = mapped_column(_enum(Hauler))
    hauler_plate: Mapped[str | None] = mapped_column(String(20))
    cargo_status: Mapped[ContainerStatus | None] = mapped_column(_enum(ContainerStatus))
    pti_status: Mapped[PTIStatus | None] = mapped_column(_enum(PTIStatus))
    destination: Mapped[Destination | None] = mapped_column(_enum(Destination))

    # Plug in / out
    purpose: Mapped[PlugPurpose | None] = mapped_column(_enum(PlugPurpose))
    generator: Mapped[Generator | None] = mapped_column(_enum(Generator))
    set_point_c: Mapped[float | None] = mapped_column(Float)
    supply_temp_c: Mapped[float | None] = mapped_column(Float)
    return_temp_c: Mapped[float | None] = mapped_column(Float)
    seal_number: Mapped[str | None] = mapped_column(String(50))
    tare_weight_kg: Mapped[int | None] = mapped_column(Integer)
    sticker: Mapped[Sticker | None] = mapped_column(_enum(Sticker))
    plug_in_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"))
    """PlugOut -> the PlugIn it closes."""

    # Cleaning
    cleaning_result: Mapped[CleaningResult | None] = mapped_column(_enum(CleaningResult))
    cross_stuffed: Mapped[bool | None] = mapped_column(Boolean)
    """Cargo was transferred, so a second wash this visit is legitimate."""

    # Cross stuffing (cargo stripped from `container_number`)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    """When the service finished (`at` holds the start)."""
    cross_stuff_target: Mapped[CrossStuffTarget | None] = mapped_column(_enum(CrossStuffTarget))
    new_container_number: Mapped[str | None] = mapped_column(String(11))
    """The receiving container, when the cargo went into another box."""
    original_emptied: Mapped[bool | None] = mapped_column(Boolean)
    """The stripped container is empty afterwards -> its cargo status becomes Empty."""

    container: Mapped[Container] = relationship(back_populates="events")

    __mapper_args__ = {"polymorphic_on": "kind"}

    @validates("seal_number", "hauler_plate")
    def _upper(self, _key, value: str | None) -> str | None:
        return value.strip().upper() if value else None

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.container_number} @ {self.at:%Y-%m-%d %H:%M})"


class GateIn(Event):
    __mapper_args__ = {"polymorphic_identity": EventKind.GATE_IN}


class GateOut(Event):
    __mapper_args__ = {"polymorphic_identity": EventKind.GATE_OUT}


class PlugIn(Event):
    __mapper_args__ = {"polymorphic_identity": EventKind.PLUG_IN}


class PlugOut(Event):
    __mapper_args__ = {"polymorphic_identity": EventKind.PLUG_OUT}

    @property
    def resulting_pti_status(self) -> PTIStatus | None:
        return self.sticker.pti_status if self.sticker else None


class Cleaning(Event):
    __mapper_args__ = {"polymorphic_identity": EventKind.CLEANING}


class CrossStuff(Event):
    __mapper_args__ = {"polymorphic_identity": EventKind.CROSS_STUFF}


# --------------------------------------------------------------------------- #
# Records that don't fit the event log
# --------------------------------------------------------------------------- #


class UnmatchedRecord(Base):
    """A form submitted for a container that wasn't where the yard expected it.

    e.g. a PTI plug for a box with no gate-in. We keep everything the operator
    typed so nothing is lost, and someone resolves it later (usually by
    recording the missing gate-in and re-entering the event).
    """

    __tablename__ = "unmatched_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[EventKind] = mapped_column(_enum(EventKind), index=True)
    container_number: Mapped[str] = mapped_column(String(11), index=True)
    check_digit_ok: Mapped[bool]
    at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    """The form fields as submitted (manufacturer, generator, set point, ...)."""
    comments: Mapped[str] = mapped_column(Text, default="")
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ShiftingJob(Base):
    """Moving one or more containers within the yard for a customer."""

    __tablename__ = "shifting_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    customer: Mapped[Customer] = mapped_column(_enum(Customer))
    container_numbers: Mapped[list[str]] = mapped_column(JSON)
    remarks: Mapped[str] = mapped_column(Text)


class TemperatureReading(Base):
    """A temperature-round reading on a plugged-in reefer.

    Kept in its own table rather than the event log: rounds are logged three
    times a day for every plugged reefer, they never change derived yard state,
    and they can be entered out of order — so the log's chronology rules and
    single-table width don't earn their keep here.
    """

    __tablename__ = "temperature_readings"

    id: Mapped[int] = mapped_column(primary_key=True)
    container_number: Mapped[str] = mapped_column(ForeignKey("containers.number"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    """Nominal round time (AM = 08:00, NOON = 12:00, PM = 16:00)."""
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    time_slot: Mapped[TimeSlot] = mapped_column(_enum(TimeSlot))
    set_point_c: Mapped[float] = mapped_column(Float)
    supply_temp_c: Mapped[float] = mapped_column(Float)
    return_temp_c: Mapped[float] = mapped_column(Float)
    temperature_remark: Mapped[TemperatureRemark] = mapped_column(_enum(TemperatureRemark))
    comments: Mapped[str] = mapped_column(Text, default="")

    container: Mapped[Container] = relationship()

    def __repr__(self) -> str:
        return f"TemperatureReading({self.container_number} {self.time_slot} @ {self.at:%Y-%m-%d})"
