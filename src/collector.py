import time
from datetime import datetime, timezone
import requests
import yaml
from sqlalchemy import create_engine, text

CONFIG_FILE = "/etc/energydash-pro/config/config.yaml"

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)

def get_value(data, path, default=None):
    cur = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur

def fetch_shelly(url, timeout):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()

def build_row(data, prod_ch, cons_ch):
    prod = f"em1:{prod_ch}"
    cons = f"em1:{cons_ch}"
    prod_data = f"em1data:{prod_ch}"
    cons_data = f"em1data:{cons_ch}"
    return {
        "ts": datetime.now(timezone.utc),
        "prod_power_w": get_value(data, [prod, "act_power"]),
        "prod_energy_wh": get_value(data, [prod_data, "total_act_energy"]),
        "prod_ret_energy_wh": get_value(data, [prod_data, "total_act_ret_energy"]),
        "prod_voltage_v": get_value(data, [prod, "voltage"]),
        "prod_current_a": get_value(data, [prod, "current"]),
        "prod_freq_hz": get_value(data, [prod, "freq"]),
        "prod_pf": get_value(data, [prod, "pf"]),
        "cons_power_w": get_value(data, [cons, "act_power"]),
        "cons_energy_wh": get_value(data, [cons_data, "total_act_energy"]),
        "cons_ret_energy_wh": get_value(data, [cons_data, "total_act_ret_energy"]),
        "cons_voltage_v": get_value(data, [cons, "voltage"]),
        "cons_current_a": get_value(data, [cons, "current"]),
        "cons_freq_hz": get_value(data, [cons, "freq"]),
        "cons_pf": get_value(data, [cons, "pf"]),
        "shelly_unixtime": get_value(data, ["sys", "unixtime"]),
        "shelly_uptime": get_value(data, ["sys", "uptime"]),
        "wifi_rssi": get_value(data, ["wifi", "rssi"]),
    }

def insert_row(engine, row):
    sql = text("""
        INSERT INTO measures_raw (
            ts, prod_power_w, prod_energy_wh, prod_ret_energy_wh, prod_voltage_v, prod_current_a, prod_freq_hz, prod_pf,
            cons_power_w, cons_energy_wh, cons_ret_energy_wh, cons_voltage_v, cons_current_a, cons_freq_hz, cons_pf,
            shelly_unixtime, shelly_uptime, wifi_rssi
        ) VALUES (
            :ts, :prod_power_w, :prod_energy_wh, :prod_ret_energy_wh, :prod_voltage_v, :prod_current_a, :prod_freq_hz, :prod_pf,
            :cons_power_w, :cons_energy_wh, :cons_ret_energy_wh, :cons_voltage_v, :cons_current_a, :cons_freq_hz, :cons_pf,
            :shelly_unixtime, :shelly_uptime, :wifi_rssi
        )
    """)
    with engine.begin() as conn:
        conn.execute(sql, row)

def main():
    cfg = load_config()
    engine = create_engine(cfg["database"]["url"], pool_pre_ping=True, future=True)
    url = cfg["shelly"]["url"]
    timeout = cfg["shelly"].get("timeout_seconds", 3)
    interval = cfg["shelly"].get("interval_seconds", 5)
    prod_ch = cfg["shelly"].get("production_channel", 0)
    cons_ch = cfg["shelly"].get("consumption_channel", 1)
    print(f"EnergyDash Pro collector avviato: {url}", flush=True)
    while True:
        try:
            data = fetch_shelly(url, timeout)
            row = build_row(data, prod_ch, cons_ch)
            insert_row(engine, row)
            print(f"{row['ts'].isoformat()} prod={row['prod_power_w']}W cons={row['cons_power_w']}W rssi={row['wifi_rssi']}", flush=True)
        except Exception as exc:
            print(f"Errore collector: {exc}", flush=True)
        time.sleep(interval)

if __name__ == "__main__":
    main()
