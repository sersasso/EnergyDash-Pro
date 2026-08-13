from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import subprocess
import shutil
import platform
import os

import pandas as pd
import plotly.graph_objects as go
import yaml
from dash import Dash, dcc, html, Input, Output, State, ctx
import dash_bootstrap_components as dbc
from sqlalchemy import create_engine, text

CONFIG_FILE = "/etc/energydash-pro/config.yaml"


def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)


cfg = load_config()
engine = create_engine(cfg["database"]["url"], pool_pre_ping=True, future=True)
LOCAL_TZ = ZoneInfo(cfg.get("app", {}).get("timezone", "Europe/Rome"))


# ---------------------------------------------------------------------
# Generic formatting
# ---------------------------------------------------------------------

def format_watt(value):
    if value is None or pd.isna(value):
        return "n/d"
    return f"{value:,.0f} W".replace(",", ".")


def format_kwh(value):
    if value is None or pd.isna(value):
        return "n/d"
    return f"{value/1000:,.2f} kWh".replace(",", "X").replace(".", ",").replace("X", ".")


def format_voltage(value):
    if value is None or pd.isna(value):
        return "n/d"
    return f"{value:.1f} V".replace(".", ",")


def format_current(value):
    if value is None or pd.isna(value):
        return "n/d"
    return f"{value:.2f} A".replace(".", ",")


def format_percent(value):
    if value is None or pd.isna(value):
        return "n/d"
    return f"{value:.1f} %".replace(".", ",")


def format_frequency(value):
    if value is None or pd.isna(value):
        return "n/d"
    return f"{value:.1f} Hz".replace(".", ",")


def format_pf(value):
    if value is None or pd.isna(value):
        return "n/d"
    return f"{value:.2f}".replace(".", ",")


def format_rssi(value):
    if value is None or pd.isna(value):
        return "n/d"
    return f"{value} dBm"


# ---------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------

def setting_get(key, default=None, value_type="string"):
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT value,value_type FROM settings WHERE key=:key"),
                {"key": key},
            ).mappings().first()

        if not row:
            return default

        value = row["value"]
        detected_type = row["value_type"] or value_type

        if detected_type == "int":
            return int(value)
        if detected_type == "float":
            return float(value)
        if detected_type == "bool":
            return str(value).lower() in ("1", "true", "yes", "on")

        return value
    except Exception:
        return default


def setting_put(key, value, value_type="string", description=None):
    sql = text("""
        INSERT INTO settings(key,value,value_type,description,updated_at,updated_by)
        VALUES(:key,:value,:value_type,:description,now(),'dashboard')
        ON CONFLICT(key) DO UPDATE SET
            value=EXCLUDED.value,
            value_type=EXCLUDED.value_type,
            description=EXCLUDED.description,
            updated_at=now(),
            updated_by='dashboard'
    """)

    with engine.begin() as conn:
        conn.execute(
            sql,
            {
                "key": key,
                "value": str(value),
                "value_type": value_type,
                "description": description,
            },
        )


def valid_hex(value):
    if not isinstance(value, str):
        return False

    value = value.strip()

    if len(value) != 7:
        return False

    if not value.startswith("#"):
        return False

    try:
        int(value[1:], 16)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------

def gauge_config():
    default_cfg = {
        "export_max_w": 6000,
        "import_warning_w": 3000,
        "import_max_w": 6000,
        "height": 330,
        "title": "Scambio rete",
    }

    default_cfg.update(cfg.get("gauge", {}))

    return {
        "export_max_w": setting_get("gauge.export_max_w", default_cfg["export_max_w"], "float"),
        "import_warning_w": setting_get("gauge.import_warning_w", default_cfg["import_warning_w"], "float"),
        "import_max_w": setting_get("gauge.import_max_w", default_cfg["import_max_w"], "float"),
        "height": setting_get("gauge.height", default_cfg["height"], "int"),
        "title": setting_get("gauge.title", default_cfg["title"], "string"),
    }


def chart_config():
    default_cfg = {
        "hours": 24,
        "resample_seconds": 60,
        "colors": {
            "produzione": "#4C6FFF",
            "consumo": "#FF4B3E",
            "autoconsumo": "#00C896",
            "immissione": "#B36BFF",
            "prelievo": "#FF9F43",
        },
    }

    user_cfg = cfg.get("chart", {})

    if "colors" in user_cfg:
        default_cfg["colors"].update(user_cfg.get("colors", {}))

    for key, value in user_cfg.items():
        if key != "colors":
            default_cfg[key] = value

    colors = default_cfg["colors"]

    return {
        "hours": setting_get("chart.hours", default_cfg["hours"], "float"),
        "resample_seconds": setting_get("chart.resample_seconds", default_cfg["resample_seconds"], "int"),
        "colors": {
            "produzione": setting_get("chart.color.produzione", colors["produzione"], "string"),
            "consumo": setting_get("chart.color.consumo", colors["consumo"], "string"),
            "autoconsumo": setting_get("chart.color.autoconsumo", colors["autoconsumo"], "string"),
            "immissione": setting_get("chart.color.immissione", colors["immissione"], "string"),
            "prelievo": setting_get("chart.color.prelievo", colors["prelievo"], "string"),
        },
    }


def load_chart_settings():
    default = {
        "production_wh": {"label": "Produzione", "color": "#4C6FFF"},
        "consumption_wh": {"label": "Consumi", "color": "#FF4B3E"},
        "self_consumption_wh": {"label": "Autoconsumo", "color": "#00C896"},
        "feed_in_wh": {"label": "Immissioni", "color": "#B36BFF"},
        "grid_import_wh": {"label": "Prelievi", "color": "#FF9F43"},
    }

    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT metric,label,color_hex FROM chart_settings ORDER BY sort_order")
            ).mappings().all()

        if not rows:
            return default

        return {
            r["metric"]: {
                "label": r["label"],
                "color": r["color_hex"],
            }
            for r in rows
        }
    except Exception:
        return default


# ---------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------

def load_live_recent():
    live_cfg = chart_config()
    hours = float(live_cfg.get("hours", 24))
    start_dt_utc = datetime.now(timezone.utc) - timedelta(hours=hours)

    sql = text("""
        SELECT
            id,
            ts,
            prod_power_w,
            cons_power_w,
            prod_energy_wh,
            cons_energy_wh,
            prod_voltage_v,
            cons_voltage_v,
            prod_current_a,
            cons_current_a,
            prod_freq_hz,
            cons_freq_hz,
            prod_pf,
            cons_pf,
            wifi_rssi
        FROM measures_raw
        WHERE ts >= :start_ts
        ORDER BY ts
    """)

    df = pd.read_sql_query(sql, engine, params={"start_ts": start_dt_utc})

    if df.empty:
        return df

    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["ts_local"] = df["ts"].dt.tz_convert(LOCAL_TZ)

    df["autoconsumo_w"] = df[["prod_power_w", "cons_power_w"]].min(axis=1)
    df["immissione_w"] = (df["prod_power_w"] - df["cons_power_w"]).clip(lower=0)
    df["prelievo_w"] = (df["cons_power_w"] - df["prod_power_w"]).clip(lower=0)
    df["net_grid_w"] = df["cons_power_w"] - df["prod_power_w"]

    df["autoconsumo_pct"] = 0.0
    df.loc[df["prod_power_w"] > 0, "autoconsumo_pct"] = (
        df["autoconsumo_w"] / df["prod_power_w"] * 100
    )

    df["autonomia_pct"] = 0.0
    df.loc[df["cons_power_w"] > 0, "autonomia_pct"] = (
        df["autoconsumo_w"] / df["cons_power_w"] * 100
    )

    return df


