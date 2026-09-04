"""HTTP layer: routing, status codes, and the YardError -> 409 mapping."""

from .conftest import CONT_A, CONT_B, ago, dry_payload, reefer_payload


def _gate_in(client, container=None, **over):
    body = dict(
        at=ago(hours=2),
        hauler="HD",
        container=container or reefer_payload(),
        cargo_status="Full",
        pti_status="PTI",
    )
    body.update(over)
    return client.post("/api/events/gate-in", json=body)


# --------------------------------------------------------------------------- #
# Reference data + pages
# --------------------------------------------------------------------------- #


def test_options_lists_every_dropdown(client):
    o = client.get("/api/options").json()
    assert "Cold Storage" in o["cross_stuff_targets"]
    assert set(o["container_types"]) == {"Dry", "Reefer"}
    assert "AM" in o["time_slots"]


def test_known_form_pages_render(client):
    for page in ("gate-in", "cross-stuff", "temperature", "events"):
        r = client.get(f"/{page}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")


def test_unknown_page_is_404(client):
    assert client.get("/does-not-exist").status_code == 404


# --------------------------------------------------------------------------- #
# Containers
# --------------------------------------------------------------------------- #


def test_register_and_fetch_container(client):
    r = client.post("/api/containers", json=reefer_payload())
    assert r.status_code == 201
    assert client.get(f"/api/containers/{CONT_A}").status_code == 200


def test_register_duplicate_returns_409(client):
    client.post("/api/containers", json=reefer_payload())
    r = client.post("/api/containers", json=reefer_payload())
    assert r.status_code == 409
    assert "already registered" in r.json()["detail"]


def test_register_bad_check_digit_returns_422(client):
    r = client.post("/api/containers", json=reefer_payload(number="MSKU1000009"))
    assert r.status_code == 422


def test_get_unknown_container_is_404(client):
    assert client.get(f"/api/containers/{CONT_A}/state").status_code == 404


# --------------------------------------------------------------------------- #
# Event flow
# --------------------------------------------------------------------------- #


def test_gate_in_creates_container_and_state(client):
    r = _gate_in(client)
    assert r.status_code == 201
    body = r.json()
    assert body["on_site"] is True
    assert body["visit_count"] == 1
    assert body["container"]["number"] == CONT_A


def test_gate_in_twice_returns_409(client):
    _gate_in(client)
    r = _gate_in(client, at=ago(hours=1))
    assert r.status_code == 409
    assert "already in the yard" in r.json()["detail"]


def test_gate_in_dry_container_without_pti_status(client):
    r = _gate_in(client, container=dry_payload(), cargo_status="Full", pti_status=None)
    assert r.status_code == 201, r.text
    assert r.json()["container"]["container_type"] == "Dry"


def test_full_lifecycle_via_http(client):
    _gate_in(client)
    plug = client.post(
        "/api/events/plug-in",
        json=dict(
            at=ago(hours=1, minutes=30), container_number=CONT_A, purpose="Storage",
            set_point_c=-18, seal_number="L0059326", cargo_status="Full",
        ),
    )
    assert plug.status_code == 201, plug.text
    assert plug.json()["is_plugged"] is True

    out = client.post(
        "/api/events/plug-out",
        json=dict(at=ago(hours=1), container_number=CONT_A, supply_temp_c=-17),
    )
    assert out.status_code == 201
    assert out.json()["is_plugged"] is False

    gate_out = client.post(
        "/api/events/gate-out",
        json=dict(
            at=ago(minutes=30), container_number=CONT_A, hauler="HD",
            destination="LML", cargo_status="Full",
        ),
    )
    assert gate_out.status_code == 201
    assert gate_out.json()["on_site"] is False


def test_events_listing_and_kind_filter(client):
    _gate_in(client)
    client.post(
        "/api/events/cleaning",
        json=dict(at=ago(hours=1), container_number=CONT_A, result="Clean"),
    )
    everything = client.get("/api/events").json()
    assert {e["kind"] for e in everything} == {"gate_in", "cleaning"}
    only_clean = client.get("/api/events", params={"kind": "cleaning"}).json()
    assert len(only_clean) == 1


def test_pti_plug_endpoints_record_pti_kinds(client):
    _gate_in(client)
    pin = client.post(
        "/api/events/plug-in",
        json=dict(
            at=ago(hours=2), container_number=CONT_A, purpose="PTI",
            generator="K7", set_point_c=-18,
        ),
    )
    assert pin.status_code == 201, pin.text
    pout = client.post(
        "/api/events/plug-out",
        json=dict(at=ago(hours=1), container_number=CONT_A, sticker="PASS"),
    )
    assert pout.status_code == 201, pout.text

    kinds = {e["kind"] for e in client.get("/api/events").json()}
    assert kinds == {"gate_in", "pti_plug_in", "pti_plug_out"}
    assert len(client.get("/api/events", params={"kind": "pti_plug_in"}).json()) == 1
    assert client.get("/api/events", params={"kind": "plug_in"}).json() == []


