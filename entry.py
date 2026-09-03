import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")

with app.setup:
    import datetime as dt


    import marimo as mo
    import polars as pl
    import sqlalchemy
    from scan_google_sheet import scan_google_sheet

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


@app.cell
def _():
    import duckdb

    DATABASE_URL = "yard.db"
    engine = duckdb.connect(DATABASE_URL, read_only=False)
    return (engine,)


@app.cell
def _(engine):
    _df = mo.sql(
        f"""
        SELECT * FROM main.containers
        """,
        engine=engine
    )
    return


@app.cell
def _():
    gatein_df = scan_google_sheet(url="https://docs.google.com/spreadsheets/d/1L9qkq9WlIa2j5DcvoLvxkqYogRg76S-e8OxAIyLruAE/edit?gid=1165006056#gid=1165006056",sheet_name="GateIn")

    plugin_df = scan_google_sheet(url="https://docs.google.com/spreadsheets/d/1L9qkq9WlIa2j5DcvoLvxkqYogRg76S-e8OxAIyLruAE/edit?gid=1165006056#gid=1165006056",sheet_name="ContainerPlugIn")

    plugout_df = scan_google_sheet(url="https://docs.google.com/spreadsheets/d/1L9qkq9WlIa2j5DcvoLvxkqYogRg76S-e8OxAIyLruAE/edit?gid=1165006056#gid=1165006056",sheet_name="ContainerPlugOut")

    gateout_df = scan_google_sheet(url="https://docs.google.com/spreadsheets/d/1L9qkq9WlIa2j5DcvoLvxkqYogRg76S-e8OxAIyLruAE/edit?gid=1165006056#gid=1165006056",sheet_name="ContainerGateOut")
    return gatein_df, gateout_df, plugin_df, plugout_df


@app.cell
def _():
    pti_plugin_df = scan_google_sheet(url="https://docs.google.com/spreadsheets/d/1L9qkq9WlIa2j5DcvoLvxkqYogRg76S-e8OxAIyLruAE/edit?gid=1165006056#gid=1165006056",sheet_name="ContainerPTI")

    pti_plugout_df = scan_google_sheet(url="https://docs.google.com/spreadsheets/d/1L9qkq9WlIa2j5DcvoLvxkqYogRg76S-e8OxAIyLruAE/edit?gid=1165006056#gid=1165006056",sheet_name="ContainerPTIUnplug")
    return pti_plugin_df, pti_plugout_df


@app.cell
def _():
    cleaning_df = scan_google_sheet(url="https://docs.google.com/spreadsheets/d/1L9qkq9WlIa2j5DcvoLvxkqYogRg76S-e8OxAIyLruAE/edit?gid=1165006056#gid=1165006056",sheet_name="ContainerCleaning")
    return (cleaning_df,)


@app.cell
def _():
    cross_stuffing_df = scan_google_sheet(url="https://docs.google.com/spreadsheets/d/1VbfiiWsp8yxs6KSR1CXpw1S_35tYlWV8UjjWah9Afpw/edit?gid=757201340#gid=757201340",sheet_name="CrossStuffing")
    return (cross_stuffing_df,)


@app.cell
def _(
    cleaning_df,
    cross_stuffing_df,
    engine,
    gatein_df,
    gateout_df,
    plugin_df,
    plugout_df,
    pti_plugin_df,
    pti_plugout_df,
    seen,
):
    # Backfill main.containers with every container referenced by any event sheet
    # so the FK on main.events resolves. Best-effort defaults for gaps.
    _num = r"[A-Z]{3}[UJZ][0-9]{7}"
    containers_ready = mo.sql(
        rf"""
        INSERT INTO main.containers
            (number, shipping_line, container_type, size, reefer_type, unit_manufacturer, created_at)
        WITH seen AS (
            SELECT trim("Container Number") AS number FROM gatein_df
            UNION SELECT trim("Container Number") FROM gateout_df
            UNION SELECT trim("Container Number") FROM plugin_df
            UNION SELECT trim("Container Number") FROM plugout_df
            UNION SELECT trim("Container Number") FROM pti_plugin_df
            UNION SELECT trim("Container Number") FROM pti_plugout_df
            UNION SELECT trim("container_number") FROM cleaning_df
            UNION SELECT trim(origin)             FROM cross_stuffing_df
            UNION SELECT trim(destination)        FROM cross_stuffing_df
        )
        SELECT DISTINCT
            number, 'MAERSK', 'Reefer', 'FEU', 'Standard', 'Carrier', now()::TIMESTAMP
        FROM seen
        WHERE regexp_full_match(number, '{_num}')
          AND number NOT IN (SELECT number FROM main.containers)
        """,
        engine=engine,
    )
    return (containers_ready,)


