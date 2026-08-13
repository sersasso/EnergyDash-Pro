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


def kwh(v):
    if v is None or pd.isna(v):
        return "n/d"
    return f"{v/1000:,.2f} kWh".replace(",", "X").replace(".", ",").replace("X", ".")


def watt(v):
    if v is None or pd.isna(v):
        return "n/d"
    return f"{v:,.0f} W".replace(",", ".")


def pct(v):
    if v is None or pd.isna(v):
        return "n/d"
    return f"{v:.1f}%"


def load_chart_settings():
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT metric,label,color_hex FROM chart_settings ORDER BY sort_order")
        ).mappings().all()

    return {
        r["metric"]: {"label": r["label"], "color": r["color_hex"]}
        for r in rows
    }


def load_live(limit=1440):
    sql = text("""
        SELECT ts, prod_power_w, cons_power_w, wifi_rssi,
               prod_voltage_v, cons_voltage_v, prod_current_a, cons_current_a,
               prod_freq_hz, cons_freq_hz, prod_pf, cons_pf
        FROM measures_raw
        ORDER BY ts DESC
        LIMIT :limit
    """)

    df = pd.read_sql_query(sql, engine, params={"limit": limit})

    if df.empty:
        return df

    df = df.sort_values("ts")
    df["ts"] = pd.to_datetime(df["ts"])
    df["self_consumption_w"] = df[["prod_power_w", "cons_power_w"]].min(axis=1)
    df["feed_in_w"] = (df["prod_power_w"] - df["cons_power_w"]).clip(lower=0)
    df["grid_import_w"] = (df["cons_power_w"] - df["prod_power_w"]).clip(lower=0)

    return df