def load_daily(days=3650):
    sql = text("""
        SELECT
            day,
            production_wh,
            consumption_wh,
            grid_import_wh,
            feed_in_wh,
            self_consumption_wh
        FROM energy_daily
        WHERE day >= current_date - (:days || ' days')::interval
          AND COALESCE(is_anomaly,false)=false
        ORDER BY day
    """)

    df = pd.read_sql_query(sql, engine, params={"days": days})

    if not df.empty:
        df["day"] = pd.to_datetime(df["day"])

    return df


# ---------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------

def make_card(title, value, subtitle, colour="primary"):
    return dbc.Card(
        dbc.CardBody(
            [
                html.H6(title, className="card-title text-muted"),
                html.H3(value, className=f"text-{colour}"),
                html.Div(subtitle, className="small text-muted"),
            ]
        ),
        className="shadow-sm h-100",
    )


def make_grid_gauge(latest):
    gc = gauge_config()

    export_max_w = float(gc.get("export_max_w", 6000))
    import_warning_w = float(gc.get("import_warning_w", 3000))
    import_max_w = float(gc.get("import_max_w", 6000))
    height = int(gc.get("height", 330))
    title = gc.get("title", "Scambio rete")

    min_value = -export_max_w
    max_value = import_max_w

    if latest is None:
        value = 0
    else:
        value = latest.get("net_grid_w", 0)
        if value is None or pd.isna(value):
            value = 0

    value = max(min(value, max_value), min_value)

    if value < 0:
        status_text = "Immissione"
        bar_colour = "#2ecc71"
    elif value < import_warning_w:
        status_text = "Prelievo moderato"
        bar_colour = "#f39c12"
    else:
        status_text = "Prelievo elevato"
        bar_colour = "#e74c3c"

    fig = go.Figure()

    fig.add_trace(
        go.Indicator(
            mode="gauge+number",
            value=value,
            number={
                "suffix": " W",
                "font": {
                    "size": 34,
                    "color": "#ffffff",
                },
            },
            title={
                "text": f"{title}<br><span style='font-size:0.75em;color:#cccccc'>{status_text}</span>",
                "font": {
                    "size": 18,
                    "color": "#ffffff",
                },
            },
            gauge={
                "shape": "angular",
                "axis": {
                    "range": [min_value, max_value],
                    "tickwidth": 1,
                    "tickcolor": "#dddddd",
                    "tickfont": {
                        "color": "#dddddd",
                        "size": 11,
                    },
                },
                "bar": {
                    "color": bar_colour,
                    "thickness": 0.22,
                },
                "bgcolor": "#202020",
                "borderwidth": 2,
                "bordercolor": "#999999",
                "steps": [
                    {
                        "range": [min_value, 0],
                        "color": "#2ecc71",
                    },
                    {
                        "range": [0, import_warning_w],
                        "color": "#f39c12",
                    },
                    {
                        "range": [import_warning_w, max_value],
                        "color": "#e74c3c",
                    },
                ],
                "threshold": {
                    "line": {
                        "color": "#ffffff",
                        "width": 4,
                    },
                    "thickness": 0.8,
                    "value": value,
                },
            },
        )
    )

    fig.add_annotation(
        x=0.14,
        y=0.03,
        text="Immissione",
        showarrow=False,
        font={
            "size": 12,
            "color": "#2ecc71",
        },
        xref="paper",
        yref="paper",
    )

    fig.add_annotation(
        x=0.86,
        y=0.03,
        text="Prelievo",
        showarrow=False,
        font={
            "size": 12,
            "color": "#e74c3c",
        },
        xref="paper",
        yref="paper",
    )

    fig.update_layout(
        template="plotly_dark",
        height=height,
        margin={
            "l": 20,
            "r": 20,
            "t": 60,
            "b": 20,
        },
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )

    return fig


def resample_for_chart(df):
    if df.empty:
        return df

    cc = chart_config()
    resample_seconds = int(cc.get("resample_seconds", 60))

    if resample_seconds <= 0:
        return df

    cols = [
        "prod_power_w",
        "cons_power_w",
        "autoconsumo_w",
        "immissione_w",
        "prelievo_w",
    ]

    df_plot = df[["ts_local"] + cols].copy()
    df_plot = df_plot.set_index("ts_local")
    df_plot = df_plot.resample(f"{resample_seconds}s").mean()
    df_plot = df_plot.dropna(how="all")
    df_plot = df_plot.reset_index()

    return df_plot


def make_power_chart(df):
    fig = go.Figure()

    if df.empty:
        fig.update_layout(
            title="Nessun dato disponibile",
            template="plotly_dark",
            height=440,
        )
        return fig

    df_plot = resample_for_chart(df)

    cc = chart_config()
    colors = cc.get("colors", {})

    traces = [
        ("prod_power_w", "Produzione", colors.get("produzione", "#4C6FFF")),
        ("cons_power_w", "Consumo", colors.get("consumo", "#FF4B3E")),
        ("autoconsumo_w", "Autoconsumo", colors.get("autoconsumo", "#00C896")),
        ("immissione_w", "Immissione", colors.get("immissione", "#B36BFF")),
        ("prelievo_w", "Prelievo", colors.get("prelievo", "#FF9F43")),
    ]

    for column, name, color in traces:
        fig.add_trace(
            go.Scatter(
                x=df_plot["ts_local"],
                y=df_plot[column],
                mode="lines",
                name=name,
                line=dict(
                    width=2,
                    color=color,
                ),
            )
        )

    hours = float(cc.get("hours", 24))
    resample_seconds = int(cc.get("resample_seconds", 60))

    end_time = datetime.now(LOCAL_TZ)
    start_time = end_time - timedelta(hours=hours)

    fig.update_layout(
        title=f"Flussi istantanei di potenza - ultime {hours:g} ore, media {resample_seconds}s",
        template="plotly_dark",
        height=440,
        margin=dict(l=40, r=20, t=70, b=40),
        xaxis=dict(title="Ora", range=[start_time, end_time]),
        yaxis_title="W",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
        ),
    )

    return fig




# ---------------------------------------------------------------------
# System page helpers
# ---------------------------------------------------------------------

SYSTEM_SERVICES = {
    "Collector": "energydash-collector.service",
    "API": "energydash-api.service",
    "Web Dashboard": "energydash-web.service",
    "Alert Worker": "energydash-alert.service",
    "Daily Update Timer": "energydash-daily-update.timer",
    "Monthly Report Timer": "energydash-report.timer",
}


def run_command(command, timeout=8):
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
        }


