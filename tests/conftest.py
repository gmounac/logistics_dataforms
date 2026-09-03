"""Shared fixtures.

Every test gets a fresh in-memory SQLite database (one connection, held open
for the test via StaticPool) so nothing leaks between tests and `yard.db` on
disk is never touched.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db import Base
from src.enums import (
    ContainerReeferType,
    ContainerSize,
    ContainerType,
    ShippingLine,
    UnitManufacturer,
)
from src.models import Container, iso6346_check_digit
from src.services import YardService

NOW = datetime.now(UTC)


def iso(body: str) -> str:
    """Complete a 10-char owner+serial prefix with its ISO 6346 check digit.

    e.g. iso("MSKU123456") -> "MSKU1234563"
    """
    assert len(body) == 10, "pass the 4 letters + 6 digits, no check digit"
    return body + str(iso6346_check_digit(body))


# A few ready-made valid numbers.
CONT_A = iso("MSKU100000")
CONT_B = iso("MSKU200000")
CONT_C = iso("MSKU300000")
CONT_D = iso("MSKU400000")


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(session_factory) -> Iterator[Session]:
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def yard(session) -> YardService:
    return YardService(session)


@pytest.fixture
def client(session_factory) -> Iterator[TestClient]:
    """FastAPI client wired to the test database via a dependency override."""
    from src.api import app, get_yard

    def _override() -> Iterator[YardService]:
        s = session_factory()
        try:
            yield YardService(s)
        finally:
            s.close()

    app.dependency_overrides[get_yard] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def reefer(number: str = CONT_A, **over) -> Container:
    kw = dict(
        number=number,
        shipping_line=ShippingLine.MAERSK,
        container_type=ContainerType.REEFER,
        size=ContainerSize.FEU,
        reefer_type=ContainerReeferType.STANDARD,
        unit_manufacturer=UnitManufacturer.CARRIER,
    )
    kw.update(over)
    return Container(**kw)


def dry(number: str = CONT_A, **over) -> Container:
    kw = dict(
        number=number,
        shipping_line=ShippingLine.MAERSK,
        container_type=ContainerType.DRY,
        size=ContainerSize.FEU,
    )
    kw.update(over)
    return Container(**kw)


def reefer_payload(number: str = CONT_A, **over) -> dict:
    p = dict(
        number=number,
        shipping_line="MAERSK",
        container_type="Reefer",
        size="FEU",
        reefer_type="Standard",
        unit_manufacturer="Carrier",
    )
    p.update(over)
    return p


def dry_payload(number: str = CONT_A, **over) -> dict:
    p = dict(number=number, shipping_line="MAERSK", container_type="Dry", size="FEU")
    p.update(over)
    return p


def ago(**kw) -> str:
    """ISO-8601 timestamp `kw` ago, with offset — for request bodies."""
    return (NOW - timedelta(**kw)).isoformat()
