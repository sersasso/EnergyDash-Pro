from datetime import date
from pathlib import Path
import yaml
import pandas as pd
from sqlalchemy import create_engine, text
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from openpyxl import Workbook

CONFIG_FILE = "/etc/energydash-pro/config.yaml"

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)

def fmt_kwh(v):
    return f"{(v or 0)/1000:.2f} kWh"

def load_period(engine, start, end):
    sql = text("""
        SELECT day, production_wh, consumption_wh, grid_import_wh, feed_in_wh, self_consumption_wh
        FROM energy_daily
        WHERE day BETWEEN :start AND :end AND COALESCE(is_anomaly,false)=false
        ORDER BY day
    """)
    return pd.read_sql_query(sql, engine, params={"start": start, "end": end})

def create_pdf(df, path, title):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    story = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    totals = df[["production_wh","consumption_wh","grid_import_wh","feed_in_wh","self_consumption_wh"]].sum() if not df.empty else {}
    data = [["Indicatore", "Valore"],
            ["Produzione", fmt_kwh(totals.get("production_wh",0))],
            ["Consumi", fmt_kwh(totals.get("consumption_wh",0))],
            ["Prelievi", fmt_kwh(totals.get("grid_import_wh",0))],
            ["Immissioni", fmt_kwh(totals.get("feed_in_wh",0))],
            ["Autoconsumo", fmt_kwh(totals.get("self_consumption_wh",0))],
            ["Giorni", str(len(df))]]
    t = Table(data, colWidths=[220, 220])
    t.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1F4E79")),
                           ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                           ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
                           ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold")]))
    story.append(t)
    doc.build(story)

def create_xlsx(df, path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Energia"
    ws.append(["Giorno", "Produzione Wh", "Consumi Wh", "Prelievi Wh", "Immissioni Wh", "Autoconsumo Wh"])
    for _, r in df.iterrows():
        ws.append([str(r["day"]), r["production_wh"], r["consumption_wh"], r["grid_import_wh"], r["feed_in_wh"], r["self_consumption_wh"]])
    wb.save(path)

def generate_report(report_type, start, end):
    cfg = load_config()
    out_dir = Path(cfg["reports"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(cfg["database"]["url"], future=True)
    df = load_period(engine, start, end)
    stem = f"energydash_{report_type}_{start}_{end}"
    pdf = out_dir / f"{stem}.pdf"
    xlsx = out_dir / f"{stem}.xlsx"
    create_pdf(df, pdf, f"EnergyDash Pro - Report {report_type} {start} - {end}")
    create_xlsx(df, xlsx)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO reports(report_type, period_start, period_end, file_pdf, file_xlsx, status, message)
            VALUES(:type, :start, :end, :pdf, :xlsx, 'created', :message)
        """), {"type": report_type, "start": start, "end": end, "pdf": str(pdf), "xlsx": str(xlsx), "message": f"{len(df)} giorni"})
    print(f"Creati: {pdf} e {xlsx}")

if __name__ == "__main__":
    today = date.today()
    start = today.replace(day=1)
    generate_report("monthly", start, today)