def service_status(service_name):
    active = run_command(["systemctl", "is-active", service_name], timeout=4)
    enabled = run_command(["systemctl", "is-enabled", service_name], timeout=4)

    active_text = active["stdout"] or "unknown"
    enabled_text = enabled["stdout"] or "unknown"

    if active_text == "active":
        colour = "success"
        label = "Running"
    elif active_text in ("activating", "reloading"):
        colour = "warning"
        label = active_text
    else:
        colour = "danger"
        label = active_text

    return {
        "service": service_name,
        "active": active_text,
        "enabled": enabled_text,
        "colour": colour,
        "label": label,
    }


def get_database_health():
    result = {
        "last_sample": None,
        "last_sample_age_seconds": None,
        "raw_rows": None,
        "daily_rows": None,
        "first_day": None,
        "last_day": None,
        "error": None,
    }

    try:
        with engine.begin() as conn:
            last = conn.execute(text("SELECT max(ts) FROM measures_raw")).scalar()
            raw_rows = conn.execute(text("SELECT count(*) FROM measures_raw")).scalar()
            daily = conn.execute(
                text("SELECT min(day), max(day), count(*) FROM energy_daily")
            ).first()

        result["last_sample"] = last
        result["raw_rows"] = raw_rows

        if daily:
            result["first_day"] = daily[0]
            result["last_day"] = daily[1]
            result["daily_rows"] = daily[2]

        if last:
            now_utc = datetime.now(timezone.utc)
            if last.tzinfo is None:
                age = datetime.utcnow() - last
            else:
                age = now_utc.astimezone(last.tzinfo) - last
            result["last_sample_age_seconds"] = int(age.total_seconds())

    except Exception as exc:
        result["error"] = str(exc)

    return result


def get_disk_health():
    paths = [
        "/",
        "/opt/energydash-pro",
        "/var/lib/energydash-pro",
        "/var/log/energydash-pro",
    ]

    rows = []

    for p in paths:
        try:
            if not os.path.exists(p):
                rows.append({
                    "path": p,
                    "total_gb": None,
                    "used_gb": None,
                    "free_gb": None,
                    "used_pct": None,
                    "error": "path not found",
                })
                continue

            usage = shutil.disk_usage(p)
            total = usage.total / 1024 / 1024 / 1024
            used = usage.used / 1024 / 1024 / 1024
            free = usage.free / 1024 / 1024 / 1024
            used_pct = used / total * 100 if total else 0

            rows.append({
                "path": p,
                "total_gb": total,
                "used_gb": used,
                "free_gb": free,
                "used_pct": used_pct,
                "error": None,
            })

        except Exception as exc:
            rows.append({
                "path": p,
                "total_gb": None,
                "used_gb": None,
                "free_gb": None,
                "used_pct": None,
                "error": str(exc),
            })

    return rows


def get_backup_health():
    backup_dir = "/var/lib/energydash-pro/backup"

    result = {
        "dir": backup_dir,
        "exists": os.path.isdir(backup_dir),
        "count": 0,
        "total_mb": 0,
        "latest_file": None,
        "latest_mtime": None,
        "error": None,
    }

    try:
        if not os.path.isdir(backup_dir):
            return result

        files = []
        total = 0

        for entry in os.scandir(backup_dir):
            if entry.is_file():
                stat = entry.stat()
                files.append((entry.path, stat.st_mtime, stat.st_size))
                total += stat.st_size

        result["count"] = len(files)
        result["total_mb"] = total / 1024 / 1024

        if files:
            latest = sorted(files, key=lambda x: x[1], reverse=True)[0]
            result["latest_file"] = os.path.basename(latest[0])
            result["latest_mtime"] = datetime.fromtimestamp(latest[1]).strftime("%d/%m/%Y %H:%M:%S")

    except Exception as exc:
        result["error"] = str(exc)

    return result


def get_system_info():
    load_avg = None

    try:
        load_avg = os.getloadavg()
    except Exception:
        load_avg = None

    uptime = run_command(["uptime", "-p"], timeout=4)

    py_version = platform.python_version()
    host = platform.node()
    system = f"{platform.system()} {platform.release()}"

    return {
        "hostname": host,
        "system": system,
        "python": py_version,
        "uptime": uptime["stdout"] if uptime["ok"] else "n/d",
        "load_avg": load_avg,
    }


def status_badge(text_value, colour):
    return dbc.Badge(text_value, color=colour, className="ms-2")


def service_table():
    rows = []

    for label, service in SYSTEM_SERVICES.items():
        status = service_status(service)
        rows.append(
            html.Tr(
                [
                    html.Td(label),
                    html.Td(service),
                    html.Td(status_badge(status["label"], status["colour"])),
                    html.Td(status["enabled"]),
                ]
            )
        )

    return dbc.Table(
        [
            html.Thead(
                html.Tr(
                    [
                        html.Th("Componente"),
                        html.Th("Servizio"),
                        html.Th("Stato"),
                        html.Th("Enabled"),
                    ]
                )
            ),
            html.Tbody(rows),
        ],
        bordered=True,
        striped=True,
        hover=True,
        responsive=True,
        className="mb-0",
    )


def database_health_cards():
    db = get_database_health()

    if db.get("error"):
        return dbc.Alert(f"Errore lettura database: {db['error']}", color="danger")

    age = db.get("last_sample_age_seconds")
    if age is None:
        age_text = "n/d"
        age_colour = "secondary"
    elif age < 60:
        age_text = f"{age}s"
        age_colour = "success"
    elif age < 300:
        age_text = f"{age // 60} min"
        age_colour = "warning"
    else:
        age_text = f"{age // 60} min"
        age_colour = "danger"

    last_sample = db.get("last_sample")
    last_sample_text = str(last_sample) if last_sample else "n/d"

    return dbc.Row(
        [
            dbc.Col(make_card("Ultimo campione", last_sample_text, "measures_raw", age_colour), md=3),
            dbc.Col(make_card("Età ultimo campione", age_text, "latenza acquisizione", age_colour), md=3),
            dbc.Col(make_card("Record raw", f"{db.get('raw_rows'):,}".replace(",", "."), "misure istantanee", "primary"), md=3),
            dbc.Col(make_card("Giorni storici", f"{db.get('daily_rows'):,}".replace(",", "."), "energy_daily", "info"), md=3),
        ],
        className="g-3",
    )


def disk_table():
    rows = []

    for item in get_disk_health():
        if item["error"]:
            rows.append(
                html.Tr(
                    [
                        html.Td(item["path"]),
                        html.Td("n/d"),
                        html.Td("n/d"),
                        html.Td("n/d"),
                        html.Td(dbc.Badge(item["error"], color="danger")),
                    ]
                )
            )
            continue

        pct_value = item["used_pct"]
        if pct_value < 70:
            colour = "success"
        elif pct_value < 90:
            colour = "warning"
        else:
            colour = "danger"

        rows.append(
            html.Tr(
                [
                    html.Td(item["path"]),
                    html.Td(f"{item['total_gb']:.1f} GB"),
                    html.Td(f"{item['used_gb']:.1f} GB"),
                    html.Td(f"{item['free_gb']:.1f} GB"),
                    html.Td(dbc.Badge(f"{pct_value:.1f}%", color=colour)),
                ]
            )
        )

    return dbc.Table(
        [
            html.Thead(
                html.Tr(
                    [
                        html.Th("Percorso"),
                        html.Th("Totale"),
                        html.Th("Usato"),
                        html.Th("Libero"),
                        html.Th("Utilizzo"),
                    ]
                )
            ),
            html.Tbody(rows),
        ],
        bordered=True,
        striped=True,
        hover=True,
        responsive=True,
        className="mb-0",
    )


