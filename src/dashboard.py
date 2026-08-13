import sqlite3
from datetime import date, timedelta
import pandas as pd
import plotly.graph_objects as go
import yaml
from dash import Dash, dcc, html, Input, Output
import dash_bootstrap_components as dbc
from sqlalchemy import create_engine, text

CONFIG_FILE = "/etc/energydash-pro/config.yaml"

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)

cfg = load_config()
engine = create_engine(cfg["database"]["url"], pool_pre_ping=True, future=True)

def kwh(v):
    return "n/d" if v is None or pd.isna(v) else f"{v/1000:,.2f} kWh".replace(",", "X").replace(".", ",").replace("X", ".")

def watt(v):
    return "n/d" if v is None or pd.isna(v) else f"{v:,.0f} W".replace(",", ".")

def load_chart_settings():
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT metric,label,color_hex FROM chart_settings ORDER BY sort_order")).mappings().all()
    return {r["metric"]: {"label": r["label"], "color": r["color_hex"]} for r in rows}

def load_live(limit=1440):
    sql = text("""
        SELECT ts, prod_power_w, cons_power_w, wifi_rssi,
               prod_voltage_v, cons_voltage_v, prod_current_a, cons_current_a, prod_freq_hz, cons_freq_hz, prod_pf, cons_pf
        FROM measures_raw ORDER BY ts DESC LIMIT :limit
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

def load_daily(days=365):
    sql = text("""
        SELECT day, production_wh, consumption_wh, grid_import_wh, feed_in_wh, self_consumption_wh
        FROM energy_daily WHERE day >= current_date - (:days || ' days')::interval AND COALESCE(is_anomaly,false)=false
        ORDER BY day
    """)
    return pd.read_sql_query(sql, engine, params={"days": days})

def card(title, value, subtitle, colour="primary"):
    return dbc.Card(dbc.CardBody([html.H6(title, className="text-muted"), html.H3(value, className=f"text-{colour}"), html.Div(subtitle, className="small text-muted")]), className="shadow-sm h-100")

def sidebar():
    return dbc.Col(dbc.Nav([
        dbc.NavLink("Home", href="/", active="exact"),
        dbc.NavLink("Live", href="/live", active="exact"),
        dbc.NavLink("Storico", href="/storico", active="exact"),
        dbc.NavLink("Report", href="/report", active="exact"),
        dbc.NavLink("Configurazione", href="/config", active="exact"),
        dbc.NavLink("Sistema", href="/sistema", active="exact"),
    ], vertical=True, pills=True), width=2, className="bg-dark p-3 min-vh-100")

app = Dash(__name__, external_stylesheets=[dbc.themes.DARKLY], suppress_callback_exceptions=True, title="EnergyDash Pro")
server = app.server
app.layout = dbc.Container(fluid=True, children=[dcc.Location(id="url"), dcc.Interval(id="refresh", interval=10000, n_intervals=0), dbc.Row([sidebar(), dbc.Col(html.Div(id="page"), width=10, className="p-3")])])

def home_layout():
    return html.Div([html.H2(cfg["app"]["name"]), html.Div(id="home-cards"), dcc.Graph(id="home-chart")])

def live_layout():
    return html.Div([html.H2("Flussi Live"), dcc.Graph(id="live-chart"), html.Div(id="live-details")])

def storico_layout():
    return html.Div([html.H2("Storico"), dcc.Dropdown(id="history-fields", multi=True, value=["production_wh","consumption_wh","grid_import_wh","feed_in_wh","self_consumption_wh"], options=[] , style={"color":"#000"}), dcc.Graph(id="history-chart")])

def simple_page(title, text):
    return html.Div([html.H2(title), dbc.Alert(text, color="info")])

@app.callback(Output("page", "children"), Input("url", "pathname"))
def route(path):
    if path == "/live": return live_layout()
    if path == "/storico": return storico_layout()
    if path == "/report": return simple_page("Report", "Qui verranno elencati e generati i report PDF/Excel.")
    if path == "/config": return simple_page("Configurazione", "Qui verranno modificati colori, soglie, tariffe e parametri impianto.")
    if path == "/sistema": return simple_page("Sistema", "Health check, servizi, backup e diagnostica.")
    return home_layout()

@app.callback(Output("home-cards", "children"), Output("home-chart", "figure"), Input("refresh", "n_intervals"))
def update_home(_):
    df = load_live(720)
    if df.empty:
        return dbc.Alert("Nessun dato disponibile", color="warning"), go.Figure()
    x = df.iloc[-1]
    cards = dbc.Row([
        dbc.Col(card("Produzione", watt(x.prod_power_w), "Istantanea", "success"), md=2),
        dbc.Col(card("Consumo", watt(x.cons_power_w), "Istantaneo", "danger"), md=2),
        dbc.Col(card("Autoconsumo", watt(x.self_consumption_w), "Istantaneo", "info"), md=2),
        dbc.Col(card("Immissione", watt(x.feed_in_w), "Verso rete", "warning"), md=2),
        dbc.Col(card("Prelievo", watt(x.grid_import_w), "Da rete", "primary"), md=2),
        dbc.Col(card("Wi-Fi", f"{x.wifi_rssi} dBm", "Shelly", "secondary"), md=2),
    ], className="g-3 mb-3")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.ts, y=df.prod_power_w, name="Produzione"))
    fig.add_trace(go.Scatter(x=df.ts, y=df.cons_power_w, name="Consumo"))
    fig.update_layout(template="plotly_dark", height=420, title="Ultimi campioni", yaxis_title="W")
    return cards, fig

@app.callback(Output("live-chart", "figure"), Output("live-details", "children"), Input("refresh", "n_intervals"))
def update_live(_):
    df = load_live(1440)
    fig = go.Figure()
    if df.empty:
        fig.update_layout(template="plotly_dark", title="Nessun dato")
        return fig, "Nessun dato"
    for col, name in [("prod_power_w","Produzione"),("cons_power_w","Consumo"),("self_consumption_w","Autoconsumo"),("feed_in_w","Immissione"),("grid_import_w","Prelievo")]:
        fig.add_trace(go.Scatter(x=df.ts, y=df[col], name=name, mode="lines"))
    fig.update_layout(template="plotly_dark", height=560, yaxis_title="W", title="Flussi live")
    x = df.iloc[-1]
    details = dbc.Table([html.Tbody([
        html.Tr([html.Th("Ultimo campione"), html.Td(str(x.ts))]),
        html.Tr([html.Th("Tensione casa"), html.Td(f"{x.cons_voltage_v} V")]),
        html.Tr([html.Th("Corrente casa"), html.Td(f"{x.cons_current_a} A")]),
        html.Tr([html.Th("Cosfi casa"), html.Td(str(x.cons_pf))]),
        html.Tr([html.Th("Frequenza"), html.Td(f"{x.cons_freq_hz} Hz")]),
    ])], bordered=True, striped=True, hover=True)
    return fig, details

@app.callback(Output("history-fields", "options"), Output("history-chart", "figure"), Input("refresh", "n_intervals"), Input("history-fields", "value"))
def update_history(_, fields):
    settings = load_chart_settings()
    options = [{"label": v["label"], "value": k} for k,v in settings.items()]
    fields = fields or [o["value"] for o in options]
    df = load_daily(3650)
    fig = go.Figure()
    if df.empty:
        fig.update_layout(template="plotly_dark", title="Nessun dato storico")
        return options, fig
    df["month"] = pd.to_datetime(df["day"]).dt.strftime("%Y-%m")
    g = df.groupby("month", as_index=False)[fields].sum()
    for f in fields:
        info = settings.get(f, {"label": f, "color": None})
        fig.add_trace(go.Bar(x=g["month"], y=g[f]/1000, name=info["label"], marker_color=info["color"]))
    fig.update_layout(template="plotly_dark", barmode="group", height=560, title="Storico mensile", yaxis_title="kWh")
    return options, fig

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050, debug=False)
