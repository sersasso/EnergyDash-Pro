import pathlib
import yaml
from sqlalchemy import create_engine, text

CONFIG_FILE = "/etc/energydash-pro/config/config.yaml"

BASE_DIR = pathlib.Path(__file__).resolve().parent
SCHEMA_FILE = BASE_DIR / "database" / "01_schema.sql"

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)

def main():
    cfg = load_config()
    engine = create_engine(cfg["database"]["url"], future=True)
    schema = pathlib.Path(SCHEMA_FILE).read_text()
    with engine.begin() as conn:
        for statement in [s.strip() for s in schema.split(";") if s.strip()]:
            conn.execute(text(statement))
    print("Schema EnergyDash Pro inizializzato")

if __name__ == "__main__":
    main()