def backup_cards():
    backup = get_backup_health()

    if backup["error"]:
        return dbc.Alert(f"Errore lettura backup: {backup['error']}", color="danger")

    exists_colour = "success" if backup["exists"] else "danger"

    return dbc.Row(
        [
            dbc.Col(make_card("Directory backup", "Presente" if backup["exists"] else "Mancante", backup["dir"], exists_colour), md=3),
            dbc.Col(make_card("Backup presenti", str(backup["count"]), "file in archivio", "primary"), md=3),
            dbc.Col(make_card("Dimensione totale", f"{backup['total_mb']:.1f} MB", "spazio occupato", "info"), md=3),
            dbc.Col(make_card("Ultimo backup", backup["latest_mtime"] or "n/d", backup["latest_file"] or "nessun file", "secondary"), md=3),
        ],
        className="g-3",
    )


def system_info_table():
    info = get_system_info()

    load_text = "n/d"
    if info["load_avg"]:
        load_text = ", ".join(f"{x:.2f}" for x in info["load_avg"])

    return dbc.Table(
        [
            html.Tbody(
                [
                    html.Tr([html.Th("Hostname"), html.Td(info["hostname"])]),
                    html.Tr([html.Th("Sistema"), html.Td(info["system"])]),
                    html.Tr([html.Th("Python"), html.Td(info["python"])]),
                    html.Tr([html.Th("Uptime"), html.Td(info["uptime"])]),
                    html.Tr([html.Th("Load average"), html.Td(load_text)]),
                ]
            )
        ],
        bordered=True,
        striped=True,
        hover=True,
        responsive=True,
        className="mb-0",
    )


def get_service_logs(service_name, lines=80):
    allowed = set(SYSTEM_SERVICES.values())

    if service_name not in allowed:
        return "Servizio non valido."

    result = run_command(
        ["journalctl", "-u", service_name, "-n", str(lines), "--no-pager"],
        timeout=8,
    )

    if result["ok"]:
        return result["stdout"] or "Nessun log disponibile."

    msg = result["stderr"] or result["stdout"] or "Errore sconosciuto."
    return f"Impossibile leggere i log di {service_name}.\n\n{msg}"


def run_backup_script():
    script = "/opt/energydash-pro/scripts/backup.sh"

    if not os.path.exists(script):
        return False, f"Script backup non trovato: {script}"

    result = run_command(["bash", script], timeout=120)

    if result["ok"]:
        return True, result["stdout"] or "Backup completato."

    return False, result["stderr"] or result["stdout"] or "Backup fallito."


# ---------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------
SIDEBAR_MODES = ["full", "compact", "hidden"]


def load_sidebar_mode():
    try:
        return setting_get("ui.sidebar_mode", "full", "string")
    except Exception:
        return "full"


def save_sidebar_mode(mode):
    try:
        setting_put(
            "ui.sidebar_mode",
            mode,
            "string",
            "Sidebar mode"
        )
    except Exception:
        pass

    return mode
def sidebar():
    return dbc.Col(
        [
            dbc.ButtonGroup(
                [
                    dbc.Button(
                        "◀",
                        id="btn-sidebar-next",
                        size="sm",
                        color="secondary",
                        outline=True,
                    ),
                    dbc.Button(
                        "✕",
                        id="btn-sidebar-hide",
                        size="sm",
                        color="secondary",
                        outline=True,
                    ),
                ],
                className="mb-2 w-100",
            ),

            html.Div(
                id="sidebar-full",
                children=dbc.Nav(
                    [
                        dbc.NavLink("Home", href="/", active="exact"),
                        dbc.NavLink("Live", href="/live", active="exact"),
                        dbc.NavLink("Storico", href="/storico", active="exact"),
                        dbc.NavLink("Report", href="/report", active="exact"),
                        dbc.NavLink("Configurazione", href="/config", active="exact"),
                        dbc.NavLink("Sistema", href="/sistema", active="exact"),
                    ],
                    vertical=True,
                    pills=True,
                ),
            ),

            html.Div(
                id="sidebar-compact",
                children=dbc.Nav(
                    [
                        dbc.NavLink("H", href="/", active="exact"),
                        dbc.NavLink("L", href="/live", active="exact"),
                        dbc.NavLink("ST", href="/storico", active="exact"),
                        dbc.NavLink("R", href="/report", active="exact"),
                        dbc.NavLink("⚙️", href="/config", active="exact"),
                        dbc.NavLink("SYS", href="/sistema", active="exact"),
                    ],
                    vertical=True,
                    pills=True,
                ),
                style={"display": "none"},
            ),
        ],
        id="sidebar-column",
        width=2,
        className="bg-dark p-3 min-vh-100",
    )


def nav_buttons():
    return dbc.ButtonGroup(
        [
            dbc.Button("Home", href="/", color="secondary", outline=True, size="sm"),
            dbc.Button("Indietro", id="btn-history-back", color="secondary", outline=True, size="sm"),
        ],
        className="mb-3",
    )


app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
    title="EnergyDash Pro",
)

server = app.server

app.layout = dbc.Container(
    fluid=True,
    children=[
        dcc.Location(id="url"),
        dbc.Button(
            "☰",
            id="floating-sidebar-button",
            color="primary",
            size="sm",
            style={
                "display": "none",
                "position": "fixed",
                "top": "10px",
                "left": "10px",
                "zIndex": 2000,
            },
        ),
        dcc.Interval(id="refresh", interval=5000, n_intervals=0),
        dcc.Store(id="history-state", data={"level": "year", "year": None, "month": None}),
        dcc.Store(
            id="sidebar-state",
            data=load_sidebar_mode(),
        ),
        dbc.Row(
            [
                sidebar(),
                dbc.Col(
                    html.Div(id="page"),
                    id="content-column",
                    width=10,
                    className="p-3",
                ),
            ]
        ),
    ],
)


def home_layout():
    return html.Div(
        [
            html.H2(cfg.get("app", {}).get("name", "EnergyDash Pro")),
            html.Div("Dashboard energetica self-hosted per Shelly Pro EM", className="text-muted mb-3"),
            html.Div(id="home-cards"),
            dcc.Graph(id="home-chart"),
        ]
    )


