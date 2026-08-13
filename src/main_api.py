from datetime import date, datetime, timedelta
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

CONFIG_FILE = "/etc/energydash-pro/config/config.yaml"

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)

cfg = load_config()
engine = create_engine(cfg["database"]["url"], pool_pre_ping=True, future=True)
app = FastAPI(title="EnergyDash Pro API", version="1.0.0")

class SettingUpdate(BaseModel):
    key: str
    value: str
    value_type: str = "string"
    description: str | None = None

@app.get("/api/health")
def health():
    with engine.begin() as conn:
        last = conn.execute(text("SELECT max(ts) FROM measures_raw")).scalar()
        days = conn.execute(text("SELECT count(*) FROM energy_daily")).scalar()
    age = None
    if last:
        age = int((datetime.utcnow().replace(tzinfo=last.tzinfo) - last).total_seconds())
    return {"status": "ok", "last_sample": last, "last_sample_age_seconds": age, "daily_rows": days}

@app.get("/api/live")
def live():
    sql = text("""
        SELECT ts, prod_power_w, cons_power_w, prod_voltage_v, cons_voltage_v, prod_current_a, cons_current_a,
               prod_freq_hz, cons_freq_hz, prod_pf, cons_pf, wifi_rssi
        FROM measures_raw ORDER BY ts DESC LIMIT 1
    """)
    with engine.begin() as conn:
        row = conn.execute(sql).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Nessun campione presente")
    prod = row["prod_power_w"] or 0
    cons = row["cons_power_w"] or 0
    auto = min(prod, cons)
    return {**dict(row), "self_consumption_w": auto, "feed_in_w": max(prod-cons, 0), "grid_import_w": max(cons-prod, 0)}

@app.get("/api/history/daily")
def daily(start: date | None = None, end: date | None = None, include_anomalies: bool = False):
    start = start or (date.today() - timedelta(days=365))
    end = end or date.today()
    where_anom = "" if include_anomalies else "AND COALESCE(is_anomaly,false)=false"
    sql = text(f"""
        SELECT day, production_wh, consumption_wh, grid_import_wh, feed_in_wh, self_consumption_wh, source, quality, is_anomaly
        FROM energy_daily
        WHERE day BETWEEN :start AND :end {where_anom}
        ORDER BY day
    """)
    with engine.begin() as conn:
        return [dict(r) for r in conn.execute(sql, {"start": start, "end": end}).mappings()]

@app.get("/api/settings")
def get_settings():
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT key,value,value_type,description FROM settings ORDER BY key")).mappings().all()
    return [dict(r) for r in rows]

@app.put("/api/settings")
def put_setting(item: SettingUpdate):
    sql = text("""
        INSERT INTO settings(key,value,value_type,description,updated_at,updated_by)
        VALUES(:key,:value,:value_type,:description,now(),'api')
        ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, value_type=EXCLUDED.value_type,
          description=EXCLUDED.description, updated_at=now(), updated_by='api'
    """)
    with engine.begin() as conn:
        conn.execute(sql, item.model_dump())
    return {"status": "ok"}

@app.get("/api/reports")
def reports():
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT * FROM reports ORDER BY created_at DESC LIMIT 100")).mappings().all()
    return [dict(r) for r in rows]
