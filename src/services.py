"""Yard operations.

All writes go through YardService so the invariants live in one place:
  * a container must be registered before any event
  * gate in only when off site; everything else only when on site
  * plug in only for reefers that aren't already plugged; plug out closes it
  * events for a container are chronological
"""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.enums import (
    CleaningResult,
    ContainerStatus,
    CrossStuffTarget,
    Customer,
    Destination,
    EventKind,
    Generator,
    Hauler,
    PlugPurpose,
    PTIStatus,
    Sticker,
    TemperatureRemark,
    TimeSlot,
)
from src.models import (
    Cleaning,
    Container,
    CrossStuff,
    Event,
    GateIn,
    GateOut,
    PlugIn,
    PlugOut,
    PtiPlugIn,
    PtiPlugOut,
    ShiftingJob,
    TemperatureReading,
    UnmatchedRecord,
    is_valid_number_format,
    iso6346_check_digit,
)


class YardError(ValueError):
    """Raised when an operation would violate a yard invariant."""


@dataclass(frozen=True)
class ContainerState:
    """Derived snapshot of a container, folded from its event log."""

    container: Container
    on_site: bool
    arrived_at: datetime | None
    """When the current visit began (gate-in time); None when off site."""
    cargo_status: ContainerStatus | None
    pti_status: PTIStatus | None
    plugged_in: PlugIn | None
    last_cleaning: CleaningResult | None
    """Most recent cleaning result ever recorded."""
    cleaned_this_visit: CleaningResult | None
    """Cleaning result during the current visit; reset on gate in."""
    visit_count: int
    """How many times this container has gated in (this visit included when on site)."""
    last_event: Event | None

    @property
    def cleaning_done(self) -> bool:
        """A Clean result closes cleaning until the container moves out and back in."""
        return self.cleaned_this_visit is CleaningResult.CLEAN

    @property
    def is_plugged(self) -> bool:
        return self.plugged_in is not None