def live_layout():
    return html.Div(
        [
            html.H2("Live monitoring"),
            html.Div("Vista live con gauge rete, KPI e dettaglio ultimo campione.", className="text-muted mb-3"),

            dbc.Row(
                [
                    dbc.Col(html.Div(id="card-produzione"), md=2),
                    dbc.Col(html.Div(id="card-consumo"), md=2),
                    dbc.Col(html.Div(id="card-autoconsumo"), md=2),
                    dbc.Col(html.Div(id="card-immissione"), md=2),
                    dbc.Col(html.Div(id="card-prelievo"), md=2),
                    dbc.Col(html.Div(id="card-wifi"), md=2),
                ],
                className="g-3 mb-3",
            ),

            dbc.Row(
                [
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    dcc.Graph(
                                        id="grid-gauge",
                                        config={
                                            "displayModeBar": False,
                                        },
                                    )
                                ]
                            ),
                            className="shadow-sm h-100",
                        ),
                        md=4,
                    ),
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    dcc.Graph(
                                        id="power-chart",
                                        config={
                                            "displayModeBar": True,
                                            "scrollZoom": True,
                                        },
                                    )
                                ]
                            ),
                            className="shadow-sm h-100",
                        ),
                        md=8,
                    ),
                ],
                className="g-3 mb-3",
            ),

            dbc.Row(
                [
                    dbc.Col(
                        dbc.Card(
                            dbc.CardBody(
                                [
                                    html.H5("Dettagli ultimo campione"),
                                    html.Div(id="details-table"),
                                ]
                            ),
                            className="shadow-sm",
                        ),
                        md=12,
                    )
                ]
            ),
        ]
    )


def storico_layout():
    return html.Div(
        [
            html.H2("Storico energia"),
            nav_buttons(),
            dbc.Alert(
                "Clicca su una barra per entrare nel dettaglio: anno, mese, giorno.",
                color="info",
                className="py-2",
            ),
            dcc.Dropdown(
                id="history-fields",
                multi=True,
                value=[
                    "production_wh",
                    "consumption_wh",
                    "grid_import_wh",
                    "feed_in_wh",
                    "self_consumption_wh",
                ],
                options=[],
                style={"color": "#000"},
            ),
            html.Div(id="history-title", className="mt-3 mb-2"),
            dcc.Graph(id="history-chart"),
            html.Div(id="history-summary", className="mt-3"),
        ]
    )


def config_input(label, input_id, value, input_type="text", min_value=None, max_value=None, step=None, help_text=None):
    return dbc.Col(
        [
            dbc.Label(label),
            dbc.Input(
                id=input_id,
                type=input_type,
                value=value,
                min=min_value,
                max=max_value,
                step=step,
            ),
            html.Div(help_text or "", className="small text-muted mt-1"),
        ],
        md=4,
        className="mb-3",
    )


def config_layout():
    cc = chart_config()
    gc = gauge_config()
    colors = cc.get("colors", {})

    return html.Div(
        [
            html.H2("Configurazione"),
            html.Div(
                "Modifica i parametri principali della dashboard live. I valori vengono salvati nel database nella tabella settings.",
                className="text-muted mb-3",
            ),

            dbc.Alert(
                "Nota: queste impostazioni controllano la dashboard web. Le configurazioni tecniche di Shelly, database e servizi restano nel file config.yaml.",
                color="info",
                className="py-2",
            ),

            html.Div(id="config-save-alert"),

            dbc.Card(
                dbc.CardBody(
                    [
                        html.H4("Grafico live", className="mb-3"),
                        dbc.Row(
                            [
                                config_input(
                                    "Ore visualizzate",
                                    "cfg-chart-hours",
                                    cc.get("hours", 24),
                                    "number",
                                    min_value=0.25,
                                    max_value=168,
                                    step=0.25,
                                    help_text="Esempio: 24 per ultime 24 ore, 6 per ultime 6 ore.",
                                ),
                                config_input(
                                    "Media/resampling in secondi",
                                    "cfg-chart-resample",
                                    cc.get("resample_seconds", 60),
                                    "number",
                                    min_value=0,
                                    max_value=3600,
                                    step=5,
                                    help_text="0 disabilita il resampling. 60 significa media a 1 minuto.",
                                ),
                            ]
                        ),
                        html.H5("Colori linee live", className="mt-3"),
                        dbc.Row(
                            [
                                config_input("Produzione", "cfg-color-produzione", colors.get("produzione", "#4C6FFF"), "text", help_text="Codice HEX, esempio #4C6FFF."),
                                config_input("Consumo", "cfg-color-consumo", colors.get("consumo", "#FF4B3E"), "text", help_text="Codice HEX."),
                                config_input("Autoconsumo", "cfg-color-autoconsumo", colors.get("autoconsumo", "#00C896"), "text", help_text="Codice HEX."),
                                config_input("Immissione", "cfg-color-immissione", colors.get("immissione", "#B36BFF"), "text", help_text="Codice HEX."),
                                config_input("Prelievo", "cfg-color-prelievo", colors.get("prelievo", "#FF9F43"), "text", help_text="Codice HEX."),
                            ]
                        ),
                    ]
                ),
                className="shadow-sm mb-3",
            ),

            dbc.Card(
                dbc.CardBody(
                    [
                        html.H4("Gauge scambio rete", className="mb-3"),
                        dbc.Row(
                            [
                                config_input(
                                    "Titolo gauge",
                                    "cfg-gauge-title",
                                    gc.get("title", "Scambio rete"),
                                    "text",
                                    help_text="Titolo visualizzato sopra il gauge.",
                                ),
                                config_input(
                                    "Massimo immissione W",
                                    "cfg-gauge-export-max",
                                    gc.get("export_max_w", 6000),
                                    "number",
                                    min_value=1,
                                    max_value=50000,
                                    step=100,
                                    help_text="Scala negativa del gauge.",
                                ),
                                config_input(
                                    "Soglia prelievo elevato W",
                                    "cfg-gauge-import-warning",
                                    gc.get("import_warning_w", 3000),
                                    "number",
                                    min_value=1,
                                    max_value=50000,
                                    step=100,
                                    help_text="Da questa soglia il gauge diventa rosso.",
                                ),
                                config_input(
                                    "Massimo prelievo W",
                                    "cfg-gauge-import-max",
                                    gc.get("import_max_w", 6000),
                                    "number",
                                    min_value=1,
                                    max_value=50000,
                                    step=100,
                                    help_text="Scala positiva del gauge.",
                                ),
                                config_input(
                                    "Altezza gauge px",
                                    "cfg-gauge-height",
                                    gc.get("height", 330),
                                    "number",
                                    min_value=200,
                                    max_value=800,
                                    step=10,
                                    help_text="Altezza del grafico gauge.",
                                ),
                            ]
                        ),
                    ]
                ),
                className="shadow-sm mb-3",
            ),

            dbc.Button("Salva configurazione", id="btn-save-config", color="success", className="me-2"),
            dbc.Button("Vai alla live", href="/live", color="primary", outline=True),
        ]
    )




