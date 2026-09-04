"""FastAPI application.

Run with:  uvicorn src.api:app --reload
Docs at:   http://127.0.0.1:8000/docs
Gate-in:   http://127.0.0.1:8000/gate-in
"""

import os
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Final

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from src import auth, enums
from src.db import init_db, make_engine, make_session_factory
from src.enums import Role
from src.models import Container, User
from src.schemas import (
    CleaningRequest,
    ContainerIn,
    ContainerOut,
    ContainerUpdate,
    CrossStuffRequest,
    EventEdit,
    EventOut,
    GateInRequest,
    GateOutRequest,
    LoginRequest,
    MeOut,
    OptionsOut,
    PlugInRequest,
    PlugOutRequest,
    ShiftingEdit,
    ShiftingIn,
    ShiftingOut,
    StateOut,
    TemperatureEdit,
    TemperatureReadingOut,
    TemperatureRequest,
    UnmatchedIn,
    UnmatchedOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from src.services import ContainerState, UserService, YardError, YardService

DATABASE_URL: str = os.environ.get("YARD_DATABASE_URL", "sqlite:///yard.db")
STATIC_DIR: Path = Path(__file__).resolve().parent.parent / "static"

# Signs the session cookie. Set YARD_SECRET_KEY in production; without it a
# random key is used, so every restart logs everyone out.
SECRET_KEY: str = os.environ.get("YARD_SECRET_KEY") or secrets.token_hex(32)
SESSION_MAX_AGE: Final[int] = 14 * 24 * 3600  # two weeks

engine = make_engine(DATABASE_URL)
SessionLocal = make_session_factory(engine)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db(engine)
    yield


app = FastAPI(title="Container Yard", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=os.environ.get("YARD_HTTPS_ONLY", "").lower() in ("1", "true", "yes"),
)


def get_yard() -> Iterator[YardService]:
    with SessionLocal() as session:
        yield YardService(session)


def get_users() -> Iterator[UserService]:
    with SessionLocal() as session:
        yield UserService(session)


Yard = Annotated[YardService, Depends(get_yard)]
Users = Annotated[UserService, Depends(get_users)]


def current_user(request: Request, users: Users) -> User:
    user = auth.user_from_session(users.s, request)
    if user is None:
        raise HTTPException(401, "sign in required")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(minimum: Role):
    """Route guard: 403 unless the signed-in user's role is at least `minimum`."""

    def dep(user: CurrentUser) -> User:
        if user.role.rank < minimum.rank:
            raise HTTPException(403, f"this action needs the {minimum} role")
        return user

    return dep


viewer = require_role(Role.VIEWER)
operator = require_role(Role.OPERATOR)
admin = require_role(Role.ADMIN)


@app.exception_handler(YardError)
def _yard_error(_: Request, exc: YardError) -> JSONResponse:
    """Invariant violations become 409 Conflict with a plain message."""
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def _state_out(st: ContainerState) -> StateOut:
    return StateOut(
        container=ContainerOut.model_validate(st.container),
        on_site=st.on_site,
        arrived_at=st.arrived_at,
        cargo_status=st.cargo_status,
        pti_status=st.pti_status,
        is_plugged=st.is_plugged,
        plugged_in=EventOut.model_validate(st.plugged_in) if st.plugged_in else None,
        last_cleaning=st.last_cleaning,
        cleaned_this_visit=st.cleaned_this_visit,
        cleaning_done=st.cleaning_done,
        visit_count=st.visit_count,
        last_event=EventOut.model_validate(st.last_event) if st.last_event else None,
    )


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


@app.get("/api/options", response_model=OptionsOut, dependencies=[Depends(viewer)])
def options() -> OptionsOut:
    return OptionsOut(
        shipping_lines=enums.ShippingLine.list_all(),
        container_types=enums.ContainerType.list_all(),
        sizes=enums.ContainerSize.list_all(),
        reefer_types=enums.ContainerReeferType.list_all(),
        unit_manufacturers=enums.UnitManufacturer.list_all(),
        cargo_statuses=enums.ContainerStatus.list_all(),
        pti_statuses=enums.PTIStatus.list_all(),
        haulers=enums.Hauler.list_all(),
        destinations=enums.Destination.list_all(),
        generators=enums.Generator.list_all(),
        plug_purposes=enums.PlugPurpose.list_all(),
        stickers=enums.Sticker.list_all(),
        cleaning_results=enums.CleaningResult.list_all(),
        temperature_remarks=enums.TemperatureRemark.list_all(),
        customers=enums.Customer.list_all(),
        time_slots=enums.TimeSlot.list_all(),
        cross_stuff_targets=enums.CrossStuffTarget.list_all(),
    )


# --------------------------------------------------------------------------- #
# Containers
# --------------------------------------------------------------------------- #


@app.post(
    "/api/containers",
    response_model=ContainerOut,
    status_code=201,
    dependencies=[Depends(admin)],
)
def register_container(body: ContainerIn, yard: Yard) -> Container:
    return yard.register(Container(**body.model_dump()))


@app.get(
    "/api/containers", response_model=list[ContainerOut], dependencies=[Depends(viewer)]
)
def list_containers(
    yard: Yard, q: str | None = None, limit: int = 1000
) -> list[Container]:
    return yard.list_containers(q, limit)


@app.get(
    "/api/containers/{number}",
    response_model=ContainerOut,
    dependencies=[Depends(viewer)],
)
def get_container(number: str, yard: Yard) -> Container:
    try:
        return yard.get(number)
    except YardError as e:
        raise HTTPException(404, str(e))


@app.patch(
    "/api/containers/{number}",
    response_model=ContainerOut,
    dependencies=[Depends(admin)],
)
def update_container(number: str, body: ContainerUpdate, yard: Yard) -> Container:
    """Fix a typo in the registry. Does not touch that container's event history."""
    try:
        return yard.edit_container(number, **body.model_dump())
    except YardError as e:
        if "unknown container" in str(e):
            raise HTTPException(404, str(e))
        raise


@app.delete("/api/containers/{number}", status_code=204, dependencies=[Depends(admin)])
def delete_container(number: str, yard: Yard) -> None:
    """Remove a container registered by mistake. Refuses if it has any events."""
    yard.delete_container(number)


@app.get(
    "/api/containers/{number}/state",
    response_model=StateOut,
    dependencies=[Depends(viewer)],
)
def container_state(number: str, yard: Yard) -> StateOut:
    try:
        return _state_out(yard.state(number))
    except YardError as e:
        raise HTTPException(404, str(e))


@app.get(
    "/api/containers/{number}/history",
    response_model=list[EventOut],
    dependencies=[Depends(viewer)],
)
def container_history(number: str, yard: Yard):
    return yard.history(yard.get(number).number)


@app.get(
    "/api/yard/on-site", response_model=list[StateOut], dependencies=[Depends(viewer)]
)
def on_site(
    yard: Yard,
    q: str | None = None,
    reefer: bool | None = None,
    plugged: bool | None = None,
    purpose: enums.PlugPurpose | None = None,
    cleanable: bool | None = None,
    limit: int = 200,
) -> list[StateOut]:
    """Containers in the yard now. `q` filters by number; the flags narrow further.

    `purpose` keeps only containers plugged in for that reason; `cleanable=true`
    drops containers already cleaned this visit.
    """
    states = yard.on_site(
        q, reefer=reefer, plugged=plugged, purpose=purpose, cleanable=cleanable
    )
    return [_state_out(st) for st in states[:limit]]


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


@app.get("/api/events", response_model=list[EventOut], dependencies=[Depends(viewer)])
def list_events(
    yard: Yard,
    kind: enums.EventKind | None = None,
    q: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 500,
):
    """Event log for the viewer, newest first. Dates are inclusive bounds on `at`."""
    return yard.events(
        kind=kind, q=q, date_from=date_from, date_to=date_to, limit=limit
    )


@app.patch(
    "/api/events/{event_id}", response_model=EventOut, dependencies=[Depends(admin)]
)
def edit_event(event_id: int, body: EventEdit, yard: Yard):
    """Correct a typo on an event. `at`/`kind`/`container_number` can't change."""
    return yard.edit_event(event_id, **body.model_dump(exclude_unset=True))


@app.delete(
    "/api/events/{event_id}", response_model=EventOut, dependencies=[Depends(admin)]
)
def delete_event(event_id: int, yard: Yard):
    """Delete an event. Only the most recent event for its container may go —
    the server explains what to delete first otherwise."""
    return yard.void_event(event_id)


@app.post(
    "/api/events/gate-in",
    response_model=StateOut,
    status_code=201,
    dependencies=[Depends(operator)],
)
def gate_in(body: GateInRequest, yard: Yard) -> StateOut:
    container = Container(**body.container.model_dump())
    data = body.model_dump(exclude={"container"})
    return _state_out(yard.gate_in(container, **data))


@app.post(
    "/api/events/gate-out",
    response_model=StateOut,
    status_code=201,
    dependencies=[Depends(operator)],
)
def gate_out(body: GateOutRequest, yard: Yard) -> StateOut:
    data = body.model_dump(exclude={"container_number"})
    return _state_out(yard.gate_out(body.container_number, **data))


@app.post(
    "/api/events/plug-in",
    response_model=StateOut,
    status_code=201,
    dependencies=[Depends(operator)],
)
def plug_in(body: PlugInRequest, yard: Yard) -> StateOut:
    data = body.model_dump(exclude={"container_number"})
    return _state_out(yard.plug_in(body.container_number, **data))


@app.post(
    "/api/events/plug-out",
    response_model=StateOut,
    status_code=201,
    dependencies=[Depends(operator)],
)
def plug_out(body: PlugOutRequest, yard: Yard) -> StateOut:
    data = body.model_dump(exclude={"container_number"})
    return _state_out(yard.plug_out(body.container_number, **data))


@app.post(
    "/api/events/cleaning",
    response_model=StateOut,
    status_code=201,
    dependencies=[Depends(operator)],
)
def cleaning(body: CleaningRequest, yard: Yard) -> StateOut:
    return _state_out(
        yard.clean(
            body.container_number,
            at=body.at,
            result=body.result,
            cross_stuffed=body.cross_stuffed,
            comments=body.comments,
        )
    )


@app.post(
    "/api/temperature",
    response_model=TemperatureReadingOut,
    status_code=201,
    dependencies=[Depends(operator)],
)
def create_temperature(body: TemperatureRequest, yard: Yard) -> TemperatureReadingOut:
    data = body.model_dump(exclude={"container_number"})
    reading = yard.temperature_check(body.container_number, **data)
    return TemperatureReadingOut.model_validate(reading)


@app.get(
    "/api/temperature",
    response_model=list[TemperatureReadingOut],
    dependencies=[Depends(viewer)],
)
def list_temperature(
    yard: Yard,
    q: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 500,
):
    """Temperature-round readings, newest first. Dates are inclusive bounds on `at`."""
    return yard.temperature_readings(
        q=q, date_from=date_from, date_to=date_to, limit=limit
    )


@app.patch(
    "/api/temperature/{reading_id}",
    response_model=TemperatureReadingOut,
    dependencies=[Depends(admin)],
)
def edit_temperature(reading_id: int, body: TemperatureEdit, yard: Yard):
    return yard.edit_temperature(reading_id, **body.model_dump(exclude_unset=True))


@app.delete(
    "/api/temperature/{reading_id}",
    response_model=TemperatureReadingOut,
    dependencies=[Depends(admin)],
)
def delete_temperature(reading_id: int, yard: Yard):
    """Void a reading. Kept for the audit trail, shown struck through in Records."""
    return yard.void_temperature(reading_id)


@app.post(
    "/api/events/cross-stuff",
    response_model=StateOut,
    status_code=201,
    dependencies=[Depends(operator)],
)
def cross_stuff(body: CrossStuffRequest, yard: Yard) -> StateOut:
    data = body.model_dump(exclude={"container_number"})
    return _state_out(yard.cross_stuff(body.container_number, **data))


# --------------------------------------------------------------------------- #
# Unmatched records
# --------------------------------------------------------------------------- #


@app.post(
    "/api/unmatched",
    response_model=UnmatchedOut,
    status_code=201,
    dependencies=[Depends(operator)],
)
def create_unmatched(body: UnmatchedIn, yard: Yard):
    """Keep a submission for a container the yard doesn't have where expected."""
    return yard.record_unmatched(**body.model_dump())


@app.get(
    "/api/unmatched", response_model=list[UnmatchedOut], dependencies=[Depends(viewer)]
)
def list_unmatched(yard: Yard, include_resolved: bool = False):
    return yard.unmatched(include_resolved=include_resolved)


@app.post(
    "/api/unmatched/{record_id}/resolve",
    response_model=UnmatchedOut,
    dependencies=[Depends(operator)],
)
def resolve_unmatched(record_id: int, yard: Yard):
    return yard.resolve_unmatched(record_id)


@app.delete(
    "/api/unmatched/{record_id}", status_code=204, dependencies=[Depends(admin)]
)
def delete_unmatched(record_id: int, yard: Yard) -> None:
    """Remove an unmatched record outright, e.g. a duplicate or test entry."""
    yard.delete_unmatched(record_id)


# --------------------------------------------------------------------------- #
# Shifting
# --------------------------------------------------------------------------- #


@app.post(
    "/api/shifting",
    response_model=ShiftingOut,
    status_code=201,
    dependencies=[Depends(operator)],
)
def create_shifting(body: ShiftingIn, yard: Yard):
    return yard.shift(**body.model_dump())


@app.get(
    "/api/shifting", response_model=list[ShiftingOut], dependencies=[Depends(viewer)]
)
def list_shifting(yard: Yard, limit: int = 100):
    return yard.shifting_jobs(limit)


@app.patch(
    "/api/shifting/{job_id}", response_model=ShiftingOut, dependencies=[Depends(admin)]
)
def update_shifting(job_id: int, body: ShiftingEdit, yard: Yard):
    return yard.edit_shifting(job_id, **body.model_dump())


@app.delete("/api/shifting/{job_id}", status_code=204, dependencies=[Depends(admin)])
def delete_shifting(job_id: int, yard: Yard) -> None:
    yard.delete_shifting(job_id)


# --------------------------------------------------------------------------- #
# Auth & accounts
# --------------------------------------------------------------------------- #


@app.post("/api/login", response_model=MeOut)
def login(body: LoginRequest, request: Request, users: Users) -> MeOut:
    try:
        user = users.authenticate(body.username, body.password)
    except YardError:
        raise HTTPException(401, "wrong username or password")
    request.session[auth.SESSION_KEY] = user.id
    return MeOut.of(user)


@app.post("/api/logout", status_code=204)
def logout(request: Request, _: CurrentUser) -> None:
    request.session.clear()


@app.get("/api/me", response_model=MeOut)
def me(user: CurrentUser) -> MeOut:
    return MeOut.of(user)


@app.get("/api/users", response_model=list[UserOut], dependencies=[Depends(admin)])
def list_users(users: Users):
    return users.list_()


@app.post(
    "/api/users", response_model=UserOut, status_code=201, dependencies=[Depends(admin)]
)
def create_user(body: UserCreate, users: Users):
    return users.create(username=body.username, password=body.password, role=body.role)


@app.patch(
    "/api/users/{user_id}", response_model=UserOut, dependencies=[Depends(admin)]
)
def update_user(user_id: int, body: UserUpdate, users: Users, actor: CurrentUser):
    return users.update(
        user_id, acting_user_id=actor.id, **body.model_dump(exclude_unset=True)
    )


@app.delete("/api/users/{user_id}", status_code=204, dependencies=[Depends(admin)])
def delete_user(user_id: int, users: Users, actor: CurrentUser) -> None:
    users.delete(user_id, acting_user_id=actor.id)


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #

PAGES = {
    "gate-in",
    "gate-out",
    "plug-in",
    "plug-out",
    "pti-plug",
    "pti-unplug",
    "cleaning",
    "temperature",
    "cross-stuff",
    "shifting",
    "events",
}
NO_CACHE = {"Cache-Control": "no-store"}


def _page_or_login(name: str, request: Request, users: Users, next_path: str):
    """Serve a static page, or bounce to the login screen when not signed in."""
    if auth.user_from_session(users.s, request) is None:
        return RedirectResponse(f"/login?next={next_path}", status_code=303)
    return FileResponse(STATIC_DIR / name, headers=NO_CACHE)


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html", headers=NO_CACHE)


@app.get("/", include_in_schema=False)
def home_page(request: Request, users: Users):
    return _page_or_login("index.html", request, users, "/")


@app.get("/{page}", include_in_schema=False)
def form_page(page: str, request: Request, users: Users):
    if page not in PAGES:
        raise HTTPException(404, "no such page")
    return _page_or_login(f"{page.replace('-', '_')}.html", request, users, f"/{page}")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------------------- #
# Embedded marimo report  —  GET /reports/dashboard
# --------------------------------------------------------------------------- #
#
# The activity notebook is served in run mode (charts only, no editable code)
# and mounted as an ASGI sub-app. The outer SessionMiddleware has already put
# the signed cookie on the scope, so the guard below only has to check that a
# session exists — the report is read-only, so any signed-in role may see it.

DASHBOARD_NB = Path(__file__).resolve().parent.parent / "dashboard.py"

# marimo serves the report full-screen with no way back to the yard, so drop a
# floating link into every page it renders (html_head is injected into <head>).
#
# TODO: Move this to the static folder in a separate file
_MARIMO_HEAD = """
<style>
  #yard-back {
    position: fixed; top: 10px; left: 10px; z-index: 2147483647;
    font: 600 13px/1 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding: 7px 12px; border-radius: 6px; text-decoration: none;
    color: #fff; background: #e5731a; box-shadow: 0 2px 10px rgba(0,0,0,.3);
  }
  #yard-back:hover { background: #cf6512; }
</style>
<script>
  addEventListener("DOMContentLoaded", function () {
    if (document.getElementById("yard-back")) return;
    var a = document.createElement("a");
    a.id = "yard-back";
    a.href = "/";
    a.textContent = "\\u2190 Yard";
    document.body.appendChild(a);
  });
</script>
"""


def _marimo_require_login(app: ASGIApp) -> ASGIApp:
    """
    Middleware that requires the user to be logged in to access the inner app.
    """
    async def guard(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            await app(scope, receive, send)
            return

        connection   = HTTPConnection(scope)
        with SessionLocal() as session:
            user = auth.user_from_session(session, connection)
        if user is None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await RedirectResponse(
                    url="/login?next=/reports/dashboard",
                    status_code=303,
                )(scope, receive, send)
            return
        await app(scope, receive, send)

    return guard


try:
    import marimo as _marimo

    _reports = (
        _marimo.create_asgi_app(include_code=False, html_head=_MARIMO_HEAD)
        .with_app(
            path="/dashboard",
            root=str(DASHBOARD_NB),
            middleware=[_marimo_require_login],
        )
        .build()
    )
    app.mount("/reports", _reports)
except Exception as _exc:  # noqa: BLE001 - a broken notebook must not take the API down with it
    import logging

    logging.getLogger("uvicorn.error").warning(
        "report at /reports/dashboard not mounted: %s", _exc
    )