@app.cell
def _(
    all_ev,
    cleaning_df,
    containers_ready,
    cross_stuffing_df,
    e_cleaning,
    e_cross_stuff,
    e_gate_in,
    e_gate_out,
    e_plug_in,
    e_plug_out,
    e_pti_in,
    e_pti_out,
    engine,
    gatein_df,
    gateout_df,
    plugin_df,
    plugout_df,
    pti_plugin_df,
    pti_plugout_df,
):
    # Load main.events from all eight sheets (temperature + shifting ignored).
    # Sheet times are Mahe local (UTC+4); every "- INTERVAL 4 HOUR" -> UTC.
    _num = r"[A-Z]{3}[UJZ][0-9]{7}"
    _ = containers_ready  # run after the container backfill
    events_loaded = mo.sql(
        rf"""
        INSERT INTO main.events
          (id, kind, container_number, "at", created_at, voided_at, comments,
           hauler, hauler_plate, cargo_status, pti_status, destination,
           purpose, generator, set_point_c, supply_temp_c, return_temp_c,
           seal_number, tare_weight_kg, sticker, plug_in_id,
           cleaning_result, cross_stuffed,
           ended_at, cross_stuff_target, new_container_number, original_emptied)
        WITH
        e_gate_in AS (
          SELECT
            'gate_in' AS kind, trim("Container Number") AS container_number,
            (coalesce(try_cast("Date" AS DATE), try_strptime(CAST("Date" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast("Time In" AS TIME),TIME '00:00')) - INTERVAL 4 HOUR AS "at",
            coalesce(coalesce(try_cast("Timestamp" AS TIMESTAMP), try_strptime(CAST("Timestamp" AS VARCHAR),'%d/%m/%Y %H:%M:%S')) - INTERVAL 4 HOUR,
                     (coalesce(try_cast("Date" AS DATE), try_strptime(CAST("Date" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast("Time In" AS TIME),TIME '00:00')) - INTERVAL 4 HOUR) AS created_at,
            NULL::TIMESTAMP AS voided_at, coalesce("Comments",'') AS comments,
            CASE
              WHEN nullif(trim("Haulage"),'') IS NULL THEN 'IPHS'
              WHEN upper(split_part("Haulage",' - ',1)) IN ('HD','LML','ACL','IPHS','UCPS') THEN upper(split_part("Haulage",' - ',1))
              WHEN upper(split_part("Haulage",' - ',1))='FEROX FEED' THEN 'Ferox Feed'
              WHEN upper(split_part("Haulage",' - ',1))='MAHE DESIGN & BUILD' THEN 'Mahe Design & Build'
              ELSE NULL END AS hauler,
            coalesce(nullif(trim(split_part("Haulage",' - ',2)),''),'S1313') AS hauler_plate,
            CASE lower(trim("Status")) WHEN 'empty' THEN 'Empty' WHEN 'partial' THEN 'Partial' WHEN 'full' THEN 'Full' WHEN 'completed' THEN 'Completed' ELSE NULL END AS cargo_status,
            CASE WHEN ("Type" ILIKE '%dry%' OR "Type" ILIKE '%not applicable%') THEN NULL
              ELSE CASE upper(trim("PTI Status"))
                WHEN 'PTI' THEN 'PTI' WHEN 'NON PTI' THEN 'NON PTI' WHEN 'NON-PTI' THEN 'NON PTI'
                WHEN 'NA' THEN 'NA' WHEN 'N/A' THEN 'NA' WHEN 'DAMAGED' THEN 'Damaged' WHEN 'MALFUNCTION' THEN 'Malfunction' ELSE NULL END
            END AS pti_status,
            NULL::VARCHAR AS destination, NULL::VARCHAR AS purpose, NULL::VARCHAR AS generator,
            NULL::DOUBLE AS set_point_c, NULL::DOUBLE AS supply_temp_c, NULL::DOUBLE AS return_temp_c,
            NULL::VARCHAR AS seal_number, NULL::BIGINT AS tare_weight_kg, NULL::VARCHAR AS sticker, NULL::BIGINT AS plug_in_id,
            NULL::VARCHAR AS cleaning_result, NULL::BOOLEAN AS cross_stuffed,

            NULL::TIMESTAMP AS ended_at, NULL::VARCHAR AS cross_stuff_target, NULL::VARCHAR AS new_container_number, NULL::BOOLEAN AS original_emptied
          FROM gatein_df
          WHERE regexp_full_match(trim("Container Number"),'{_num}') AND coalesce(try_cast("Date" AS DATE), try_strptime(CAST("Date" AS VARCHAR),'%d/%m/%Y')::DATE) IS NOT NULL
          QUALIFY row_number() OVER (PARTITION BY container_number, "at") = 1
        ),
        e_gate_out AS (
          SELECT
            'gate_out' AS kind, trim("Container Number") AS container_number,
            (coalesce(try_cast("Date" AS DATE), try_strptime(CAST("Date" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast("Time Out" AS TIME),TIME '00:00')) - INTERVAL 4 HOUR AS "at",
            coalesce(coalesce(try_cast("Timestamp" AS TIMESTAMP), try_strptime(CAST("Timestamp" AS VARCHAR),'%d/%m/%Y %H:%M:%S')) - INTERVAL 4 HOUR,
                     (coalesce(try_cast("Date" AS DATE), try_strptime(CAST("Date" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast("Time Out" AS TIME),TIME '00:00')) - INTERVAL 4 HOUR) AS created_at,
            NULL::TIMESTAMP AS voided_at, ''::VARCHAR AS comments,
            CASE upper(trim(regexp_replace("Hauler",'\s*S\d+\s*$','')))
              WHEN 'HD' THEN 'HD' WHEN 'LML' THEN 'LML' WHEN 'ACL' THEN 'ACL' WHEN 'IPHS' THEN 'IPHS' WHEN 'UCPS' THEN 'UCPS'
              WHEN 'FEROX FEED' THEN 'Ferox Feed' WHEN 'MAHE DESIGN' THEN 'Mahe Design & Build' WHEN 'MAHE DESIGN & BUILD' THEN 'Mahe Design & Build'
              ELSE NULL END AS hauler,
            nullif(regexp_extract("Hauler",'(S\d+)\s*$',1),'') AS hauler_plate,
            CASE lower(trim("Status")) WHEN 'empty' THEN 'Empty' WHEN 'partial' THEN 'Partial' WHEN 'full' THEN 'Full' WHEN 'completed' THEN 'Completed' ELSE NULL END AS cargo_status,
            NULL::VARCHAR AS pti_status,
            CASE upper(trim("Destination")) WHEN 'LML' THEN 'LML' WHEN 'IOT' THEN 'IOT' WHEN 'FISHING PORT' THEN 'Fishing Port'
              WHEN 'ZONE 14' THEN 'Zone 14' WHEN 'HD YARD' THEN 'HD Yard' WHEN 'JHL' THEN 'JHL' ELSE NULL END AS destination,
            NULL::VARCHAR AS purpose, NULL::VARCHAR AS generator,
            NULL::DOUBLE AS set_point_c, NULL::DOUBLE AS supply_temp_c, NULL::DOUBLE AS return_temp_c,
            NULL::VARCHAR AS seal_number, NULL::BIGINT AS tare_weight_kg, NULL::VARCHAR AS sticker, NULL::BIGINT AS plug_in_id,
            NULL::VARCHAR AS cleaning_result, NULL::BOOLEAN AS cross_stuffed,

            NULL::TIMESTAMP AS ended_at, NULL::VARCHAR AS cross_stuff_target, NULL::VARCHAR AS new_container_number, NULL::BOOLEAN AS original_emptied
          FROM gateout_df
          WHERE regexp_full_match(trim("Container Number"),'{_num}') AND coalesce(try_cast("Date" AS DATE), try_strptime(CAST("Date" AS VARCHAR),'%d/%m/%Y')::DATE) IS NOT NULL
          QUALIFY row_number() OVER (PARTITION BY container_number, "at") = 1
        ),
        e_plug_in AS (
          SELECT
            'plug_in' AS kind, trim("Container Number") AS container_number,
            (coalesce(try_cast("Date" AS DATE), try_strptime(CAST("Date" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast("Time" AS TIME),TIME '00:00')) - INTERVAL 4 HOUR AS "at",
            coalesce(coalesce(try_cast("Timestamp" AS TIMESTAMP), try_strptime(CAST("Timestamp" AS VARCHAR),'%d/%m/%Y %H:%M:%S')) - INTERVAL 4 HOUR,
                     (coalesce(try_cast("Date" AS DATE), try_strptime(CAST("Date" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast("Time" AS TIME),TIME '00:00')) - INTERVAL 4 HOUR) AS created_at,
            NULL::TIMESTAMP AS voided_at,
            coalesce(concat_ws(' - ', nullif(trim("Remarks"),''),
                CASE WHEN nullif(trim("Location"),'') IS NOT NULL THEN 'loc: '||trim("Location") END),'') AS comments,
            NULL::VARCHAR AS hauler, NULL::VARCHAR AS hauler_plate,
            CASE lower(trim("Status")) WHEN 'empty' THEN 'Empty' WHEN 'partial' THEN 'Partial' WHEN 'full' THEN 'Full' WHEN 'completed' THEN 'Completed' ELSE NULL END AS cargo_status,
            NULL::VARCHAR AS pti_status, NULL::VARCHAR AS destination,
            'Storage'::VARCHAR AS purpose, NULL::VARCHAR AS generator,
            try_cast("Set Point" AS DOUBLE) AS set_point_c,
            try_cast(regexp_extract("Plugin Temp",'SUP[:\s]*(-?\d+(\.\d+)?)',1) AS DOUBLE) AS supply_temp_c,
            try_cast(regexp_extract("Plugin Temp",'RET[:\s]*(-?\d+(\.\d+)?)',1) AS DOUBLE) AS return_temp_c,
            nullif(trim("Seal Number"),'') AS seal_number,
            try_cast("Tare Weight" AS BIGINT) AS tare_weight_kg,
            NULL::VARCHAR AS sticker, NULL::BIGINT AS plug_in_id,
            NULL::VARCHAR AS cleaning_result, NULL::BOOLEAN AS cross_stuffed,

            NULL::TIMESTAMP AS ended_at, NULL::VARCHAR AS cross_stuff_target, NULL::VARCHAR AS new_container_number, NULL::BOOLEAN AS original_emptied
          FROM plugin_df
          WHERE regexp_full_match(trim("Container Number"),'{_num}') AND coalesce(try_cast("Date" AS DATE), try_strptime(CAST("Date" AS VARCHAR),'%d/%m/%Y')::DATE) IS NOT NULL
          QUALIFY row_number() OVER (PARTITION BY container_number, "at") = 1
        ),
        e_plug_out AS (
          SELECT
            'plug_out' AS kind, trim("Container Number") AS container_number,
            (coalesce(try_cast("Date" AS DATE), try_strptime(CAST("Date" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast("Time Out" AS TIME),TIME '00:00')) - INTERVAL 4 HOUR AS "at",
            coalesce(coalesce(try_cast("Timestamp" AS TIMESTAMP), try_strptime(CAST("Timestamp" AS VARCHAR),'%d/%m/%Y %H:%M:%S')) - INTERVAL 4 HOUR,
                     (coalesce(try_cast("Date" AS DATE), try_strptime(CAST("Date" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast("Time Out" AS TIME),TIME '00:00')) - INTERVAL 4 HOUR) AS created_at,
            NULL::TIMESTAMP AS voided_at, coalesce(nullif(trim("Remarks"),''),'') AS comments,
            NULL::VARCHAR AS hauler, NULL::VARCHAR AS hauler_plate, NULL::VARCHAR AS cargo_status,
            NULL::VARCHAR AS pti_status, NULL::VARCHAR AS destination, 'Storage'::VARCHAR AS purpose, NULL::VARCHAR AS generator,
            NULL::DOUBLE AS set_point_c,
            try_cast(regexp_extract("Plug Out Temperature",'SUP[:\s]*(-?\d+(\.\d+)?)',1) AS DOUBLE) AS supply_temp_c,
            try_cast(regexp_extract("Plug Out Temperature",'RET[:\s]*(-?\d+(\.\d+)?)',1) AS DOUBLE) AS return_temp_c,
            NULL::VARCHAR AS seal_number, NULL::BIGINT AS tare_weight_kg, NULL::VARCHAR AS sticker, NULL::BIGINT AS plug_in_id,
            NULL::VARCHAR AS cleaning_result, NULL::BOOLEAN AS cross_stuffed,

            NULL::TIMESTAMP AS ended_at, NULL::VARCHAR AS cross_stuff_target, NULL::VARCHAR AS new_container_number, NULL::BOOLEAN AS original_emptied
          FROM plugout_df
          WHERE regexp_full_match(trim("Container Number"),'{_num}') AND coalesce(try_cast("Date" AS DATE), try_strptime(CAST("Date" AS VARCHAR),'%d/%m/%Y')::DATE) IS NOT NULL
          QUALIFY row_number() OVER (PARTITION BY container_number, "at") = 1
        ),
        e_pti_in AS (
          SELECT
            'pti_plug_in' AS kind, trim("Container Number") AS container_number,
            (coalesce(try_cast("Date Plugin" AS DATE), try_strptime(CAST("Date Plugin" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast("Time Plugin" AS TIME),TIME '00:00')) - INTERVAL 4 HOUR AS "at",
            coalesce(coalesce(try_cast("Timestamp" AS TIMESTAMP), try_strptime(CAST("Timestamp" AS VARCHAR),'%d/%m/%Y %H:%M:%S')) - INTERVAL 4 HOUR,
                     (coalesce(try_cast("Date Plugin" AS DATE), try_strptime(CAST("Date Plugin" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast("Time Plugin" AS TIME),TIME '00:00')) - INTERVAL 4 HOUR) AS created_at,
            NULL::TIMESTAMP AS voided_at, coalesce("Comments",'') AS comments,
            NULL::VARCHAR AS hauler, NULL::VARCHAR AS hauler_plate, NULL::VARCHAR AS cargo_status,
            NULL::VARCHAR AS pti_status, NULL::VARCHAR AS destination,
            'PTI'::VARCHAR AS purpose,
            CASE upper(trim("Generator")) WHEN 'K2' THEN 'K2' WHEN 'K3' THEN 'K3' WHEN 'K6' THEN 'K6' WHEN 'K7' THEN 'K7'
              WHEN 'K8' THEN 'K8' WHEN 'K9' THEN 'K9' WHEN 'AKSA' THEN 'AKSA' ELSE NULL END AS generator,
            try_cast("Set Point" AS DOUBLE) AS set_point_c,
            NULL::DOUBLE AS supply_temp_c, NULL::DOUBLE AS return_temp_c,
            NULL::VARCHAR AS seal_number, NULL::BIGINT AS tare_weight_kg, NULL::VARCHAR AS sticker, NULL::BIGINT AS plug_in_id,
            NULL::VARCHAR AS cleaning_result, NULL::BOOLEAN AS cross_stuffed,

            NULL::TIMESTAMP AS ended_at, NULL::VARCHAR AS cross_stuff_target, NULL::VARCHAR AS new_container_number, NULL::BOOLEAN AS original_emptied
          FROM pti_plugin_df
          WHERE regexp_full_match(trim("Container Number"),'{_num}') AND coalesce(try_cast("Date Plugin" AS DATE), try_strptime(CAST("Date Plugin" AS VARCHAR),'%d/%m/%Y')::DATE) IS NOT NULL
          QUALIFY row_number() OVER (PARTITION BY container_number, "at") = 1
        ),
        e_pti_out AS (
          SELECT
            'pti_plug_out' AS kind, trim("Container Number") AS container_number,
            (coalesce(try_cast("Date Unplugin" AS DATE), try_strptime(CAST("Date Unplugin" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast("Time Unplugin" AS TIME),TIME '00:00')) - INTERVAL 4 HOUR AS "at",
            coalesce(coalesce(try_cast("Timestamp" AS TIMESTAMP), try_strptime(CAST("Timestamp" AS VARCHAR),'%d/%m/%Y %H:%M:%S')) - INTERVAL 4 HOUR,
                     (coalesce(try_cast("Date Unplugin" AS DATE), try_strptime(CAST("Date Unplugin" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast("Time Unplugin" AS TIME),TIME '00:00')) - INTERVAL 4 HOUR) AS created_at,
            NULL::TIMESTAMP AS voided_at, ''::VARCHAR AS comments,
            NULL::VARCHAR AS hauler, NULL::VARCHAR AS hauler_plate, NULL::VARCHAR AS cargo_status,
            NULL::VARCHAR AS pti_status, NULL::VARCHAR AS destination, 'PTI'::VARCHAR AS purpose, NULL::VARCHAR AS generator,
            NULL::DOUBLE AS set_point_c, NULL::DOUBLE AS supply_temp_c, NULL::DOUBLE AS return_temp_c,
            NULL::VARCHAR AS seal_number, NULL::BIGINT AS tare_weight_kg,
            CASE upper(trim("Sticker")) WHEN 'PASS' THEN 'PASS' WHEN 'RED' THEN 'RED' WHEN 'TBR' THEN 'TBR' WHEN 'NA' THEN 'NA' ELSE NULL END AS sticker,
            NULL::BIGINT AS plug_in_id, NULL::VARCHAR AS cleaning_result, NULL::BOOLEAN AS cross_stuffed,

            NULL::TIMESTAMP AS ended_at, NULL::VARCHAR AS cross_stuff_target, NULL::VARCHAR AS new_container_number, NULL::BOOLEAN AS original_emptied
          FROM pti_plugout_df
          WHERE regexp_full_match(trim("Container Number"),'{_num}') AND coalesce(try_cast("Date Unplugin" AS DATE), try_strptime(CAST("Date Unplugin" AS VARCHAR),'%d/%m/%Y')::DATE) IS NOT NULL
          QUALIFY row_number() OVER (PARTITION BY container_number, "at") = 1
        ),
        e_cleaning AS (
          SELECT
            'cleaning' AS kind, trim("container_number") AS container_number,
            (coalesce(try_cast("date" AS DATE), try_strptime(CAST("date" AS VARCHAR),'%d/%m/%Y')::DATE) + TIME '12:00') - INTERVAL 4 HOUR AS "at",
            coalesce(coalesce(try_cast("Timestamp" AS TIMESTAMP), try_strptime(CAST("Timestamp" AS VARCHAR),'%d/%m/%Y %H:%M:%S')) - INTERVAL 4 HOUR,
                     (coalesce(try_cast("date" AS DATE), try_strptime(CAST("date" AS VARCHAR),'%d/%m/%Y')::DATE) + TIME '12:00') - INTERVAL 4 HOUR) AS created_at,
            NULL::TIMESTAMP AS voided_at,
            coalesce(concat_ws(' - ', nullif(trim("comments"),''),
                CASE WHEN nullif(trim("Invoice To"),'') IS NOT NULL THEN 'invoice: '||trim("Invoice To") END),'') AS comments,
            NULL::VARCHAR AS hauler, NULL::VARCHAR AS hauler_plate, NULL::VARCHAR AS cargo_status,
            NULL::VARCHAR AS pti_status, NULL::VARCHAR AS destination, NULL::VARCHAR AS purpose, NULL::VARCHAR AS generator,
            NULL::DOUBLE AS set_point_c, NULL::DOUBLE AS supply_temp_c, NULL::DOUBLE AS return_temp_c,
            NULL::VARCHAR AS seal_number, NULL::BIGINT AS tare_weight_kg, NULL::VARCHAR AS sticker, NULL::BIGINT AS plug_in_id,
            CASE
              WHEN "cleaning_remarks" ILIKE 'clean' THEN 'Clean' WHEN "cleaning_remarks" ILIKE 'rewash' THEN 'Rewash'
              WHEN "cleaning_remarks" ILIKE 'unclean' THEN 'Unclean' WHEN "cleaning_remarks" ILIKE 'other' THEN 'Other'
              WHEN "cleaning_remarks" ILIKE '%rewash%' THEN 'Rewash'
              WHEN "cleaning_remarks" ILIKE '%unclean%' OR "cleaning_remarks" ILIKE '%fail%' THEN 'Unclean'
              WHEN "cleaning_remarks" ILIKE '%clean%' OR "cleaning_remarks" ILIKE '%pass%' THEN 'Clean' ELSE 'Other' END AS cleaning_result,
            FALSE::BOOLEAN AS cross_stuffed,

            NULL::TIMESTAMP AS ended_at, NULL::VARCHAR AS cross_stuff_target, NULL::VARCHAR AS new_container_number, NULL::BOOLEAN AS original_emptied
          FROM cleaning_df
          WHERE regexp_full_match(trim("container_number"),'{_num}') AND coalesce(try_cast("date" AS DATE), try_strptime(CAST("date" AS VARCHAR),'%d/%m/%Y')::DATE) IS NOT NULL
          QUALIFY row_number() OVER (PARTITION BY container_number, "at") = 1
        ),
        e_cross_stuff AS (
          SELECT
            'cross_stuff' AS kind, trim(origin) AS container_number,
            (coalesce(try_cast("date" AS DATE), try_strptime(CAST("date" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast(start_time AS TIME),TIME '00:00')) - INTERVAL 4 HOUR AS "at",
            (coalesce(try_cast("date" AS DATE), try_strptime(CAST("date" AS VARCHAR),'%d/%m/%Y')::DATE) + coalesce(try_cast(start_time AS TIME),TIME '00:00')) - INTERVAL 4 HOUR AS created_at,
            NULL::TIMESTAMP AS voided_at,
            coalesce(concat_ws(' - ', nullif(trim(remarks),''),
                CASE WHEN nullif(trim(service),'')       IS NOT NULL THEN 'service: '||trim(service) END,
                CASE WHEN nullif(trim(vessel_client),'') IS NOT NULL THEN 'vessel: '||trim(vessel_client) END,

                CASE WHEN nullif(trim(invoiced),'')      IS NOT NULL THEN 'invoiced: '||trim(invoiced) END),'') AS comments,
            NULL::VARCHAR AS hauler, NULL::VARCHAR AS hauler_plate, NULL::VARCHAR AS cargo_status,
            NULL::VARCHAR AS pti_status, NULL::VARCHAR AS destination, NULL::VARCHAR AS purpose, NULL::VARCHAR AS generator,
            NULL::DOUBLE AS set_point_c, NULL::DOUBLE AS supply_temp_c, NULL::DOUBLE AS return_temp_c,
            NULL::VARCHAR AS seal_number, NULL::BIGINT AS tare_weight_kg, NULL::VARCHAR AS sticker, NULL::BIGINT AS plug_in_id,
            NULL::VARCHAR AS cleaning_result, NULL::BOOLEAN AS cross_stuffed,

            CASE
              WHEN try_cast(end_time AS TIME) IS NULL THEN NULL
              WHEN try_cast(end_time AS TIME) < coalesce(try_cast(start_time AS TIME),TIME '00:00')
                THEN (coalesce(try_cast("date" AS DATE), try_strptime(CAST("date" AS VARCHAR),'%d/%m/%Y')::DATE) + try_cast(end_time AS TIME) + INTERVAL 1 DAY) - INTERVAL 4 HOUR
              ELSE (coalesce(try_cast("date" AS DATE), try_strptime(CAST("date" AS VARCHAR),'%d/%m/%Y')::DATE) + try_cast(end_time AS TIME)) - INTERVAL 4 HOUR END AS ended_at,
            CASE
              WHEN upper(trim(destination)) IN ('CCCS','COLDSTORE','COLD STORE','CCCS COLDSTORE') THEN 'Cold Storage'
              WHEN regexp_full_match(trim(destination),'{_num}') AND trim(destination) <> trim(origin) THEN 'Container'
              WHEN upper(trim(destination)) LIKE '%VESSEL%' THEN 'Cargo Vessel' ELSE 'Cold Storage' END AS cross_stuff_target,
            CASE WHEN regexp_full_match(trim(destination),'{_num}') AND trim(destination) <> trim(origin) THEN trim(destination) ELSE NULL END AS new_container_number,
            is_origin_empty = TRUE AS original_emptied
          FROM cross_stuffing_df
          WHERE regexp_full_match(trim(origin),'{_num}') AND coalesce(try_cast("date" AS DATE), try_strptime(CAST("date" AS VARCHAR),'%d/%m/%Y')::DATE) IS NOT NULL
          QUALIFY row_number() OVER (PARTITION BY container_number, "at") = 1
        ),
        all_ev AS (
          SELECT * FROM e_gate_in
          UNION ALL SELECT * FROM e_gate_out
          UNION ALL SELECT * FROM e_plug_in
          UNION ALL SELECT * FROM e_plug_out
          UNION ALL SELECT * FROM e_pti_in
          UNION ALL SELECT * FROM e_pti_out
          UNION ALL SELECT * FROM e_cleaning
          UNION ALL SELECT * FROM e_cross_stuff
        )
        SELECT
          (SELECT coalesce(max(id),0) FROM main.events)
            + row_number() OVER (ORDER BY "at",
                CASE kind WHEN 'gate_in' THEN 0 WHEN 'plug_in' THEN 1 WHEN 'pti_plug_in' THEN 1 WHEN 'cleaning' THEN 2
                          WHEN 'cross_stuff' THEN 3 WHEN 'plug_out' THEN 4 WHEN 'pti_plug_out' THEN 4 WHEN 'gate_out' THEN 5 ELSE 9 END,
                container_number) AS id,
          kind, container_number, "at", created_at, voided_at, comments,
          hauler, hauler_plate, cargo_status, pti_status, destination,
          purpose, generator, set_point_c, supply_temp_c, return_temp_c,
          seal_number, tare_weight_kg, sticker, plug_in_id,
          cleaning_result, cross_stuffed,
          ended_at, cross_stuff_target, new_container_number, original_emptied
        FROM all_ev
        WHERE container_number IN (SELECT number FROM main.containers)
        """,
        engine=engine,
    )
    return


if __name__ == "__main__":
    app.run()