def system_layout():
    service_options = [
        {"label": label, "value": service}
        for label, service in SYSTEM_SERVICES.items()
    ]

    default_service = "energydash-web.service"

    return html.Div(
        [
            html.H2("Sistema"),
            html.Div(
                "Health check, servizi, backup, log e diagnostica.",
                className="text-muted mb-3",
            ),

            dbc.ButtonGroup(
                [
                    dbc.Button("Home", href="/", color="secondary", outline=True, size="sm"),
                    dbc.Button("Aggiorna", id="btn-system-refresh", color="primary", outline=True, size="sm"),
                ],
                className="mb-3",
            ),

            html.Div(id="system-refresh-alert"),

            dbc.Card(
                dbc.CardBody(
                    [
                        html.H4("Stato servizi", className="mb-3"),
                        html.Div(id="system-services-table"),
                    ]
                ),
                className="shadow-sm mb-3",
            ),

            dbc.Card(
                dbc.CardBody(
                    [
                        html.H4("Health database", className="mb-3"),
                        html.Div(id="system-db-health"),
                    ]
                ),
                className="shadow-sm mb-3",
            ),

            dbc.Card(
                dbc.CardBody(
                    [
                        html.H4("Storage", className="mb-3"),
                        html.Div(id="system-disk-table"),
                    ]
                ),
                className="shadow-sm mb-3",
            ),

            dbc.Card(
                dbc.CardBody(
                    [
                        html.H4("Backup", className="mb-3"),
                        html.Div(id="system-backup-cards"),
                        html.Div(className="mt-3"),
                        dbc.Button("Crea backup adesso", id="btn-run-backup", color="success", outline=False),
                        html.Div(id="system-backup-result", className="mt-3"),
                    ]
                ),
                className="shadow-sm mb-3",
            ),

            dbc.Card(
                dbc.CardBody(
                    [
                        html.H4("Informazioni sistema", className="mb-3"),
                        html.Div(id="system-info-table"),
                    ]
                ),
                className="shadow-sm mb-3",
            ),

            dbc.Card(
                dbc.CardBody(
                    [
                        html.H4("Log viewer", className="mb-3"),
                        dbc.Row(
                            [
                                dbc.Col(
                                    dcc.Dropdown(
                                        id="system-log-service",
                                        options=service_options,
                                        value=default_service,
                                        clearable=False,
                                        style={"color": "#000"},
                                    ),
                                    md=6,
                                ),
                                dbc.Col(
                                    dbc.Button("Ricarica log", id="btn-system-logs", color="secondary", outline=True),
                                    md=3,
                                ),
                            ],
                            className="mb-3",
                        ),
                        html.Pre(
                            id="system-log-output",
                            style={
                                "backgroundColor": "#111",
                                "color": "#ddd",
                                "border": "1px solid #444",
                                "padding": "12px",
                                "maxHeight": "520px",
                                "overflowY": "auto",
                                "whiteSpace": "pre-wrap",
                                "fontSize": "0.85rem",
                            },
                        ),
                    ]
                ),
                className="shadow-sm mb-3",
            ),
        ]
    )


def simple_page(title, text):
    return html.Div(
        [
            html.H2(title),
            dbc.ButtonGroup(
                [
                    dbc.Button("Home", href="/", color="secondary", outline=True, size="sm"),
                ],
                className="mb-3",
            ),
            dbc.Alert(text, color="info"),
        ]
    )


# ---------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------

@app.callback(Output("page", "children"), Input("url", "pathname"))
def route(path):
    if path == "/live":
        return live_layout()
    if path == "/storico":
        return storico_layout()
    if path == "/report":
        return simple_page("Report", "Qui verranno elencati e generati i report PDF/Excel.")
    if path == "/config":
        return config_layout()
    if path == "/sistema":
        return system_layout()
    return home_layout()


# ---------------------------------------------------------------------
# Home and live callbacks
# ---------------------------------------------------------------------

@app.callback(
    Output("home-cards", "children"),
    Output("home-chart", "figure"),
    Input("refresh", "n_intervals"),
)
def update_home(_):
    df = load_live_recent()

    if df.empty:
        return dbc.Alert("Nessun dato disponibile", color="warning"), go.Figure()

    latest = df.iloc[-1]
    last_update = latest["ts_local"].strftime("%d/%m/%Y %H:%M:%S")

    cards = dbc.Row(
        [
            dbc.Col(make_card("Produzione", format_watt(latest["prod_power_w"]), last_update, "success"), md=2),
            dbc.Col(make_card("Consumo", format_watt(latest["cons_power_w"]), "Potenza istantanea", "danger"), md=2),
            dbc.Col(make_card("Autoconsumo", format_watt(latest["autoconsumo_w"]), format_percent(latest["autoconsumo_pct"]), "info"), md=2),
            dbc.Col(make_card("Autonomia", format_percent(latest["autonomia_pct"]), "Quota consumi coperta da FV", "primary"), md=2),
            dbc.Col(make_card("Immissione", format_watt(latest["immissione_w"]), "Verso rete", "warning"), md=2),
            dbc.Col(make_card("Prelievo", format_watt(latest["prelievo_w"]), "Da rete", "secondary"), md=2),
        ],
        className="g-3 mb-3",
    )

    fig = make_power_chart(df)
    return cards, fig


@app.callback(
    Output("card-produzione", "children"),
    Output("card-consumo", "children"),
    Output("card-autoconsumo", "children"),
    Output("card-immissione", "children"),
    Output("card-prelievo", "children"),
    Output("card-wifi", "children"),
    Output("grid-gauge", "figure"),
    Output("power-chart", "figure"),
    Output("details-table", "children"),
    Input("refresh", "n_intervals"),
)
def update_live_dashboard(_):
    df = load_live_recent()

    if df.empty:
        empty_card = make_card(
            "Dato non disponibile",
            "n/d",
            "Database vuoto",
            "secondary",
        )

        gauge = make_grid_gauge(None)
        fig = make_power_chart(df)

        return (
            empty_card,
            empty_card,
            empty_card,
            empty_card,
            empty_card,
            empty_card,
            gauge,
            fig,
            html.Div("Nessun dato presente nel database."),
        )

    latest = df.iloc[-1]

    prod_power = latest["prod_power_w"]
    cons_power = latest["cons_power_w"]
    autoconsumo = latest["autoconsumo_w"]
    immissione = latest["immissione_w"]
    prelievo = latest["prelievo_w"]
    autoconsumo_pct = latest["autoconsumo_pct"]
    autonomia_pct = latest["autonomia_pct"]
    wifi_rssi = latest["wifi_rssi"]
    net_grid_w = latest["net_grid_w"]

    last_update = latest["ts_local"].strftime("%d/%m/%Y %H:%M:%S")

    card_produzione = make_card(
        "Produzione",
        format_watt(prod_power),
        f"Ultimo aggiornamento: {last_update}",
        "success",
    )

    card_consumo = make_card(
        "Consumo",
        format_watt(cons_power),
        "Potenza attiva istantanea",
        "danger",
    )

    card_autoconsumo = make_card(
        "Autoconsumo",
        format_watt(autoconsumo),
        f"{format_percent(autoconsumo_pct)} della produzione",
        "info",
    )

    card_immissione = make_card(
        "Immissione",
        format_watt(immissione),
        "Energia istantanea verso rete",
        "warning",
    )

    card_prelievo = make_card(
        "Prelievo",
        format_watt(prelievo),
        "Energia istantanea da rete",
        "primary",
    )

    card_wifi = make_card(
        "Wi-Fi Shelly",
        format_rssi(wifi_rssi),
        "Segnale RSSI",
        "secondary",
    )

    gauge = make_grid_gauge(latest)
    fig = make_power_chart(df)

    if net_grid_w < 0:
        scambio_text = f"Immissione {format_watt(abs(net_grid_w))}"
    elif net_grid_w > 0:
        scambio_text = f"Prelievo {format_watt(net_grid_w)}"
    else:
        scambio_text = "Equilibrio 0 W"

    details = dbc.Table(
        [
            html.Tbody(
                [
                    html.Tr([html.Th("Ultimo aggiornamento"), html.Td(last_update)]),
                    html.Tr([html.Th("Produzione"), html.Td(format_watt(prod_power))]),
                    html.Tr([html.Th("Consumo"), html.Td(format_watt(cons_power))]),
                    html.Tr([html.Th("Scambio rete"), html.Td(scambio_text)]),
                    html.Tr([html.Th("Autoconsumo"), html.Td(format_watt(autoconsumo))]),
                    html.Tr([html.Th("Immissione"), html.Td(format_watt(immissione))]),
                    html.Tr([html.Th("Prelievo"), html.Td(format_watt(prelievo))]),
                    html.Tr([html.Th("Autoconsumo produzione"), html.Td(format_percent(autoconsumo_pct))]),
                    html.Tr([html.Th("Autonomia consumi"), html.Td(format_percent(autonomia_pct))]),
                    html.Tr([html.Th("Tensione casa"), html.Td(format_voltage(latest["cons_voltage_v"]))]),
                    html.Tr([html.Th("Corrente casa"), html.Td(format_current(latest["cons_current_a"]))]),
                    html.Tr([html.Th("Cosfi casa"), html.Td(format_pf(latest["cons_pf"]))]),
                    html.Tr([html.Th("Frequenza casa"), html.Td(format_frequency(latest["cons_freq_hz"]))]),
                    html.Tr([html.Th("Cosfi inverter"), html.Td(format_pf(latest["prod_pf"]))]),
                    html.Tr([html.Th("Tensione inverter"), html.Td(format_voltage(latest["prod_voltage_v"]))]),
                    html.Tr([html.Th("Corrente inverter"), html.Td(format_current(latest["prod_current_a"]))]),
                    html.Tr([html.Th("RSSI Shelly"), html.Td(format_rssi(wifi_rssi))]),
                    html.Tr([html.Th("Campioni visualizzati"), html.Td(str(len(df)))]),
                ]
            )
        ],
        bordered=True,
        striped=True,
        hover=True,
        responsive=True,
        className="mb-0",
    )

    return (
        card_produzione,
        card_consumo,
        card_autoconsumo,
        card_immissione,
        card_prelievo,
        card_wifi,
        gauge,
        fig,
        details,
    )


