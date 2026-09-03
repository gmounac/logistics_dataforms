# Container Yard

FastAPI + SQLAlchemy + SQLite service for a reefer container yard. State
(on site, cargo status, PTI status, plugged in, cleaned) is *derived* by folding
an append-only event log — never stored.

## Run

```sh
uv run main.py                 # serve on 0.0.0.0:8000, reachable from a phone
uv run main.py --port 9000
uv run migrate.py              # apply schema fix-ups to an existing yard.db
```

Forms: `/gate-in`, `/gate-out`, `/plug-in`, `/plug-out`, `/pti-plug`,
`/pti-unplug`, `/cleaning`, `/temperature`, `/cross-stuff`, `/shifting`.
Records viewer: `/events`. API docs: `/docs`.

## Tests

```sh
uv run pytest                  # 92 tests, in-memory SQLite, no network
uv run ruff check .
```

`tests/` covers the ISO 6346 check digit (`test_iso6346.py`), the yard
invariants in `YardService` (`test_services.py`), the Pydantic request rules
(`test_schemas.py`), and the HTTP layer end to end (`test_api.py`).

## Reports

```sh
uv run marimo edit dashboard.py   # daily / weekly / monthly activity report
uv run marimo edit entry.py       # bulk-load historical data from Google Sheets
```