class YardService:
    def __init__(self, session: Session) -> None:
        self.s = session

    # ------------------------------------------------------------------ #
    # Registry
    # ------------------------------------------------------------------ #

    def register(self, container: Container) -> Container:
        container.validate()
        if self.s.get(Container, container.number):
            raise YardError(f"{container.number} is already registered")
        self.s.add(container)
        self.s.commit()
        return container

    def ensure(self, container: Container) -> Container:
        """Register the container if unknown; otherwise return the existing one.

        Used by gate-in, where the operator types the container details every time.
        If the details differ from what's on file, that's an error rather than a
        silent overwrite — the registry is the source of truth.
        """
        container.validate()
        existing = self.s.get(Container, container.number)
        if existing is None:
            self.s.add(container)
            self.s.flush()
            return container
        for attr in ("shipping_line", "container_type", "size", "reefer_type", "unit_manufacturer"):
            if getattr(existing, attr) != getattr(container, attr):
                raise YardError(
                    f"{existing.number} is on file as {getattr(existing, attr)} "
                    f"({attr}), not {getattr(container, attr)}"
                )
        return existing

    def get(self, number: str) -> Container:
        c = self.s.get(Container, number.strip().upper())
        if c is None:
            raise YardError(f"unknown container {number}")
        return c

    # ------------------------------------------------------------------ #
    # State (derived)
    # ------------------------------------------------------------------ #

    def history(self, number: str, *, include_voided: bool = False) -> list[Event]:
        stmt = select(Event).where(Event.container_number == number)
        if not include_voided:
            stmt = stmt.where(Event.voided_at.is_(None))
        stmt = stmt.order_by(Event.at, Event.id)
        return list(self.s.scalars(stmt))

    def events(
        self,
        *,
        kind: EventKind | None = None,
        q: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        include_voided: bool = True,
        limit: int = 500,
    ) -> list[Event]:
        stmt = select(Event).order_by(Event.at.desc(), Event.id.desc()).limit(limit)
        if not include_voided:
            stmt = stmt.where(Event.voided_at.is_(None))
        if kind:
            stmt = stmt.where(Event.kind == kind)
        if q:
            stmt = stmt.where(Event.container_number.contains(q.strip().upper()))
        if date_from:
            stmt = stmt.where(Event.at >= date_from)
        if date_to:
            stmt = stmt.where(Event.at <= date_to)
        return list(self.s.scalars(stmt))

    @staticmethod
    def fold(container: Container, events: list[Event]) -> ContainerState:
        on_site = False
        visit_count = 0
        arrived = cargo = pti = plugged = cleaning = visit_cleaning = None
        for e in events:
            match e:
                case GateIn():
                    on_site, arrived, cargo, pti, plugged = True, e.at, e.cargo_status, e.pti_status, None
                    visit_cleaning = None
                    visit_count += 1
                case GateOut():
                    on_site, arrived, cargo, plugged = False, None, e.cargo_status, None
                case PlugIn():
                    plugged = e
                    if e.cargo_status is not None:
                        cargo = e.cargo_status
                case PlugOut():
                    plugged = None
                    if e.sticker is not None:
                        pti = e.sticker.pti_status
                case Cleaning():
                    cleaning = visit_cleaning = e.cleaning_result
                case CrossStuff():
                    if e.container_number == container.number:
                        if e.original_emptied:
                            cargo = ContainerStatus.EMPTY
                    elif e.new_container_number == container.number:
                        cargo = ContainerStatus.FULL
        return ContainerState(
            container=container,
            on_site=on_site,
            arrived_at=arrived,
            cargo_status=cargo,
            pti_status=pti,
            plugged_in=plugged,
            last_cleaning=cleaning,
            cleaned_this_visit=visit_cleaning,
            visit_count=visit_count,
            # the timeline may include cross-stuff events owned by another
            # container, so pick the last one that is actually this container's
            last_event=next((e for e in reversed(events) if e.container_number == container.number), None),
        )

    def _inbound_cross_stuff(self, number: str) -> list[Event]:
        """Cross-stuff events done elsewhere that named `number` as the receiver.

        They aren't in the container's own `events`, but they change its cargo
        status, so state derivation has to see them.
        """
        return list(
            self.s.scalars(
                select(Event).where(
                    Event.kind == EventKind.CROSS_STUFF,
                    Event.new_container_number == number,
                    Event.voided_at.is_(None),
                )
            )
        )

    def _timeline(self, number: str) -> list[Event]:
        own = self.history(number)
        inbound = self._inbound_cross_stuff(number)
        if not inbound:
            return own
        return sorted(own + inbound, key=lambda e: (e.at, e.id))

    def state(self, number: str) -> ContainerState:
        c = self.get(number)
        return self.fold(c, self._timeline(c.number))

    def on_site(
        self,
        q: str | None = None,
        *,
        reefer: bool | None = None,
        plugged: bool | None = None,
        purpose: PlugPurpose | None = None,
        cleanable: bool | None = None,
    ) -> list[ContainerState]:
        """Containers currently in the yard.

        `q` filters by number substring; `reefer` / `plugged` narrow further
        (None = don't care). Used by the form pickers.
        """
        stmt = select(Container).order_by(Container.number)
        if q:
            stmt = stmt.where(Container.number.contains(q.strip().upper()))
        containers = list(self.s.scalars(stmt))

        inbound: dict[str, list[Event]] = {}
        for e in self.s.scalars(
            select(Event).where(
                Event.kind == EventKind.CROSS_STUFF,
                Event.new_container_number.is_not(None),
                Event.voided_at.is_(None),
            )
        ):
            inbound.setdefault(e.new_container_number, []).append(e)

        def timeline(c: Container) -> list[Event]:
            own = [e for e in c.events if e.voided_at is None]
            extra = inbound.get(c.number)
            return sorted(own + extra, key=lambda e: (e.at, e.id)) if extra else own

        states = (self.fold(c, timeline(c)) for c in containers)
        return [
            st for st in states
            if st.on_site
            and (reefer is None or st.container.is_reefer == reefer)
            and (plugged is None or st.is_plugged == plugged)
            and (purpose is None or (st.plugged_in is not None and st.plugged_in.purpose is purpose))
            and (cleanable is None or (not st.cleaning_done) == cleanable)
        ]

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #

    def _append(self, event: Event, *, require_on_site: bool, strict_order: bool = True) -> ContainerState:
        c = self.get(event.container_number)
        events = self.history(c.number)
        st = self.fold(c, events)

        if strict_order and st.last_event and event.at < st.last_event.at:
            raise YardError(
                f"{c.number}: event at {event.at:%Y-%m-%d %H:%M} is earlier than "
                f"last event at {st.last_event.at:%Y-%m-%d %H:%M}"
            )
        if require_on_site and not st.on_site:
            raise YardError(f"{c.number} is not in the yard")
        if not require_on_site and st.on_site:
            raise YardError(f"{c.number} is already in the yard")

        self.s.add(event)
        self.s.commit()
        return self.fold(c, events + [event])

    def gate_in(
        self,
        container: Container | str,
        *,
        at: datetime,
        hauler: Hauler,
        hauler_plate: str | None = None,
        cargo_status: ContainerStatus,
        pti_status: PTIStatus | None = None,
        comments: str = "",
    ) -> ContainerState:
        """Gate a container in. Pass a `Container` to register-on-first-sight."""
        c = self.get(container) if isinstance(container, str) else self.ensure(container)
        if c.is_reefer and pti_status is None:
            raise YardError("pti_status is required for reefers")
        if not c.is_reefer and pti_status not in (None, PTIStatus.NA):
            raise YardError("pti_status only applies to reefers")
        return self._append(
            GateIn(
                container_number=c.number, at=at, hauler=hauler, hauler_plate=hauler_plate,
                cargo_status=cargo_status, pti_status=pti_status, comments=comments,
            ),
            require_on_site=False,
        )

    def gate_out(
        self,
        number: str,
        *,
        at: datetime,
        hauler: Hauler,
        hauler_plate: str | None = None,
        destination: Destination,
        cargo_status: ContainerStatus,
        comments: str = "",
    ) -> ContainerState:
        c = self.get(number)
        if self.state(c.number).is_plugged:
            raise YardError(f"{c.number} is still plugged in; plug out first")
        return self._append(
            GateOut(
                container_number=c.number, at=at, hauler=hauler, hauler_plate=hauler_plate,
                destination=destination, cargo_status=cargo_status, comments=comments,
            ),
            require_on_site=True,
        )

    def plug_in(
        self,
        number: str,
        *,
        at: datetime,
        purpose: PlugPurpose,
        generator: Generator | None = None,
        set_point_c: float,
        supply_temp_c: float | None = None,
        return_temp_c: float | None = None,
        seal_number: str | None = None,
        tare_weight_kg: int | None = None,
        cargo_status: ContainerStatus | None = None,
        comments: str = "",
    ) -> ContainerState:
        c = self.get(number)
        if not c.is_reefer:
            raise YardError(f"{c.number} is not a reefer")
        if set_point_c < c.reefer_type.min_temperature_c:
            raise YardError(
                f"set point {set_point_c}°C is below the {c.reefer_type} "
                f"minimum of {c.reefer_type.min_temperature_c}°C"
            )
        if self.state(c.number).is_plugged:
            raise YardError(f"{c.number} is already plugged in")
        plug_cls = PtiPlugIn if purpose is PlugPurpose.PTI else PlugIn
        return self._append(
            plug_cls(
                container_number=c.number, at=at, purpose=purpose, generator=generator,
                set_point_c=set_point_c, supply_temp_c=supply_temp_c,
                return_temp_c=return_temp_c, seal_number=seal_number,
                tare_weight_kg=tare_weight_kg, cargo_status=cargo_status, comments=comments,
            ),
            require_on_site=True,
        )

    def plug_out(
        self,
        number: str,
        *,
        at: datetime,
        supply_temp_c: float | None = None,
        return_temp_c: float | None = None,
        sticker: Sticker | None = None,
        comments: str = "",
    ) -> ContainerState:
        c = self.get(number)
        st = self.state(c.number)
        if not st.is_plugged:
            raise YardError(f"{c.number} is not plugged in")
        closing_pti = st.plugged_in.purpose is PlugPurpose.PTI
        if closing_pti and sticker is None:
            raise YardError("a sticker is required when plugging out from a PTI")
        plug_out_cls = PtiPlugOut if closing_pti else PlugOut
        return self._append(
            plug_out_cls(
                container_number=c.number, at=at, plug_in_id=st.plugged_in.id,
                purpose=st.plugged_in.purpose,
                supply_temp_c=supply_temp_c, return_temp_c=return_temp_c,
                sticker=sticker, comments=comments,
            ),
            require_on_site=True,
        )

    def clean(
        self,
        number: str,
        *,
        at: datetime,
        result: CleaningResult,
        cross_stuffed: bool = False,
        comments: str = "",
    ) -> ContainerState:
        """Record a cleaning. One Clean result per visit, unless the cargo was cross-stuffed."""
        c = self.get(number)
        if self.state(c.number).cleaning_done and not cross_stuffed:
            raise YardError(
                f"{c.number} was already cleaned this visit; tick 'cross stuffed' if it was "
                "restuffed, otherwise it must gate out and back in first"
            )
        return self._append(
            Cleaning(container_number=c.number, at=at, cleaning_result=result,
                     cross_stuffed=cross_stuffed, comments=comments),
            require_on_site=True,
        )

    # ------------------------------------------------------------------ #
    # Temperature readings (own table, not the event log)
    # ------------------------------------------------------------------ #

    def temperature_check(
        self,
        number: str,
        *,
        at: datetime,
        time_slot: TimeSlot,
        set_point_c: float,
        supply_temp_c: float,
        return_temp_c: float,
        remark: TemperatureRemark,
        comments: str = "",
    ) -> TemperatureReading:
        """A temperature round reading; only meaningful while plugged in.

        Rounds are AM / NOON / PM slots, so `at` is nominal. Readings may be
        entered out of order (a NOON reading typed in after the PM one), so
        we only insist the reading is after the plug-in, not after every event.
        """
        c = self.get(number)
        st = self.state(c.number)
        if not st.is_plugged:
            raise YardError(f"{c.number} is not plugged in")
        if at < st.plugged_in.at:
            raise YardError(f"reading is before the plug in at {st.plugged_in.at:%Y-%m-%d %H:%M}")
        reading = TemperatureReading(
            container_number=c.number, at=at, time_slot=time_slot, set_point_c=set_point_c,
            supply_temp_c=supply_temp_c, return_temp_c=return_temp_c,
            temperature_remark=remark, comments=comments,
        )
        self.s.add(reading)
        self.s.commit()
        return reading

    def temperature_readings(
        self,
        *,
        q: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        include_voided: bool = True,
        limit: int = 500,
    ) -> list[TemperatureReading]:
        stmt = (
            select(TemperatureReading)
            .order_by(TemperatureReading.at.desc(), TemperatureReading.id.desc())
            .limit(limit)
        )
        if not include_voided:
            stmt = stmt.where(TemperatureReading.voided_at.is_(None))
        if q:
            stmt = stmt.where(TemperatureReading.container_number.contains(q.strip().upper()))
        if date_from:
            stmt = stmt.where(TemperatureReading.at >= date_from)
        if date_to:
            stmt = stmt.where(TemperatureReading.at <= date_to)
        return list(self.s.scalars(stmt))

    TEMPERATURE_EDIT_FIELDS: ClassVar[set[str]] = {
        "time_slot", "set_point_c", "supply_temp_c", "return_temp_c",
        "temperature_remark", "comments",
    }

    def edit_temperature(self, reading_id: int, **fields) -> TemperatureReading:
        r = self.s.get(TemperatureReading, reading_id)
        if r is None or r.voided_at is not None:
            raise YardError(f"no active temperature reading {reading_id}")
        unknown = set(fields) - self.TEMPERATURE_EDIT_FIELDS
        if unknown:
            raise YardError(f"cannot edit: {', '.join(sorted(unknown))}")
        for k, v in fields.items():
            if v is not None:
                setattr(r, k, v)
        self.s.commit()
        return r

    def void_temperature(self, reading_id: int) -> TemperatureReading:
        r = self.s.get(TemperatureReading, reading_id)
        if r is None or r.voided_at is not None:
            raise YardError(f"no active temperature reading {reading_id}")
        from src.db import utcnow
        r.voided_at = utcnow()
        self.s.commit()
        return r

    def cross_stuff(
        self,
        number: str,
        *,
        at: datetime,
        ended_at: datetime,
        target: CrossStuffTarget,
        new_container_number: str | None = None,
        original_emptied: bool = False,
        comments: str = "",
    ) -> ContainerState:
        """Record cargo stripped from `number` into another container, cold
        storage or a vessel.

        The stripped container's cargo status becomes Empty when
        `original_emptied` is set; a receiving container's becomes Full. Both
        follow from the folded event log, so nothing is written twice.
        """
        c = self.get(number)
        if target is CrossStuffTarget.CONTAINER:
            if not new_container_number:
                raise YardError("a new container number is required when transferring to a container")
            nc = self.get(new_container_number)
            if nc.number == c.number:
                raise YardError("the new container is the same as the original")
            if not self.state(nc.number).on_site:
                raise YardError(f"{nc.number} (the new container) is not in the yard")
            new_container_number = nc.number
        else:
            new_container_number = None
        return self._append(
            CrossStuff(
                container_number=c.number, at=at, ended_at=ended_at,
                cross_stuff_target=target, new_container_number=new_container_number,
                original_emptied=original_emptied, comments=comments,
            ),
            require_on_site=True,
        )

    # ------------------------------------------------------------------ #
    # Unmatched records
    # ------------------------------------------------------------------ #

    def record_unmatched(
        self,
        *,
        kind: EventKind,
        container_number: str,
        at: datetime,
        details: dict,
        comments: str = "",
    ) -> UnmatchedRecord:
        number = container_number.strip().upper()
        if not is_valid_number_format(number):
            raise YardError(f"{number!r} is not a container number (4 letters + 7 digits)")
        rec = UnmatchedRecord(
            kind=kind,
            container_number=number,
            check_digit_ok=int(number[10]) == iso6346_check_digit(number),
            at=at,
            details=details,
            comments=comments,
        )
        self.s.add(rec)
        self.s.commit()
        return rec

    def unmatched(self, *, include_resolved: bool = False) -> list[UnmatchedRecord]:
        stmt = select(UnmatchedRecord).order_by(UnmatchedRecord.at.desc())
        if not include_resolved:
            stmt = stmt.where(UnmatchedRecord.resolved_at.is_(None))
        return list(self.s.scalars(stmt))

    def resolve_unmatched(self, record_id: int) -> UnmatchedRecord:
        rec = self.s.get(UnmatchedRecord, record_id)
        if rec is None:
            raise YardError(f"no unmatched record {record_id}")
        from src.db import utcnow
        rec.resolved_at = utcnow()
        self.s.commit()
        return rec

    # ------------------------------------------------------------------ #
    # Shifting
    # ------------------------------------------------------------------ #

    def shift(
        self,
        *,
        at: datetime,
        customer: Customer,
        container_numbers: list[str],
        remarks: str,
        comments: str = "",
    ) -> ShiftingJob:
        """Record a shifting job. Containers must be in the yard, except for CCCS jobs."""
        numbers = [n.strip().upper() for n in container_numbers if n.strip()]
        if not numbers:
            raise YardError("at least one container number is required")
        bad = [n for n in numbers if not is_valid_number_format(n)]
        if bad:
            raise YardError("not container numbers: " + ", ".join(bad))
        if len(set(numbers)) != len(numbers):
            raise YardError("the same container is listed twice")
        if customer is not Customer.CCCS:
            on_site = {st.container.number for st in self.on_site()}
            missing = [n for n in numbers if n not in on_site]
            if missing:
                raise YardError("not in the yard: " + ", ".join(missing))
        text = remarks.strip() + (f" — {comments.strip()}" if comments.strip() else "")
        job = ShiftingJob(at=at, customer=customer, container_numbers=numbers, remarks=text)
        self.s.add(job)
        self.s.commit()
        return job

    def shifting_jobs(self, limit: int = 100) -> list[ShiftingJob]:
        stmt = select(ShiftingJob).order_by(ShiftingJob.at.desc()).limit(limit)
        return list(self.s.scalars(stmt))

    def edit_shifting(self, job_id: int, **fields) -> ShiftingJob:
        job = self.s.get(ShiftingJob, job_id)
        if job is None:
            raise YardError(f"no shifting job {job_id}")
        for k, v in fields.items():
            setattr(job, k, v)
        self.s.commit()
        return job

    def delete_shifting(self, job_id: int) -> None:
        job = self.s.get(ShiftingJob, job_id)
        if job is None:
            raise YardError(f"no shifting job {job_id}")
        self.s.delete(job)
        self.s.commit()

    # ------------------------------------------------------------------ #
    # Corrections: edit and delete
    # ------------------------------------------------------------------ #
    #
    # The event log is append-only by design (that is what lets `state()` be
    # derived rather than stored), but typos happen. The policy:
    #   - any event's descriptive fields can be corrected in place, since that
    #     only changes history, not the chain of what-happened-when;
    #   - only the most recent event for a container can be *deleted* (voided),
    #     because removing one from the middle would leave later events
    #     resting on a gap (e.g. a plug-out with no plug-in before it).
    #     Deleting the latest first, then the one before, works like undo.
    #   - `at`, `kind` and `container_number` are never editable: changing
    #     them is really "this was a different event", which is a delete +
    #     re-entry, not a correction.

    EDITABLE_EVENT_FIELDS: ClassVar[set[str]] = {
        "comments", "hauler", "hauler_plate", "cargo_status", "pti_status", "destination",
        "purpose", "generator", "set_point_c", "supply_temp_c", "return_temp_c",
        "seal_number", "tare_weight_kg", "sticker", "cleaning_result", "cross_stuffed",
        "cross_stuff_target", "new_container_number", "original_emptied",
    }

    def edit_event(self, event_id: int, **fields) -> Event:
        ev = self.s.get(Event, event_id)
        if ev is None or ev.voided_at is not None:
            raise YardError(f"no active event {event_id}")
        unknown = set(fields) - self.EDITABLE_EVENT_FIELDS
        if unknown:
            raise YardError(f"cannot edit: {', '.join(sorted(unknown))}")
        for k, v in fields.items():
            setattr(ev, k, v)
        self.s.commit()
        return ev

    def void_event(self, event_id: int) -> Event:
        """Delete an event. Only the most recent event for its container may go."""
        ev = self.s.get(Event, event_id)
        if ev is None or ev.voided_at is not None:
            raise YardError(f"no active event {event_id}")
        hist = self.history(ev.container_number)
        if not hist or hist[-1].id != ev.id:
            if hist:
                raise YardError(
                    f"only the most recent event for {ev.container_number} can be deleted — "
                    f"that's the {hist[-1].kind.value.replace('_', ' ')} at {hist[-1].at:%Y-%m-%d %H:%M}"
                )
            raise YardError(f"{ev.container_number} has no active events to delete")
        from src.db import utcnow
        ev.voided_at = utcnow()
        self.s.commit()
        return ev

    def list_containers(self, q: str | None = None, limit: int = 1000) -> list[Container]:
        stmt = select(Container).order_by(Container.number).limit(limit)
        if q:
            stmt = stmt.where(Container.number.contains(q.strip().upper()))
        return list(self.s.scalars(stmt))

    def edit_container(self, number: str, **fields) -> Container:
        c = self.get(number)
        for k, v in fields.items():
            setattr(c, k, v)
        c.validate()
        self.s.commit()
        return c

    def delete_container(self, number: str) -> None:
        c = self.get(number)
        if c.events:
            raise YardError(
                f"{c.number} has recorded events and can't be deleted; delete those first"
            )
        has_readings = self.s.scalar(
            select(TemperatureReading.id).where(TemperatureReading.container_number == c.number).limit(1)
        )
        if has_readings:
            raise YardError(f"{c.number} has temperature readings and can't be deleted")
        self.s.delete(c)
        self.s.commit()

    def delete_unmatched(self, record_id: int) -> None:
        rec = self.s.get(UnmatchedRecord, record_id)
        if rec is None:
            raise YardError(f"no unmatched record {record_id}")
        self.s.delete(rec)
        self.s.commit()