def load_daily(days=3650):
    sql = text("""
        SELECT day, production_wh, consumption_wh, grid_import_wh, feed_in_wh,
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


def card(title, value, subtitle, colour="primary"):
    return dbc.Card(
        dbc.CardBody([
            html.H6(title, className="text-muted"),
            html.H3(value, className=f"text-{colour}"),
            html.Div(subtitle, className="small text-muted"),
        ]),
        className="shadow-sm h-100",
    )


def sidebar():
    return dbc.Col(
        dbc.Nav([
            dbc.NavLink("Home", href="/", active="exact"),
            dbc.NavLink("Live", href="/live", active="exact"),
            dbc.NavLink("Storico", href="/storico", active="exact"),
            dbc.NavLink("Report", href="/report", active="exact"),
            dbc.NavLink("Configurazione", href="/config", active="exact"),
            dbc.NavLink("Sistema", href="/sistema", active="exact"),
        ], vertical=True, pills=True),
        width=2,
        className="bg-dark p-3 min-vh-100",
    )


def nav_buttons():
    return dbc.ButtonGroup([
        dbc.Button("Home", href="/", color="secondary", outline=True, size="sm"),
        dbc.Button("Indietro", id="btn-history-back", color="secondary", outline=True, size="sm"),
    ], className="mb-3")


app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
    title="EnergyDash Pro",
)

server = app.server

app.layout = dbc.Container(fluid=True, children=[
    dcc.Location(id="url"),
    dcc.Interval(id="refresh", interval=10000, n_intervals=0),
    dcc.Store(id="history-state", data={"level": "year", "year": None, "month": None}),
    dbc.Row([
        sidebar(),
        dbc.Col(html.Div(id="page"), width=10, className="p-3"),
    ]),
])


def home_layout():
    return html.Div([
        html.H2(cfg["app"]["name"]),
        html.Div(id="home-cards"),
        dcc.Graph(id="home-chart"),
    ])


def live_layout():
    return html.Div([
        html.H2("Flussi Live"),
        dcc.Graph(id="live-chart"),
        html.Div(id="live-details"),
    ])


def storico_layout():
    return html.Div([
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
    ])


def simple_page(title, text):
    return html.Div([
        html.H2(title),
        dbc.ButtonGroup([
            dbc.Button("Home", href="/", color="secondary", outline=True, size="sm"),
        ], className="mb-3"),
        dbc.Alert(text, color="info"),
    ])


@app.callback(Output("page", "children"), Input("url", "pathname"))
def route(path):
    if path == "/live":
        return live_layout()
    if path == "/storico":
        return storico_layout()
    if path == "/report":
        return simple_page("Report", "Qui verranno elencati e generati i report PDF/Excel.")
    if path == "/config":
        return simple_page("Configurazione", "Qui verranno modificati colori, soglie, tariffe e parametri impianto.")
    if path == "/sistema":
        return simple_page("Sistema", "Health check, servizi, backup e diagnostica.")
    return home_layout()


@app.callback(
    Output("home-cards", "children"),
    Output("home-chart", "figure"),
    Input("refresh", "n_intervals"),
)
def update_home(_):
    df = load_live(720)

    if df.empty:
        return dbc.Alert("Nessun dato disponibile", color="warning"), go.Figure()

    x = df.iloc[-1]

    production = x.prod_power_w or 0
    consumption = x.cons_power_w or 0
    self_consumption = x.self_consumption_w or 0

    self_consumption_pct = (self_consumption / production * 100) if production else None
    self_sufficiency_pct = (self_consumption / consumption * 100) if consumption else None

    cards = dbc.Row([
        dbc.Col(card("Produzione", watt(x.prod_power_w), "Istantanea", "success"), md=2),
        dbc.Col(card("Consumo", watt(x.cons_power_w), "Istantaneo", "danger"), md=2),
        dbc.Col(card("Autoconsumo", watt(x.self_consumption_w), pct(self_consumption_pct), "info"), md=2),
        dbc.Col(card("Autosufficienza", pct(self_sufficiency_pct), "Quota consumi coperta da FV", "primary"), md=2),
        dbc.Col(card("Immissione", watt(x.feed_in_w), "Verso rete", "warning"), md=2),
        dbc.Col(card("Prelievo", watt(x.grid_import_w), "Da rete", "secondary"), md=2),
    ], className="g-3 mb-3")

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.ts, y=df.prod_power_w, name="Produzione"))
    fig.add_trace(go.Scatter(x=df.ts, y=df.cons_power_w, name="Consumo"))
    fig.update_layout(template="plotly_dark", height=420, title="Ultimi campioni", yaxis_title="W")

    return cards, fig


@app.callback(
    Output("live-chart", "figure"),
    Output("live-details", "children"),
    Input("refresh", "n_intervals"),
)
def update_live(_):
    df = load_live(1440)
    fig = go.Figure()

    if df.empty:
        fig.update_layout(template="plotly_dark", title="Nessun dato")
        return fig, "Nessun dato"

    for col, name in [
        ("prod_power_w", "Produzione"),
        ("cons_power_w", "Consumo"),
        ("self_consumption_w", "Autoconsumo"),
        ("feed_in_w", "Immissione"),
        ("grid_import_w", "Prelievo"),
    ]:
        fig.add_trace(go.Scatter(x=df.ts, y=df[col], name=name, mode="lines"))

    fig.update_layout(template="plotly_dark", height=560, yaxis_title="W", title="Flussi live")

    x = df.iloc[-1]

    details = dbc.Table([
        html.Tbody([
            html.Tr([html.Th("Ultimo campione"), html.Td(str(x.ts))]),
            html.Tr([html.Th("Tensione casa"), html.Td(f"{x.cons_voltage_v} V")]),
            html.Tr([html.Th("Corrente casa"), html.Td(f"{x.cons_current_a} A")]),
            html.Tr([html.Th("Cosfi casa"), html.Td(str(x.cons_pf))]),
            html.Tr([html.Th("Frequenza"), html.Td(f"{x.cons_freq_hz} Hz")]),
            html.Tr([html.Th("Wi-Fi Shelly"), html.Td(f"{x.wifi_rssi} dBm")]),
        ])
    ], bordered=True, striped=True, hover=True)

    return fig, details


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
        fig.add_trace(go.Bar(
            x=grouped[x_col],
            y=grouped[f] / 1000,
            name=info["label"],
            marker_color=info["color"],
        ))

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

    summary = dbc.Row([
        dbc.Col(card("Produzione", kwh(production), "Totale periodo", "success"), md=2),
        dbc.Col(card("Consumi", kwh(consumption), "Totale periodo", "danger"), md=2),
        dbc.Col(card("Autoconsumo", kwh(self_consumption), pct(self_consumption_pct), "info"), md=2),
        dbc.Col(card("Autosufficienza", pct(self_sufficiency_pct), "Consumi coperti da FV", "primary"), md=2),
        dbc.Col(card("Prelievi", kwh(totals.get("grid_import_wh", 0)), "Da rete", "secondary"), md=2),
        dbc.Col(card("Immissioni", kwh(totals.get("feed_in_wh", 0)), "Verso rete", "warning"), md=2),
    ], className="g-3")

    return options, fig, html.H5(title_text), summary


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)
