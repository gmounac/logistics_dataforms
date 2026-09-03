"""Request-model validation — the business rules that used to live in Apps Script."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from src.schemas import (
    CleaningRequest,
    ContainerIn,
    CrossStuffRequest,
    GateInRequest,
    PlugInRequest,
    TemperatureRequest,
)

from .conftest import CONT_A, CONT_B, NOW, dry_payload, reefer_payload


def _gate_in_body(**over) -> dict:
    body = dict(
        at=(NOW - timedelta(hours=1)).isoformat(),
        hauler="HD",
        container=reefer_payload(),
        cargo_status="Full",
        pti_status="PTI",
    )
    body.update(over)
    return body


# --------------------------------------------------------------------------- #
# _EventRequest: timezone + backdating
# --------------------------------------------------------------------------- #


def test_at_must_be_timezone_aware():
    with pytest.raises(ValidationError, match="timezone offset"):
        GateInRequest(**_gate_in_body(at="2026-01-01T09:00:00"))


def test_at_cannot_be_in_the_future():
    with pytest.raises(ValidationError, match="future"):
        GateInRequest(**_gate_in_body(at=(NOW + timedelta(hours=2)).isoformat()))


def test_backdated_entry_needs_comments():
    old = (NOW - timedelta(days=5)).isoformat()
    with pytest.raises(ValidationError, match="older than 3 days"):
        GateInRequest(**_gate_in_body(at=old))
    # with a comment it is accepted
    GateInRequest(**_gate_in_body(at=old, comments="late data entry"))


# --------------------------------------------------------------------------- #
# Container / reefer rules
# --------------------------------------------------------------------------- #


def test_container_number_check_digit_enforced():
    with pytest.raises(ValidationError):
        ContainerIn(**reefer_payload(number="MSKU1000009"))  # bad check digit


def test_dry_container_rejects_reefer_fields():
    with pytest.raises(ValidationError, match="no reefer type"):
        ContainerIn(**dry_payload(reefer_type="Standard"))


def test_reefer_requires_type_and_manufacturer():
    with pytest.raises(ValidationError, match="reefer type is required"):
        ContainerIn(**reefer_payload(reefer_type=None))
    with pytest.raises(ValidationError, match="unit manufacturer is required"):
        ContainerIn(**reefer_payload(unit_manufacturer=None))


def test_magnum_manufacturer_whitelist():
    with pytest.raises(ValidationError, match="only made by"):
        ContainerIn(**reefer_payload(reefer_type="Magnum", unit_manufacturer="Carrier"))
    ContainerIn(**reefer_payload(reefer_type="Magnum", unit_manufacturer="Thermoking"))


# --------------------------------------------------------------------------- #
# Gate in status rules
# --------------------------------------------------------------------------- #


def test_gate_in_completed_is_not_an_arrival_status():
    with pytest.raises(ValidationError, match="plug-in status"):
        GateInRequest(**_gate_in_body(cargo_status="Completed"))


def test_gate_in_dry_container_drops_pti_status():
    body = GateInRequest(
        **_gate_in_body(container=dry_payload(), cargo_status="Full", pti_status=None)
    )
    assert body.pti_status is None


def test_gate_in_loaded_reefer_cannot_be_non_pti():
    with pytest.raises(ValidationError, match="loaded reefer cannot be NON PTI"):
        GateInRequest(**_gate_in_body(cargo_status="Full", pti_status="NON PTI"))


def test_gate_in_dry_container_cannot_be_partial():
    with pytest.raises(ValidationError, match="cannot be Partial"):
        GateInRequest(
            **_gate_in_body(container=dry_payload(), cargo_status="Partial", pti_status=None)
        )


# --------------------------------------------------------------------------- #
# Plug in
# --------------------------------------------------------------------------- #


def _plug_body(**over) -> dict:
    body = dict(
        at=(NOW - timedelta(hours=1)).isoformat(),
        container_number=CONT_A,
        purpose="Storage",
        set_point_c=-18,
        seal_number="L0059326",
    )
    body.update(over)
    return body


def test_storage_plug_needs_a_seal():
    with pytest.raises(ValidationError, match="seal number is required"):
        PlugInRequest(**_plug_body(seal_number=None))


def test_pti_plug_needs_a_generator():
    with pytest.raises(ValidationError, match="generator is required"):
        PlugInRequest(**_plug_body(purpose="PTI", seal_number=None, generator=None))


def test_seal_number_format():
    with pytest.raises(ValidationError, match="seal must be"):
        PlugInRequest(**_plug_body(seal_number="XYZ123"))
    PlugInRequest(**_plug_body(seal_number="MLSC0029995"))


def test_set_point_out_of_range_is_rejected():
    with pytest.raises(ValidationError):
        PlugInRequest(**_plug_body(set_point_c=-99))


def test_tare_weight_bounds():
    with pytest.raises(ValidationError):
        PlugInRequest(**_plug_body(tare_weight_kg=500))


# --------------------------------------------------------------------------- #
# Cleaning / temperature
# --------------------------------------------------------------------------- #


def test_cleaning_other_needs_a_comment():
    base = dict(at=(NOW - timedelta(hours=1)).isoformat(), container_number=CONT_A)
    with pytest.raises(ValidationError, match="say what happened"):
        CleaningRequest(**base, result="Other")
    CleaningRequest(**base, result="Other", comments="door damage")


def test_temperature_alarm_needs_a_comment():
    base = dict(
        at=(NOW - timedelta(hours=1)).isoformat(),
        container_number=CONT_A,
        time_slot="AM",
        set_point_c=-18,
        supply_temp_c=-17,
        return_temp_c=-16,
    )
    with pytest.raises(ValidationError, match="describe the alarm"):
        TemperatureRequest(**base, remark="Alarm")
    TemperatureRequest(**base, remark="Alarm", comments="compressor tripped")


def test_temperature_allows_same_day_future_slot():
    # PM reading (nominal 16:00) entered at noon: within 24h window, accepted.
    TemperatureRequest(
        at=(NOW + timedelta(hours=6)).isoformat(),
        container_number=CONT_A,
        time_slot="PM",
        set_point_c=-18,
        supply_temp_c=-17,
        return_temp_c=-16,
        remark="In Range",
    )


# --------------------------------------------------------------------------- #
# Cross stuffing
# --------------------------------------------------------------------------- #


def _xs_body(**over) -> dict:
    body = dict(
        at=(NOW - timedelta(hours=3)).isoformat(),
        ended_at=(NOW - timedelta(hours=2)).isoformat(),
        container_number=CONT_A,
        target="Container",
        new_container_number=CONT_B,
    )
    body.update(over)
    return body


def test_cross_stuff_end_before_start_is_rejected():
    with pytest.raises(ValidationError, match="before the start"):
        CrossStuffRequest(**_xs_body(ended_at=(NOW - timedelta(hours=4)).isoformat()))


def test_cross_stuff_container_target_needs_new_number():
    with pytest.raises(ValidationError, match="new container number is required"):
        CrossStuffRequest(**_xs_body(new_container_number=None))


def test_cross_stuff_non_container_target_rejects_new_number():
    with pytest.raises(ValidationError, match="only applies when transferring"):
        CrossStuffRequest(**_xs_body(target="Cold Storage"))


def test_cross_stuff_new_number_cannot_equal_original():
    with pytest.raises(ValidationError, match="same as the original"):
        CrossStuffRequest(**_xs_body(new_container_number=CONT_A))
