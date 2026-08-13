import csv
import glob
from datetime import datetime
from pathlib import Path
import yaml
from sqlalchemy import create_engine, text

CONFIG_FILE = "/etc/energydash-pro/config/config.yaml"
IMPORT_DIR = Path("/var/lib/energydash-pro/import/metern")
FILE_MAPPINGS = {
    "1Produzione*.csv": "production_wh",
    "2Consumi*.csv": "consumption_wh",
    "3Prelievi*.csv": "grid_import_wh",
    "4Immissioni*.csv": "feed_in_wh",
    "5Autoconsumo*.csv": "self_consumption_wh",
}

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)

def parse_day(value):
    return datetime.strptime(value.strip(), "%Y%m%d").date()

def parse_value(value):
    v = value.strip().replace(",", ".")
    return None if v == "" else float(v)

def read_file(path):
    rows = {}
    with open(path, "r", newline="") as f:
        reader = csv.reader(f)
        for n, row in enumerate(reader, start=1):
            if len(row) < 2:
                continue
            try:
                rows[parse_day(row[0])] = parse_value(row[1])
            except Exception as exc:
                print(f"Riga ignorata {path}:{n}: {row} - {exc}")
    return rows

def main():
    cfg = load_config()
    engine = create_engine(cfg["database"]["url"], future=True)
    data = {col: {} for col in FILE_MAPPINGS.values()}
    for pattern, col in FILE_MAPPINGS.items():
        files = sorted(glob.glob(str(IMPORT_DIR / pattern)))
        print(f"{pattern} -> {col}: {len(files)} file")
        for path in files:
            file_data = read_file(path)
            data[col].update(file_data)
            print(f"  {path}: {len(file_data)} righe")
    all_days = sorted(set().union(*[set(v.keys()) for v in data.values()])) if data else []
    upsert = text("""
        INSERT INTO energy_daily(day, production_wh, consumption_wh, grid_import_wh, feed_in_wh, self_consumption_wh, source, quality, samples, is_anomaly, anomaly_note, updated_at)
        VALUES(:day, :production_wh, :consumption_wh, :grid_import_wh, :feed_in_wh, :self_consumption_wh, 'metern', 'imported_daily', 0, false, NULL, now())
        ON CONFLICT(day) DO UPDATE SET
            production_wh=EXCLUDED.production_wh,
            consumption_wh=EXCLUDED.consumption_wh,
            grid_import_wh=EXCLUDED.grid_import_wh,
            feed_in_wh=EXCLUDED.feed_in_wh,
            self_consumption_wh=EXCLUDED.self_consumption_wh,
            source=EXCLUDED.source,
            quality=EXCLUDED.quality,
            updated_at=now()
    """)
    with engine.begin() as conn:
        for day in all_days:
            conn.execute(upsert, {"day": day, **{col: data[col].get(day) for col in FILE_MAPPINGS.values()}})
    print(f"Giorni importati/aggiornati: {len(all_days)}")

if __name__ == "__main__":
    main()