def test_backdated_event_without_comment_is_422(client):
    r = _gate_in(client, at=ago(days=5))
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Cross stuffing over HTTP
# --------------------------------------------------------------------------- #


def test_cross_stuff_endpoint_moves_state(client):
    _gate_in(client)
    _gate_in(client, container=reefer_payload(number=CONT_B), cargo_status="Empty",
             pti_status="NON PTI")
    r = client.post(
        "/api/events/cross-stuff",
        json=dict(
            at=ago(hours=1), ended_at=ago(minutes=30), container_number=CONT_A,
            target="Container", new_container_number=CONT_B, original_emptied=True,
        ),
    )
    assert r.status_code == 201, r.text
    assert r.json()["cargo_status"] == "Empty"
    assert client.get(f"/api/containers/{CONT_B}/state").json()["cargo_status"] == "Full"


def test_cross_stuff_receiver_not_on_site_is_409(client):
    _gate_in(client)
    client.post("/api/containers", json=reefer_payload(number=CONT_B))
    r = client.post(
        "/api/events/cross-stuff",
        json=dict(
            at=ago(hours=1), ended_at=ago(minutes=30), container_number=CONT_A,
            target="Container", new_container_number=CONT_B,
        ),
    )
    assert r.status_code == 409


# --------------------------------------------------------------------------- #
# Temperature endpoints
# --------------------------------------------------------------------------- #


def _plug(client):
    _gate_in(client)
    return client.post(
        "/api/events/plug-in",
        json=dict(
            at=ago(hours=1, minutes=45), container_number=CONT_A, purpose="Storage",
            set_point_c=-18, seal_number="L0059326", cargo_status="Full",
        ),
    )


def test_temperature_crud_roundtrip(client):
    _plug(client)
    create = client.post(
        "/api/temperature",
        json=dict(
            at=ago(hours=1), container_number=CONT_A, time_slot="AM",
            set_point_c=-18, supply_temp_c=-17, return_temp_c=-16, remark="In Range",
        ),
    )
    assert create.status_code == 201, create.text
    rid = create.json()["id"]

    listing = client.get("/api/temperature").json()
    assert len(listing) == 1 and listing[0]["id"] == rid

    patched = client.patch(f"/api/temperature/{rid}", json={"supply_temp_c": -15.0})
    assert patched.status_code == 200
    assert patched.json()["supply_temp_c"] == -15.0

    deleted = client.delete(f"/api/temperature/{rid}")
    assert deleted.status_code == 200
    assert deleted.json()["voided_at"] is not None


def test_temperature_on_unplugged_container_is_409(client):
    _gate_in(client)
    r = client.post(
        "/api/temperature",
        json=dict(
            at=ago(hours=1), container_number=CONT_A, time_slot="AM",
            set_point_c=-18, supply_temp_c=-17, return_temp_c=-16, remark="In Range",
        ),
    )
    assert r.status_code == 409


def test_temperature_never_shows_up_in_the_event_log(client):
    _plug(client)
    client.post(
        "/api/temperature",
        json=dict(
            at=ago(hours=1), container_number=CONT_A, time_slot="AM",
            set_point_c=-18, supply_temp_c=-17, return_temp_c=-16, remark="In Range",
        ),
    )
    kinds = {e["kind"] for e in client.get("/api/events").json()}
    assert "temperature" not in kinds


# --------------------------------------------------------------------------- #
# Corrections
# --------------------------------------------------------------------------- #


def test_delete_non_latest_event_is_409_with_guidance(client):
    _gate_in(client)
    client.post(
        "/api/events/cleaning",
        json=dict(at=ago(hours=1), container_number=CONT_A, result="Clean"),
    )
    events = client.get("/api/events").json()
    gate_in_id = next(e["id"] for e in events if e["kind"] == "gate_in")
    r = client.delete(f"/api/events/{gate_in_id}")
    assert r.status_code == 409
    assert "later" in r.json()["detail"]


def test_edit_event_comment(client):
    _gate_in(client)
    ev_id = client.get("/api/events").json()[0]["id"]
    r = client.patch(f"/api/events/{ev_id}", json={"comments": "checked by S. Rose"})
    assert r.status_code == 200
    assert r.json()["comments"] == "checked by S. Rose"
