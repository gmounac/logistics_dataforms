"""YardService invariants — the rules that keep the event log foldable."""

from datetime import timedelta
from sys import orig_argv

import pytest

from src.enums import (
    CleaningResult,
    ContainerStatus,
    CrossStuffTarget,
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
from src.services import YardError

from .conftest import CONT_A, CONT_B, CONT_C, NOW, dry, reefer

# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


def test_register_then_duplicate_is_rejected(yard):
    yard.register(reefer(CONT_A))
    with pytest.raises(YardError, match="already registered"):
        yard.register(reefer(CONT_A))


def test_get_unknown_raises(yard):
    with pytest.raises(YardError, match="unknown container"):
        yard.get(CONT_A)


def test_ensure_conflicting_details_is_rejected(yard):
    yard.register(reefer(CONT_A, size="FEU"))
    with pytest.raises(YardError, match="on file"):
        yard.ensure(reefer(CONT_A, size="TEU"))


# --------------------------------------------------------------------------- #
# Gate in / out
# --------------------------------------------------------------------------- #


def gate_in_reefer(yard, number=CONT_A, at=None, **over):
    kw = dict(
        at=at or NOW - timedelta(days=1),
        hauler=Hauler.HD,
        cargo_status=ContainerStatus.FULL,
        pti_status=PTIStatus.PTI,
    )
    kw.update(over)
    return yard.gate_in(reefer(number), **kw)


def test_gate_in_registers_on_first_sight(yard):
    st = gate_in_reefer(yard)
    assert st.on_site
    assert st.visit_count == 1
    assert yard.get(CONT_A).number == CONT_A


def test_cannot_gate_in_a_container_already_on_site(yard):
    gate_in_reefer(yard)
    with pytest.raises(YardError, match="already in the yard"):
        gate_in_reefer(yard, at=NOW - timedelta(hours=1))


def test_gate_out_requires_on_site(yard):
    yard.register(reefer(CONT_A))
    with pytest.raises(YardError, match="not in the yard"):
        yard.gate_out(
            CONT_A,
            at=NOW,
            hauler=Hauler.HD,
            destination=Destination.LML,
            cargo_status=ContainerStatus.EMPTY,
        )


def test_events_must_be_chronological(yard):
    gate_in_reefer(yard, at=NOW - timedelta(days=1))
    with pytest.raises(YardError, match="earlier than"):
        yard.clean(CONT_A, at=NOW - timedelta(days=2), result=CleaningResult.CLEAN)


def test_visit_count_increments_each_gate_in(yard):
    gate_in_reefer(yard, at=NOW - timedelta(days=5))
    yard.gate_out(
        CONT_A,
        at=NOW - timedelta(days=4),
        hauler=Hauler.HD,
        destination=Destination.LML,
        cargo_status=ContainerStatus.EMPTY,
    )
    gate_in_reefer(yard, at=NOW - timedelta(days=3))
    assert yard.state(CONT_A).visit_count == 2


def test_dry_container_pti_status_rules(yard):
    with pytest.raises(YardError, match="only applies to reefers"):
        yard.gate_in(
            dry(CONT_A),
            at=NOW - timedelta(days=1),
            hauler=Hauler.HD,
            cargo_status=ContainerStatus.FULL,
            pti_status=PTIStatus.PTI,
        )


def test_reefer_gate_in_needs_pti_status(yard):
    with pytest.raises(YardError, match="pti_status is required"):
        yard.gate_in(
            reefer(CONT_A),
            at=NOW - timedelta(days=1),
            hauler=Hauler.HD,
            cargo_status=ContainerStatus.FULL,
            pti_status=None,
        )


# --------------------------------------------------------------------------- #
# Plug in / out
# --------------------------------------------------------------------------- #


def test_plug_in_rejects_dry_container(yard):
    yard.gate_in(
        dry(CONT_A),
        at=NOW - timedelta(days=1),
        hauler=Hauler.HD,
        cargo_status=ContainerStatus.FULL,
    )
    with pytest.raises(YardError, match="not a reefer"):
        yard.plug_in(CONT_A, at=NOW, purpose=PlugPurpose.STORAGE, set_point_c=-18)


def test_plug_in_rejects_set_point_below_unit_minimum(yard):
    gate_in_reefer(yard)
    with pytest.raises(YardError, match="below the"):
        yard.plug_in(
            CONT_A,
            at=NOW - timedelta(hours=1),
            purpose=PlugPurpose.STORAGE,
            set_point_c=-40,
            seal_number="L0059326",
        )


def test_double_plug_in_is_rejected(yard):
    gate_in_reefer(yard)
    yard.plug_in(
        CONT_A,
        at=NOW - timedelta(hours=10),
        purpose=PlugPurpose.PTI,
        generator=Generator.K7,
        set_point_c=-18,
    )
    with pytest.raises(YardError, match="already plugged in"):
        yard.plug_in(
            CONT_A,
            at=NOW - timedelta(hours=9),
            purpose=PlugPurpose.PTI,
            generator=Generator.K7,
            set_point_c=-18,
        )


def test_plug_out_requires_plug_in_first(yard):
    gate_in_reefer(yard)
    with pytest.raises(YardError, match="not plugged in"):
        yard.plug_out(CONT_A, at=NOW)


def test_pti_plug_out_requires_a_sticker(yard):
    gate_in_reefer(yard)
    yard.plug_in(
        CONT_A,
        at=NOW - timedelta(hours=10),
        purpose=PlugPurpose.PTI,
        generator=Generator.K7,
        set_point_c=-18,
    )
    with pytest.raises(YardError, match="sticker is required"):
        yard.plug_out(CONT_A, at=NOW - timedelta(hours=5))


def test_pti_plug_out_sets_pti_status_from_sticker(yard):
    gate_in_reefer(yard)
    yard.plug_in(
        CONT_A,
        at=NOW - timedelta(hours=10),
        purpose=PlugPurpose.PTI,
        generator=Generator.K7,
        set_point_c=-18,
    )
    yard.plug_out(CONT_A, at=NOW - timedelta(hours=5), sticker=Sticker.PASS)
    assert yard.state(CONT_A).pti_status is PTIStatus.PTI
    assert not yard.state(CONT_A).is_plugged


def test_pti_plugs_get_their_own_event_kind(yard):
    gate_in_reefer(yard)
    yard.plug_in(
        CONT_A,
        at=NOW - timedelta(hours=10),
        purpose=PlugPurpose.PTI,
        generator=Generator.K7,
        set_point_c=-18,
    )
    yard.plug_out(CONT_A, at=NOW - timedelta(hours=5), sticker=Sticker.PASS)
    kinds = [e.kind for e in yard.history(CONT_A)]
    assert kinds == [EventKind.GATE_IN, EventKind.PTI_PLUG_IN, EventKind.PTI_PLUG_OUT]
    # folding still treats them as plug in / plug out
    assert not yard.state(CONT_A).is_plugged


def test_storage_plugs_keep_the_plain_kind(yard):
    gate_in_reefer(yard)
    yard.plug_in(
        CONT_A,
        at=NOW - timedelta(hours=10),
        purpose=PlugPurpose.STORAGE,
        set_point_c=-18,
        seal_number="L0059326",
        cargo_status=ContainerStatus.FULL,
    )
    yard.plug_out(CONT_A, at=NOW - timedelta(hours=5))
    kinds = [e.kind for e in yard.history(CONT_A)]
    assert kinds == [EventKind.GATE_IN, EventKind.PLUG_IN, EventKind.PLUG_OUT]


def test_pti_kind_filter_excludes_storage_plugs(yard):
    gate_in_reefer(yard, number=CONT_A)
    yard.plug_in(
        CONT_A,
        at=NOW - timedelta(hours=10),
        purpose=PlugPurpose.PTI,
        generator=Generator.K7,
        set_point_c=-18,
    )
    yard.plug_out(CONT_A, at=NOW - timedelta(hours=9), sticker=Sticker.PASS)
    yard.plug_in(
        CONT_A,
        at=NOW - timedelta(hours=8),
        purpose=PlugPurpose.STORAGE,
        set_point_c=-18,
        seal_number="L0059326",
        cargo_status=ContainerStatus.FULL,
    )
    assert len(yard.events(kind=EventKind.PTI_PLUG_IN)) == 1
    assert len(yard.events(kind=EventKind.PLUG_IN)) == 1


def test_gate_out_blocked_while_plugged_in(yard):
    gate_in_reefer(yard)
    yard.plug_in(
        CONT_A,
        at=NOW - timedelta(hours=10),
        purpose=PlugPurpose.STORAGE,
        set_point_c=-18,
        seal_number="L0059326",
        cargo_status=ContainerStatus.FULL,
    )
    with pytest.raises(YardError, match="still plugged in"):
        yard.gate_out(
            CONT_A,
            at=NOW - timedelta(hours=1),
            hauler=Hauler.HD,
            destination=Destination.LML,
            cargo_status=ContainerStatus.FULL,
        )


# --------------------------------------------------------------------------- #
# Cleaning
# --------------------------------------------------------------------------- #


def test_one_clean_per_visit_unless_cross_stuffed(yard):
    gate_in_reefer(yard)
    yard.clean(CONT_A, at=NOW - timedelta(hours=9), result=CleaningResult.CLEAN)
    with pytest.raises(YardError, match="already cleaned this visit"):
        yard.clean(CONT_A, at=NOW - timedelta(hours=8), result=CleaningResult.CLEAN)
    # cross-stuffed re-wash is allowed
    st = yard.clean(
        CONT_A,
        at=NOW - timedelta(hours=7),
        result=CleaningResult.CLEAN,
        cross_stuffed=True,
    )
    assert st.cleaning_done


def test_cleaning_resets_after_gate_out_and_back_in(yard):
    gate_in_reefer(yard, at=NOW - timedelta(days=5))
    yard.clean(
        CONT_A,
        at=NOW - timedelta(days=5) + timedelta(hours=1),
        result=CleaningResult.CLEAN,
    )
    yard.gate_out(
        CONT_A,
        at=NOW - timedelta(days=4),
        hauler=Hauler.HD,
        destination=Destination.LML,
        cargo_status=ContainerStatus.EMPTY,
    )
    gate_in_reefer(yard, at=NOW - timedelta(days=3))
    assert yard.state(CONT_A).cleaned_this_visit is None
    assert yard.state(CONT_A).last_cleaning is CleaningResult.CLEAN


# --------------------------------------------------------------------------- #
# Cross stuffing
# --------------------------------------------------------------------------- #


def _two_on_site(yard):
    yard.gate_in(
        reefer(CONT_A),
        at=NOW - timedelta(days=1),
        hauler=Hauler.HD,
        cargo_status=ContainerStatus.FULL,
        pti_status=PTIStatus.PTI,
    )
    yard.gate_in(
        reefer(CONT_B),
        at=NOW - timedelta(days=1),
        hauler=Hauler.HD,
        cargo_status=ContainerStatus.EMPTY,
        pti_status=PTIStatus.NON_PTI,
    )


def test_cross_stuff_into_container_moves_cargo_state(yard):
    """Cross stuffing moves cargo state from source to target container."""
    _two_on_site(yard)
    yard.cross_stuff(
        CONT_A,
        at=NOW - timedelta(hours=3),
        ended_at=NOW - timedelta(hours=2),
        target=CrossStuffTarget.CONTAINER,
        new_container_number=CONT_B,
        original_emptied=True,
    )

    cross = yard.history(CONT_A)[-1]

    assert cross.new_container_number == CONT_B
    assert len(yard._inbound_cross_stuff(CONT_B)) == 1

    assert yard.state(CONT_A).cargo_status is ContainerStatus.EMPTY
    assert yard.state(CONT_B).cargo_status is ContainerStatus.FULL


def test_cross_stuff_receiver_seen_by_on_site_listing(yard):
    _two_on_site(yard)
    yard.cross_stuff(
        CONT_A,
        at=NOW - timedelta(hours=3),
        ended_at=NOW - timedelta(hours=2),
        target=CrossStuffTarget.CONTAINER,
        new_container_number=CONT_B,
        original_emptied=True,
    )
    by_number = {s.container.number: s.cargo_status for s in yard.on_site()}
    assert by_number[CONT_B] is ContainerStatus.FULL


def test_cross_stuff_to_cold_storage_needs_no_new_container(yard):
    _two_on_site(yard)
    st = yard.cross_stuff(
        CONT_A,
        at=NOW - timedelta(hours=3),
        ended_at=NOW - timedelta(hours=2),
        target=CrossStuffTarget.COLD_STORAGE,
        original_emptied=True,
    )
    assert st.cargo_status is ContainerStatus.EMPTY


def test_cross_stuff_to_container_requires_new_number(yard):
    _two_on_site(yard)
    with pytest.raises(YardError, match="new container number is required"):
        yard.cross_stuff(
            CONT_A,
            at=NOW - timedelta(hours=3),
            ended_at=NOW - timedelta(hours=2),
            target=CrossStuffTarget.CONTAINER,
        )


def test_cross_stuff_receiver_must_be_on_site(yard):
    yard.gate_in(
        reefer(CONT_A),
        at=NOW - timedelta(days=1),
        hauler=Hauler.HD,
        cargo_status=ContainerStatus.FULL,
        pti_status=PTIStatus.PTI,
    )
    yard.register(reefer(CONT_C))
    with pytest.raises(YardError, match="not in the yard"):
        yard.cross_stuff(
            CONT_A,
            at=NOW - timedelta(hours=3),
            ended_at=NOW - timedelta(hours=2),
            target=CrossStuffTarget.CONTAINER,
            new_container_number=CONT_C,
        )


def test_cross_stuff_into_itself_is_rejected(yard):
    _two_on_site(yard)
    with pytest.raises(YardError, match="same as the original"):
        yard.cross_stuff(
            CONT_A,
            at=NOW - timedelta(hours=3),
            ended_at=NOW - timedelta(hours=2),
            target=CrossStuffTarget.CONTAINER,
            new_container_number=CONT_A,
        )


def test_event_after_inbound_cross_stuff_keeps_receiver_full(yard):
    _two_on_site(yard)
    yard.cross_stuff(
        CONT_A,
        at=NOW - timedelta(hours=3),
        ended_at=NOW - timedelta(hours=2),
        target=CrossStuffTarget.CONTAINER,
        new_container_number=CONT_B,
        original_emptied=True,
    )

    st = yard.clean(
        CONT_B,
        at=NOW - timedelta(hours=1),
        result=CleaningResult.UNCLEAN,
    )

    assert st.cargo_status is ContainerStatus.FULL


def test_cross_stuff_cannot_be_voided_after_receiver_has_later_event(yard):

    _two_on_site(yard)

    yard.cross_stuff(
        CONT_A,
        at=NOW - timedelta(hours=3),
        ended_at=NOW - timedelta(hours=2),
        target=CrossStuffTarget.CONTAINER,
        new_container_number=CONT_B,
        original_emptied=True,
    )

    cross_stuff = yard.history(CONT_A)[-1]

    yard.clean(
        CONT_B,
        at=NOW - timedelta(hours=1),
        result=CleaningResult.UNCLEAN,
    )
    with pytest.raises(YardError, match="later"):
        yard.void_event(cross_stuff.id)


# --------------------------------------------------------------------------- #
# Temperature readings (own table)
# --------------------------------------------------------------------------- #


def _plug_for_temps(yard):
    gate_in_reefer(yard)
    yard.plug_in(
        CONT_A,
        at=NOW - timedelta(hours=12),
        purpose=PlugPurpose.STORAGE,
        set_point_c=-18,
        seal_number="L0059326",
        cargo_status=ContainerStatus.FULL,
    )


def test_temperature_check_requires_plugged_in(yard):
    gate_in_reefer(yard)
    with pytest.raises(YardError, match="not plugged in"):
        yard.temperature_check(
            CONT_A,
            at=NOW,
            time_slot=TimeSlot.AM,
            set_point_c=-18,
            supply_temp_c=-17,
            return_temp_c=-16,
            remark=TemperatureRemark.IN_RANGE,
        )


def test_temperature_reading_before_plug_in_is_rejected(yard):
    _plug_for_temps(yard)
    with pytest.raises(YardError, match="before the plug in"):
        yard.temperature_check(
            CONT_A,
            at=NOW - timedelta(hours=20),
            time_slot=TimeSlot.AM,
            set_point_c=-18,
            supply_temp_c=-17,
            return_temp_c=-16,
            remark=TemperatureRemark.IN_RANGE,
        )


def test_temperature_readings_out_of_order_are_allowed(yard):
    _plug_for_temps(yard)
    yard.temperature_check(
        CONT_A,
        at=NOW - timedelta(hours=4),
        time_slot=TimeSlot.PM,
        set_point_c=-18,
        supply_temp_c=-17,
        return_temp_c=-16,
        remark=TemperatureRemark.IN_RANGE,
    )
    # earlier slot, entered afterwards
    yard.temperature_check(
        CONT_A,
        at=NOW - timedelta(hours=8),
        time_slot=TimeSlot.NOON,
        set_point_c=-18,
        supply_temp_c=-17,
        return_temp_c=-16,
        remark=TemperatureRemark.IN_RANGE,
    )
    assert len(yard.temperature_readings()) == 2


def test_edit_and_void_temperature(yard):
    _plug_for_temps(yard)
    r = yard.temperature_check(
        CONT_A,
        at=NOW - timedelta(hours=4),
        time_slot=TimeSlot.PM,
        set_point_c=-18,
        supply_temp_c=-17,
        return_temp_c=-16,
        remark=TemperatureRemark.IN_RANGE,
    )
    yard.edit_temperature(r.id, supply_temp_c=-15.5)
    assert yard.s.get(type(r), r.id).supply_temp_c == -15.5
    with pytest.raises(YardError, match="cannot edit"):
        yard.edit_temperature(r.id, container_number="whatever")
    yard.void_temperature(r.id)
    assert yard.temperature_readings(include_voided=False) == []


def test_temperature_readings_do_not_appear_in_event_log(yard):
    _plug_for_temps(yard)
    yard.temperature_check(
        CONT_A,
        at=NOW - timedelta(hours=4),
        time_slot=TimeSlot.PM,
        set_point_c=-18,
        supply_temp_c=-17,
        return_temp_c=-16,
        remark=TemperatureRemark.IN_RANGE,
    )
    assert all(e.kind != "temperature" for e in yard.events())


# --------------------------------------------------------------------------- #
# Corrections: edit / void
# --------------------------------------------------------------------------- #


def test_only_latest_event_can_be_voided(yard):
    gate_in_reefer(yard)
    yard.clean(CONT_A, at=NOW - timedelta(hours=2), result=CleaningResult.CLEAN)
    hist = yard.history(CONT_A)
    with pytest.raises(YardError, match="later"):
        yard.void_event(hist[0].id)  # the gate-in
    yard.void_event(hist[-1].id)  # the cleaning — allowed
    assert len(yard.history(CONT_A)) == 1


def test_voided_event_is_ignored_by_state(yard):
    gate_in_reefer(yard)
    yard.clean(CONT_A, at=NOW - timedelta(hours=2), result=CleaningResult.CLEAN)
    ev = yard.history(CONT_A)[-1]
    yard.void_event(ev.id)
    assert yard.state(CONT_A).cleaned_this_visit is None


def test_edit_event_rejects_locked_fields(yard):
    gate_in_reefer(yard)
    ev = yard.history(CONT_A)[-1]
    with pytest.raises(YardError, match="cannot edit"):
        yard.edit_event(ev.id, at=NOW.isoformat())


def test_delete_container_blocked_when_it_has_events(yard):
    gate_in_reefer(yard)
    with pytest.raises(YardError, match="recorded events"):
        yard.delete_container(CONT_A)


def test_unused_container_can_be_deleted(yard):
    yard.register(reefer(CONT_A))
    yard.delete_container(CONT_A)
    with pytest.raises(YardError, match="unknown container"):
        yard.get(CONT_A)
