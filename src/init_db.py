import pathlib
import yaml
from sqlalchemy import create_engine, text

CONFIG_FILE = "/etc/energydash-pro/config.yaml"


def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)


def find_schema_file():
    base_dir = pathlib.Path(__file__).resolve().parent

    candidates = [
        base_dir / "database" / "01_schema.sql",
        base_dir.parent / "database" / "01_schema.sql",
        pathlib.Path.cwd() / "database" / "01_schema.sql",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "Schema file non trovato. Percorsi cercati: "
        + ", ".join(str(c) for c in candidates)
    )


def split_sql_statements(sql_text):
    statements = []
    current = []
    in_single_quote = False
    in_line_comment = False

    i = 0
    while i < len(sql_text):
        char = sql_text[i]
        next_char = sql_text[i + 1] if i + 1 < len(sql_text) else ""

        if not in_single_quote and char == "-" and next_char == "-":
            in_line_comment = True

        if in_line_comment:
            current.append(char)
            if char == "\n":
                in_line_comment = False
            i += 1
            continue

        if char == "'" and not in_line_comment:
            in_single_quote = not in_single_quote

        if char == ";" and not in_single_quote:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(char)

        i += 1

    final_statement = "".join(current).strip()
    if final_statement:
        statements.append(final_statement)

    return statements


def main():
    cfg = load_config()
    engine = create_engine(cfg["database"]["url"], future=True)

    schema_file = find_schema_file()
    print(f"Uso schema: {schema_file}")

    schema = schema_file.read_text()

    with engine.begin() as conn:
        for statement in split_sql_statements(schema):
            conn.execute(text(statement))

    print("Schema EnergyDash Pro inizializzato")


if __name__ == "__main__":
    main()
