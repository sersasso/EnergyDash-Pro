import time
from datetime import datetime, timezone
import requests
import yaml
from sqlalchemy import create_engine, text

CONFIG_FILE = "/etc/energydash-pro/config/config.yaml"

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)

def send_ntfy(cfg, title, message):
    n = cfg.get("notifications", {})
    if not n.get("ntfy_enabled"):
        print(f"NOTIFICA DISABILITATA: {title} - {message}")
        return
    url = n.get("ntfy_url", "https://ntfy.sh").rstrip("/") + "/" + n["ntfy_topic"]
    requests.post(url, data=message.encode("utf-8"), headers={"Title": title}, timeout=5)

def open_event(conn, severity, message):
    conn.execute(text("INSERT INTO alert_events(rule_id,status,severity,message) VALUES(NULL,'open',:severity,:message)"), {"severity": severity, "message": message})

def check_last_sample(engine, cfg):
    minutes = cfg["thresholds"].get("shelly_offline_minutes", 3)
    with engine.begin() as conn:
        last = conn.execute(text("SELECT max(ts) FROM measures_raw")).scalar()
        if not last:
            msg = "Nessun campione presente nel database"
            open_event(conn, "high", msg)
            send_ntfy(cfg, "EnergyDash Pro", msg)
            return
        age = (datetime.now(timezone.utc) - last).total_seconds() / 60
        if age > minutes:
            msg = f"Shelly offline o collector fermo: ultimo campione {age:.1f} minuti fa"
            open_event(conn, "high", msg)
            send_ntfy(cfg, "EnergyDash Pro - Allarme", msg)

def check_consumption(engine, cfg):
    threshold = cfg["thresholds"].get("high_consumption_w", 6000)
    with engine.begin() as conn:
        row = conn.execute(text("SELECT ts, cons_power_w FROM measures_raw ORDER BY ts DESC LIMIT 1")).mappings().first()
        if row and row["cons_power_w"] and row["cons_power_w"] > threshold:
            msg = f"Consumo elevato: {row['cons_power_w']:.0f} W alle {row['ts']}"
            open_event(conn, "medium", msg)
            send_ntfy(cfg, "EnergyDash Pro - Consumo elevato", msg)

def main():
    cfg = load_config()
    engine = create_engine(cfg["database"]["url"], pool_pre_ping=True, future=True)
    print("EnergyDash Pro alert worker avviato", flush=True)
    while True:
        try:
            check_last_sample(engine, cfg)
            check_consumption(engine, cfg)
        except Exception as exc:
            print(f"Errore alert worker: {exc}", flush=True)
        time.sleep(60)

if __name__ == "__main__":
    main()
