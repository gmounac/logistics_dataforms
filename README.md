# Container Yard

A FastAPI + SQLAlchemy + SQLite service for a reefer container yard: gate
moves, plug in/out, PTI, cleaning, cross-stuffing, temperature rounds and
shifting jobs, entered from mobile-friendly web forms.

The design is **event-sourced**. `containers` is a registry; `events` is an
append-only log with one row per thing that happened to a container. Current
status — on site, cargo status, PTI status, plugged in, cleaned this visit,
visit count — is *derived* by folding that log (`YardService.fold`), never
stored, so history can always be corrected without the state drifting.

Targets **Android phones and Windows PCs** on the same network. iOS/macOS are
not tested.

---

## Quick start

```sh
uv sync
uv run manage.py add <username> admin        # create the first account (prompts for a password)
uv run main.py                               # serve on 0.0.0.0:8000
```

`main.py` prints both a localhost URL and the machine's LAN address; open the
LAN one on a phone on the same Wi-Fi.

```sh
uv run main.py --port 9000
uv run main.py --reload                       # auto-reload on code changes
```

---

## Accounts & roles

Every page and every `/api/*` route (except the login page and `POST
/api/login`) needs a signed-in session. Auth is a **signed session cookie**
(`itsdangerous` via Starlette's `SessionMiddleware`); passwords are **argon2id**
hashed.

Three hierarchical roles:

| role       | can do                                                                 |
|------------|-----------------------------------------------------------------------|
| `viewer`   | every read (all `GET`)                                                |
| `operator` | viewer **+** record events, temperature readings, shifting, unmatched |
| `admin`    | operator **+** edit/void records, the container registry, user accounts |

There is no self-service sign-up. Bootstrap and recover accounts from the CLI:

```sh
uv run manage.py add <username> <viewer|operator|admin>   # prompts for a password
uv run manage.py list
uv run manage.py role <username> operator
uv run manage.py passwd <username>
uv run manage.py disable <username>       # / enable
```

After the first admin exists, manage users over the API (`/api/users`, admin
only) or add a small admin page. Guards prevent demoting/disabling/deleting
your own account and deleting the last active admin.

A **"← Yard"** link and a **theme toggle** (auto / light / dark, remembered per
device) appear in the header of every page.

---

## The app

Forms (all under auth, `operator`+ to submit):

`/gate-in` · `/gate-out` · `/plug-in` · `/plug-out` · `/pti-plug` ·
`/pti-unplug` · `/cleaning` · `/cross-stuff` · `/temperature` · `/shifting`

Other routes:

| route                  | what                                                            |
|------------------------|----------------------------------------------------------------|
| `/`                    | home: what's in the yard now, quick links                     |
| `/events`              | records viewer — filter, edit (admin), void, CSV export       |
| `/reports/dashboard/`  | the marimo activity report, embedded live (see **Reports**)   |
| `/login`               | sign-in page                                                  |
| `/docs`                | OpenAPI docs                                                  |

### Data model

- **`containers`** — registry keyed on the ISO 6346 `number` (format + check
  digit enforced). Reefer type / unit manufacturer only for reefers.
- **`events`** — the append-only log, single-table inheritance on `kind`:

  | kind            | notes                                                       |
  |-----------------|-------------------------------------------------------------|
  | `gate_in`       | registers the container on first sight; bumps `visit_count` |
  | `gate_out`      | blocked while still plugged in                              |
  | `plug_in`       | storage plug — needs a seal; reefers only                   |
  | `plug_out`      | closes a storage plug                                       |
  | `pti_plug_in`   | pre-trip inspection plug — needs a generator                |
  | `pti_plug_out`  | closes a PTI; carries the `PASS`/`RED`/`TBR` sticker        |
  | `cleaning`      | one `Clean` per visit unless the cargo was cross-stuffed    |
  | `cross_stuff`   | cargo stripped from a container into another box, cold storage or a vessel; `original_emptied` flips the source to Empty and a receiving container to Full |

  PTI plugs are their own kinds (not `plug_in` + a flag) so they count
  separately in reports; `fold()` still treats them as plug in / plug out via
  subclassing.
- **`temperature_readings`** — its own table, not the event log. Rounds happen
  three times a day (`AM`/`NOON`/`PM`), never change derived state, and may be
  entered out of order.
- **`unmatched_records`** — a form submitted for a container the yard didn't
  have where expected, kept intact for someone to resolve later.
- **`shifting_jobs`** — moving containers within the yard for a customer.
- **`users`** — logins (see **Accounts**).

Corrections policy: any event's descriptive fields can be edited in place;
only the *most recent* event for a container can be voided (deleting from the
middle would leave later events resting on a gap). `at`, `kind` and
`container_number` are never editable — that's a delete-and-re-enter.

### Database

SQLite (`yard.db`) with per-connection pragmas set in `src/db.py`:

- `journal_mode=WAL` — readers don't block the writer, so a phone and a PC can
  record at the same time
- `busy_timeout=5000` — wait for the single writer lock instead of erroring
- `synchronous=NORMAL` — the safe WAL pairing, much faster
- `foreign_keys=ON`

Comfortable to low-hundreds-of-thousands of events (tens of MB). It stays fast
because state is derived per container and containers cycle through; the one
thing to watch as the registry grows is `YardService.on_site()`, which folds
every container. If you outgrow SQLite, `YARD_DATABASE_URL` points the same
SQLAlchemy code at Postgres.

### Configuration

| env var              | default              | purpose                                        |
|----------------------|----------------------|-----------------------------------------------|
| `YARD_DATABASE_URL`  | `sqlite:///yard.db`  | any SQLAlchemy URL                            |
| `YARD_SECRET_KEY`    | random per start     | signs the session cookie — **set in prod** or restarts log everyone out |
| `YARD_HTTPS_ONLY`    | off                  | `1` to mark the session cookie Secure (serve over TLS) |

---

## Reports

`dashboard.py` is a **marimo** notebook — a daily / weekly / monthly activity
report with stat tiles, a stacked bar chart per bucket, and per-activity and
full-record tables, plus an "All" tab with container search. Storage vs PTI
plugs are shown separately.

It runs two ways:

```sh
uv run marimo edit dashboard.py         # standalone, editable
```

and it's **mounted live inside the API** at `/reports/dashboard/` — run mode
(charts only, no editable code), behind the same session cookie, any signed-in
role. It's a marimo ASGI sub-app; each viewer session spins a short-lived
kernel.

---

## Bulk-loading history

`entry.py` is a marimo notebook that pulls historical sheets from Google Sheets
with `scan_google_sheet`, then transforms and inserts them into
`main.containers` / `main.events` using **DuckDB SQL** run against `yard.db`.

```sh
uv run marimo edit entry.py
```

It has two SQL cells: a containers backfill (every ISO-6346 number referenced by
any sheet) and an events load (one `INSERT` with a CTE per event kind, an
explicit generated `id`, per-container/-timestamp dedup, and `- INTERVAL 4
HOUR` to convert Mahé local time to stored UTC). Notes in the cell cover the
knobs (drop the interval if the sheets are already UTC; spot-check distinct
`Destination`/`Generator` values against the enums). Do **not** mount this
notebook as a route — running its cells writes to the database and calls Google.

---

## Migrations

`init_db` (run on startup) only *creates* missing tables. For changes to
existing tables, run once after pulling:

```sh
uv run migrate.py              # operates on yard.db
uv run migrate.py other.db
```

It's idempotent and currently: adds the cross-stuff columns; reclassifies old
`plug_in`/`plug_out` rows with `purpose = 'PTI'` (or a sticker) to
`pti_plug_in` / `pti_plug_out`; moves any legacy `kind='temperature'` rows into
`temperature_readings`.

---

## Development

```sh
uv run pytest                  # full suite: in-memory SQLite, no network
uv run ruff check .
```

`tests/`:

| file               | covers                                                        |
|--------------------|-------------------------------------------------------------|
| `test_iso6346.py`  | container-number format + check-digit algorithm             |
| `test_services.py` | every `YardService` invariant (ordering, plug rules, cross-stuff, temperature, corrections) |
| `test_schemas.py`  | the Pydantic request rules (backdating, reefer rules, seal/plate formats, cross-stuff targets) |
| `test_api.py`      | the HTTP layer end to end, `YardError` → 409 vs schema → 422 |
| `test_db.py`       | the WAL / foreign-key pragmas                               |
| `test_auth.py`     | login, sessions, and role enforcement per route            |

The `client` fixture signs in as an admin so API tests read naturally;
`operator_client`, `viewer_client`, `anon_client` cover the rest.

### Layout

```
main.py            server launcher (host/port/reload, prints the LAN URL)
manage.py          user-account CLI
migrate.py         one-off schema fix-ups
dashboard.py       marimo activity report (also served at /reports/dashboard/)
entry.py           marimo bulk loader (Google Sheets -> DuckDB -> yard.db)
src/
  api.py           FastAPI app: routes, auth wiring, the marimo mount
  auth.py          argon2 hashing + session helpers
  db.py            engine, session factory, UTC column type, SQLite pragmas
  enums.py         every domain enumeration (stored as its string value)
  models.py        SQLAlchemy models
  schemas.py       Pydantic request/response models + business validation
  services.py      YardService (yard ops) and UserService (accounts)
static/            the forms, login page, shared yard.css / yard.js
tests/
```
