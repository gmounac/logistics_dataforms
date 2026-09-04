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

`dashboard.py` is also served live inside the app at **`/reports/dashboard/`**
(run mode, no editable code, any signed-in user). It's a marimo ASGI sub-app
mounted on the FastAPI server, sharing the same session cookie.

Every page and every `/api/*` route (except `/api/login` and the login page)
requires a signed-in session. The theme toggle (auto / light / dark) is in the
header on every page and is remembered per device.

## Accounts

Session-cookie auth with three hierarchical roles:

| role       | can do                                                            |
|------------|------------------------------------------------------------------|
| `viewer`   | every read (GET)                                                 |
| `operator` | viewer + record events, temperature readings, shifting, unmatched |
| `admin`    | operator + edit/void records, the container registry, user accounts |

Create the first account from the command line (there is no sign-up):

```sh
uv run manage.py add <username> admin       # prompts for a password
uv run manage.py list
uv run manage.py role <username> operator
uv run manage.py passwd <username>
uv run manage.py disable <username>
```

After that, admins manage users at the API (`/api/users`). Set
`YARD_SECRET_KEY` in production so sessions survive a restart; set
`YARD_HTTPS_ONLY=1` when served over TLS. Passwords are argon2id hashed.

## Tests

```sh
uv run pytest                  # in-memory SQLite, no network
uv run ruff check .
```

`tests/` covers the ISO 6346 check digit (`test_iso6346.py`), the yard
invariants in `YardService` (`test_services.py`), the Pydantic request rules
(`test_schemas.py`), the HTTP layer end to end (`test_api.py`), the WAL
pragmas (`test_db.py`), and login + role enforcement (`test_auth.py`).

## Reports

```sh
uv run marimo edit dashboard.py   # daily / weekly / monthly activity report
uv run marimo edit entry.py       # bulk-load historical data from Google Sheets
```
