import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import yaml
from sqlalchemy import create_engine, text

CONFIG_FILE = "/etc/energydash-pro/config.yaml"

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)

def compute_daily(df, timezone_name):
    if df.empty or len(df) < 2:
        return pd.DataFrame()
    tz = ZoneInfo(timezone_name)
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df["local_day"] = df["ts"].dt.tz_convert(tz).dt.date
    df["prev_day"] = df["local_day"].shift(1)
    df["delta_prod_wh"] = df["prod_energy_wh"].diff()
    df["delta_cons_wh"] = df["cons_energy_wh"].diff()
    valid = df[(df["local_day"] == df["prev_day"]) & (df["delta_prod_wh"] >= 0) & (df["delta_cons_wh"] >= 0)].copy()
    if valid.empty:
        return pd.DataFrame()
    valid["self_consumption_wh"] = valid[["delta_prod_wh", "delta_cons_wh"]].min(axis=1)
    valid["feed_in_wh"] = (valid["delta_prod_wh"] - valid["delta_cons_wh"]).clip(lower=0)
    valid["grid_import_wh"] = (valid["delta_cons_wh"] - valid["delta_prod_wh"]).clip(lower=0)
    return valid.groupby("local_day", as_index=False).agg(
        production_wh=("delta_prod_wh", "sum"),
        consumption_wh=("delta_cons_wh", "sum"),
        grid_import_wh=("grid_import_wh", "sum"),
        feed_in_wh=("feed_in_wh", "sum"),
        self_consumption_wh=("self_consumption_wh", "sum"),
        samples=("id", "count"),
    )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    cfg = load_config()
    engine = create_engine(cfg["database"]["url"], future=True)
    since = datetime.utcnow() - timedelta(days=args.days + 2)
    query = text("""
        SELECT id, ts, prod_energy_wh, cons_energy_wh
        FROM measures_raw
        WHERE ts >= :since AND prod_energy_wh IS NOT NULL AND cons_energy_wh IS NOT NULL
        ORDER BY ts
    """)
    df = pd.read_sql_query(query, engine, params={"since": since})
    daily = compute_daily(df, cfg["app"].get("timezone", "Europe/Rome"))
    if daily.empty:
        print("Nessun dato giornaliero da aggiornare")
        return
    upsert = text("""
        INSERT INTO energy_daily(day, production_wh, consumption_wh, grid_import_wh, feed_in_wh, self_consumption_wh, source, quality, samples, is_anomaly, anomaly_note, updated_at)
        VALUES(:day, :production_wh, :consumption_wh, :grid_import_wh, :feed_in_wh, :self_consumption_wh, 'shelly', 'computed_interval', :samples, false, NULL, now())
        ON CONFLICT(day) DO UPDATE SET
            production_wh=EXCLUDED.production_wh,
            consumption_wh=EXCLUDED.consumption_wh,
            grid_import_wh=EXCLUDED.grid_import_wh,
            feed_in_wh=EXCLUDED.feed_in_wh,
            self_consumption_wh=EXCLUDED.self_consumption_wh,
            source=EXCLUDED.source,
            quality=EXCLUDED.quality,
            samples=EXCLUDED.samples,
            is_anomaly=EXCLUDED.is_anomaly,
            anomaly_note=EXCLUDED.anomaly_note,
            updated_at=now()
    """)
    with engine.begin() as conn:
        for _, r in daily.iterrows():
            conn.execute(upsert, {
                "day": r["local_day"],
                "production_wh": float(r["production_wh"]),
                "consumption_wh": float(r["consumption_wh"]),
                "grid_import_wh": float(r["grid_import_wh"]),
                "feed_in_wh": float(r["feed_in_wh"]),
                "self_consumption_wh": float(r["self_consumption_wh"]),
                "samples": int(r["samples"]),
            })
    print(f"Giorni aggiornati: {len(daily)}")

if __name__ == "__main__":
    main()