# ---------------------------------------------------------------------
# Config callback
# ---------------------------------------------------------------------

@app.callback(
    Output("config-save-alert", "children"),
    Input("btn-save-config", "n_clicks"),
    State("cfg-chart-hours", "value"),
    State("cfg-chart-resample", "value"),
    State("cfg-color-produzione", "value"),
    State("cfg-color-consumo", "value"),
    State("cfg-color-autoconsumo", "value"),
    State("cfg-color-immissione", "value"),
    State("cfg-color-prelievo", "value"),
    State("cfg-gauge-title", "value"),
    State("cfg-gauge-export-max", "value"),
    State("cfg-gauge-import-warning", "value"),
    State("cfg-gauge-import-max", "value"),
    State("cfg-gauge-height", "value"),
    prevent_initial_call=True,
)
def save_config(
    n_clicks,
    chart_hours,
    chart_resample,
    color_produzione,
    color_consumo,
    color_autoconsumo,
    color_immissione,
    color_prelievo,
    gauge_title,
    gauge_export_max,
    gauge_import_warning,
    gauge_import_max,
    gauge_height,
):
    errors = []

    color_values = {
        "chart.color.produzione": color_produzione,
        "chart.color.consumo": color_consumo,
        "chart.color.autoconsumo": color_autoconsumo,
        "chart.color.immissione": color_immissione,
        "chart.color.prelievo": color_prelievo,
    }

    for key, value in color_values.items():
        if not valid_hex(value):
            errors.append(f"{key}: colore non valido '{value}'. Usa formato #RRGGBB.")

    try:
        chart_hours = float(chart_hours)
        if chart_hours <= 0:
            errors.append("Le ore visualizzate devono essere maggiori di zero.")
    except Exception:
        errors.append("Ore visualizzate non valide.")

    try:
        chart_resample = int(chart_resample)
        if chart_resample < 0:
            errors.append("Il resampling non può essere negativo.")
    except Exception:
        errors.append("Resampling non valido.")

    numeric_checks = [
        ("gauge.export_max_w", gauge_export_max),
        ("gauge.import_warning_w", gauge_import_warning),
        ("gauge.import_max_w", gauge_import_max),
        ("gauge.height", gauge_height),
    ]

    parsed_numbers = {}

    for key, value in numeric_checks:
        try:
            parsed_numbers[key] = float(value)
            if parsed_numbers[key] <= 0:
                errors.append(f"{key}: deve essere maggiore di zero.")
        except Exception:
            errors.append(f"{key}: valore numerico non valido.")

    if errors:
        return dbc.Alert(
            [
                html.H5("Configurazione non salvata"),
                html.Ul([html.Li(e) for e in errors]),
            ],
            color="danger",
        )

    try:
        setting_put("chart.hours", chart_hours, "float", "Ore visualizzate nel grafico live")
        setting_put("chart.resample_seconds", chart_resample, "int", "Secondi di media per il grafico live")

        for key, value in color_values.items():
            setting_put(key, value.strip(), "string", "Colore HEX dashboard live")

        setting_put("gauge.title", gauge_title or "Scambio rete", "string", "Titolo gauge scambio rete")
        setting_put("gauge.export_max_w", parsed_numbers["gauge.export_max_w"], "float", "Massimo immissione gauge")
        setting_put("gauge.import_warning_w", parsed_numbers["gauge.import_warning_w"], "float", "Soglia prelievo elevato gauge")
        setting_put("gauge.import_max_w", parsed_numbers["gauge.import_max_w"], "float", "Massimo prelievo gauge")
        setting_put("gauge.height", int(parsed_numbers["gauge.height"]), "int", "Altezza gauge in pixel")

        return dbc.Alert(
            "Configurazione salvata correttamente. Le modifiche si applicano ai prossimi refresh della dashboard live.",
            color="success",
        )
    except Exception as exc:
        return dbc.Alert(
            f"Errore durante il salvataggio: {exc}",
            color="danger",
        )


# ---------------------------------------------------------------------
# History callbacks
# ---------------------------------------------------------------------

@app.callback(
    Output("history-state", "data"),
    Input("history-chart", "clickData"),
    Input("btn-history-back", "n_clicks"),
    State("history-state", "data"),
    prevent_initial_call=True,
)
def update_history_state(click_data, back_clicks, state):
    state = state or {"level": "year", "year": None, "month": None}
    trigger = ctx.triggered_id

    if trigger == "btn-history-back":
        if state.get("level") == "day":
            return {"level": "month", "year": state.get("year"), "month": None}
        if state.get("level") == "month":
            return {"level": "year", "year": None, "month": None}
        return {"level": "year", "year": None, "month": None}

    if trigger == "history-chart" and click_data:
        x_value = click_data["points"][0]["x"]

        if state.get("level") == "year":
            return {"level": "month", "year": str(x_value), "month": None}

        if state.get("level") == "month":
            return {"level": "day", "year": state.get("year"), "month": str(x_value)}

    return state


