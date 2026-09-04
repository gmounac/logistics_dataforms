import marimo

__generated_with = "0.24.0"
app = marimo.App(width="full", app_title="Dashboard")

with app.setup:
    import datetime as dt

    import altair as alt
    import marimo as mo
    import polars as pl
    import sqlalchemy

    DB_URL = "sqlite:///yard.db"

    KIND_LABEL = {
        "gate_in": "Gate in",
        "gate_out": "Gate out",
        "plug_in": "Storage plug in",
        "plug_out": "Storage plug out",
        "pti_plug_in": "PTI plug in",
        "pti_plug_out": "PTI plug out",
        "cleaning": "Cleaning",
        "cross_stuff": "Cross stuffing",
        "temperature": "Temperature round",
    }
    KIND_ORDER = list(KIND_LABEL.values())


@app.cell(hide_code=True)
def _():
    mo.md("""
    # Yard activity report

    Daily, weekly and monthly views of everything recorded in the yard —
    gate moves, plugs, cleanings, cross-stuffing and temperature rounds.
    Pick a date in each tab; times are UTC as stored.
    """)
    return


@app.cell
def _():
    _engine = sqlalchemy.create_engine(DB_URL)

    def _read(sql: str) -> pl.DataFrame:
        with _engine.connect() as con:
            return pl.read_database(sql, con)


    # id, kind, container_number, at, comments



    _events = _read("SELECT id, kind, container_number, at, comments FROM events WHERE voided_at IS NULL")
    _temps = _read(
        "SELECT id, container_number, at, time_slot, set_point_c, supply_temp_c, "
        "return_temp_c, temperature_remark, comments FROM temperature_readings WHERE voided_at IS NULL"
    )
    containers = _read("SELECT number, container_type, shipping_line FROM containers")

    def _with_at(df: pl.DataFrame) -> pl.DataFrame:
        if df.is_empty():
            return df.with_columns(pl.lit(None).cast(pl.Datetime).alias("at"))
        return df.with_columns(
            pl.col("at").str.to_datetime("%Y-%m-%d %H:%M:%S%.f", strict=False)
        )

    events = _with_at(_events)
    temps = _with_at(_temps)

    # One long frame: every recorded thing, with a kind and a timestamp.
    activity = pl.concat(
        [
            events.select("at", "kind", "container_number"),
            temps.select(
                "at",
                pl.lit("temperature").alias("kind"),
                "container_number",
            ),
        ],
        how="vertical",
    ).with_columns(
        pl.col("kind").replace(KIND_LABEL).alias("activity")
    )

    mo.md(
        f"Loaded **{events.height}** events and **{temps.height}** temperature readings "
        f"across **{containers.height}** registered containers."
    )
    return activity, temps


@app.cell
def _():
    today = dt.date.today()
    day_anchor = mo.ui.date(value=today, label="Day")
    week_anchor = mo.ui.date(value=today, label="Week containing")
    month_anchor = mo.ui.date(value=today, label="Month containing")
    return day_anchor, month_anchor, week_anchor


@app.function
def period_window(kind: str, anchor: dt.date) -> tuple[dt.datetime, dt.datetime, str]:
    """(start, end-exclusive, heading) for a daily / weekly / monthly window."""
    start = dt.datetime.combine(anchor, dt.time.min)
    if kind == "day":
        return start, start + dt.timedelta(days=1), anchor.strftime("%A %d %B %Y")
    if kind == "week":
        monday = start - dt.timedelta(days=start.weekday())
        end = monday + dt.timedelta(days=7)
        return monday, end, f"{monday:%d %b} – {end - dt.timedelta(days=1):%d %b %Y}"
    first = start.replace(day=1)
    end = (first.replace(day=28) + dt.timedelta(days=7)).replace(day=1)
    return first, end, first.strftime("%B %Y")


@app.function
def period_report(activity: pl.DataFrame, kind: str, anchor: dt.date, temps: pl.DataFrame) -> mo.Html:
    start, end, heading = period_window(kind, anchor)
    win = activity.filter((pl.col("at") >= start) & (pl.col("at") < end))
    tw = temps.filter((pl.col("at") >= start) & (pl.col("at") < end)) if not temps.is_empty() else temps

    def c(k: str) -> int:
        return int(win.filter(pl.col("kind") == k).height)

    alarms = 0
    if not tw.is_empty() and "temperature_remark" in tw.columns:
        alarms = int(tw.filter(pl.col("temperature_remark") == "Alarm").height)

    tiles = mo.hstack(
        [
            mo.stat(win.height, label="Recorded", bordered=True),
            mo.stat(c("gate_in"), label="Gate ins", bordered=True),
            mo.stat(c("gate_out"), label="Gate outs", bordered=True),
            mo.stat(c("gate_in") - c("gate_out"), label="Net in yard", bordered=True),
            mo.stat(c("plug_in"), label="Storage plugs", bordered=True),
            mo.stat(c("pti_plug_in"), label="PTI plugs", bordered=True),
            mo.stat(c("cleaning"), label="Cleanings", bordered=True),
            mo.stat(c("cross_stuff"), label="Cross stuffs", bordered=True),
            mo.stat(c("temperature"), label="Temp rounds", bordered=True),
            mo.stat(alarms, label="Temp alarms", bordered=True),
            mo.stat(win["container_number"].n_unique(), label="Containers", bordered=True),
        ],
        wrap=True,
        gap=0.5,
    )

    if win.is_empty():
        return mo.vstack([mo.md(f"### {heading}"), tiles, mo.md("*Nothing recorded in this window.*")])

    bucket = "1h" if kind == "day" else "1d"
    by_bucket = (
        win.with_columns(pl.col("at").dt.truncate(bucket).alias("bucket"))
        .group_by("bucket", "activity")
        .agg(pl.len().alias("count"))
        .sort("bucket")
    )
    chart = mo.ui.altair_chart(
        alt.Chart(by_bucket)
        .mark_bar()
        .encode(
            x=alt.X("bucket:T", title="Hour" if kind == "day" else "Day"),
            y=alt.Y("count:Q", title="Records", stack=True),
            color=alt.Color("activity:N", title="Activity", sort=KIND_ORDER),
            tooltip=["bucket:T", "activity:N", "count:Q"],
        )
        .properties(height=260)
    )

    by_kind = (
        win.group_by("activity")
        .agg(pl.len().alias("count"))
        .sort("count", descending=True)
    )

    detail = win.sort("at", descending=True).select(
        pl.col("at").dt.strftime("%d %b %H:%M").alias("When"),
        pl.col("activity").alias("Activity"),
        pl.col("container_number").alias("Container"),
    )

    return mo.vstack(
        [
            mo.md(f"### {heading}"),
            tiles,
            chart,
            mo.hstack(
                [
                    mo.vstack([mo.md("**By activity**"), mo.ui.table(by_kind, selection=None, page_size=10)]),
                    mo.vstack([mo.md("**Every record**"), mo.ui.table(detail, selection=None, page_size=15)]),
                ],
                widths=[1, 2],
                gap=1,
            ),
        ]
    )


@app.cell
def _(activity, day_anchor, month_anchor, temps, week_anchor):
    mo.ui.tabs(
        {
            "Daily": mo.vstack([day_anchor, period_report(activity, "day", day_anchor.value, temps)]),
            "Weekly": mo.vstack([week_anchor, period_report(activity, "week", week_anchor.value, temps)]),
            "Monthly": mo.vstack([month_anchor, period_report(activity, "month", month_anchor.value, temps)]),
        }
    )
    return


if __name__ == "__main__":
    app.run()