@app.callback(
    Output("history-fields", "options"),
    Output("history-chart", "figure"),
    Output("history-title", "children"),
    Output("history-summary", "children"),
    Input("refresh", "n_intervals"),
    Input("history-fields", "value"),
    Input("history-state", "data"),
)
def update_history(_, fields, state):
    settings = load_chart_settings()
    options = [{"label": v["label"], "value": k} for k, v in settings.items()]
    fields = fields or [o["value"] for o in options]

    df = load_daily(3650)
    fig = go.Figure()

    if df.empty:
        fig.update_layout(template="plotly_dark", title="Nessun dato storico")
        return options, fig, html.H5("Nessun dato storico"), ""

    state = state or {"level": "year", "year": None, "month": None}
    level = state.get("level", "year")

    df["year"] = df["day"].dt.year.astype(str)
    df["month"] = df["day"].dt.strftime("%Y-%m")
    df["day_label"] = df["day"].dt.strftime("%Y-%m-%d")

    if level == "year":
        grouped = df.groupby("year", as_index=False)[fields].sum()
        x_col = "year"
        title_text = "Vista per anno"

    elif level == "month":
        year = state.get("year")
        filtered = df[df["year"] == str(year)]
        grouped = filtered.groupby("month", as_index=False)[fields].sum()
        x_col = "month"
        title_text = f"Vista mesi anno {year}"

    elif level == "day":
        month = state.get("month")
        filtered = df[df["month"] == str(month)]
        grouped = filtered.groupby("day_label", as_index=False)[fields].sum()
        x_col = "day_label"
        title_text = f"Vista giorni mese {month}"

    else:
        grouped = df.groupby("year", as_index=False)[fields].sum()
        x_col = "year"
        title_text = "Vista per anno"

    for f in fields:
        info = settings.get(f, {"label": f, "color": None})
        fig.add_trace(
            go.Bar(
                x=grouped[x_col],
                y=grouped[f] / 1000,
                name=info["label"],
                marker_color=info["color"],
            )
        )

    fig.update_layout(
        template="plotly_dark",
        barmode="group",
        height=560,
        title=title_text,
        yaxis_title="kWh",
        clickmode="event+select",
    )

    totals = grouped[fields].sum() if not grouped.empty else {}
    production = totals.get("production_wh", 0)
    consumption = totals.get("consumption_wh", 0)
    self_consumption = totals.get("self_consumption_wh", 0)

    self_consumption_pct = (self_consumption / production * 100) if production else None
    self_sufficiency_pct = (self_consumption / consumption * 100) if consumption else None

    summary = dbc.Row(
        [
            dbc.Col(make_card("Produzione", format_kwh(production), "Totale periodo", "success"), md=2),
            dbc.Col(make_card("Consumi", format_kwh(consumption), "Totale periodo", "danger"), md=2),
            dbc.Col(make_card("Autoconsumo", format_kwh(self_consumption), format_percent(self_consumption_pct), "info"), md=2),
            dbc.Col(make_card("Autosufficienza", format_percent(self_sufficiency_pct), "Consumi coperti da FV", "primary"), md=2),
            dbc.Col(make_card("Prelievi", format_kwh(totals.get("grid_import_wh", 0)), "Da rete", "secondary"), md=2),
            dbc.Col(make_card("Immissioni", format_kwh(totals.get("feed_in_wh", 0)), "Verso rete", "warning"), md=2),
        ],
        className="g-3",
    )

    return options, fig, html.H5(title_text), summary




# ---------------------------------------------------------------------
# System callbacks
# ---------------------------------------------------------------------

@app.callback(
    Output("system-services-table", "children"),
    Output("system-db-health", "children"),
    Output("system-disk-table", "children"),
    Output("system-backup-cards", "children"),
    Output("system-info-table", "children"),
    Output("system-refresh-alert", "children"),
    Input("refresh", "n_intervals"),
    Input("btn-system-refresh", "n_clicks"),
)
def update_system_panel(_interval, _clicks):
    return (
        service_table(),
        database_health_cards(),
        disk_table(),
        backup_cards(),
        system_info_table(),
        dbc.Alert(
            f"Aggiornato: {datetime.now(LOCAL_TZ).strftime('%d/%m/%Y %H:%M:%S')}",
            color="secondary",
            className="py-2",
        ),
    )


@app.callback(
    Output("system-log-output", "children"),
    Input("system-log-service", "value"),
    Input("btn-system-logs", "n_clicks"),
    Input("refresh", "n_intervals"),
)
def update_system_logs(service_name, _clicks, _interval):
    if not service_name:
        service_name = "energydash-web.service"

    return get_service_logs(service_name, lines=80)


@app.callback(
    Output("system-backup-result", "children"),
    Input("btn-run-backup", "n_clicks"),
    prevent_initial_call=True,
)
def run_backup_from_ui(n_clicks):
    ok, message = run_backup_script()

    if ok:
        return dbc.Alert(
            [
                html.H5("Backup completato"),
                html.Pre(message, className="mb-0"),
            ],
            color="success",
        )

    return dbc.Alert(
        [
            html.H5("Backup non completato"),
            html.Div("La dashboard gira con utente energydash: se mancano permessi, esegui il backup da shell oppure configuriamo sudo mirato."),
            html.Pre(message, className="mb-0 mt-2"),
        ],
        color="danger",
    )

@app.callback(
    Output("sidebar-state", "data"),
    Input("btn-sidebar-next", "n_clicks"),
    Input("btn-sidebar-hide", "n_clicks"),
    Input("floating-sidebar-button", "n_clicks"),
    State("sidebar-state", "data"),
    prevent_initial_call=True,
)
def update_sidebar_state(_, __, ___, current):

    current = current or "full"

    trigger = ctx.triggered_id

    if trigger == "btn-sidebar-hide":
        return save_sidebar_mode("hidden")

    if trigger == "floating-sidebar-button":
        return save_sidebar_mode("full")

    if current == "full":
        return save_sidebar_mode("compact")

    if current == "compact":
        return save_sidebar_mode("hidden")

    return save_sidebar_mode("full")
@app.callback(
    Output("sidebar-column", "width"),
    Output("sidebar-column", "style"),
    Output("sidebar-full", "style"),
    Output("sidebar-compact", "style"),
    Output("content-column", "width"),
    Output("floating-sidebar-button", "style"),
    Input("sidebar-state", "data"),
)
def apply_sidebar(mode):

    if mode == "hidden":
        return (
            0,
            {"display": "none"},
            {"display": "none"},
            {"display": "none"},
            12,
            {
                "display": "block",
                "position": "fixed",
                "top": "10px",
                "left": "10px",
                "zIndex": 2000,
            },
        )

    if mode == "compact":
        return (
            1,
            {},
            {"display": "none"},
            {"display": "block"},
            11,
            {"display": "none"},
        )

    return (
        2,
        {},
        {"display": "block"},
        {"display": "none"},
        10,
        {"display": "none"},
    )

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8050,
        debug=False,
    )
