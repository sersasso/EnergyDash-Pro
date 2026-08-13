import time
import logging
from datetime import datetime, timezone, timedelta
import requests
import yaml
from sqlalchemy import create_engine, text

CONFIG_FILE = "/etc/energydash-pro/config.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s energydash-alert %(message)s",
)
logger = logging.getLogger("energydash-alert")

LAST_EMITTED = {}


def load_config():
    with open(CONFIG_FILE, "r") as f:
        return yaml.safe_load(f)


def cooldown_minutes(cfg):
    return int(cfg.get("alerts", {}).get("cooldown_minutes", 30))


def can_emit_alert(key, cfg):
    now = datetime.now(timezone.utc)
    cooldown = timedelta(minutes=cooldown_minutes(cfg))

    previous = LAST_EMITTED.get(key)
    if previous and now - previous < cooldown:
        logger.info("Alert soppresso per cooldown: %s", key)
        return False

    LAST_EMITTED[key] = now
    return True


def send_ntfy(cfg, title, message):
    n = cfg.get("notifications", {})

    if not n.get("ntfy_enabled"):
        logger.warning("NOTIFICA DISABILITATA: %s - %s", title, message)
        return

    topic = n.get("ntfy_topic")
    if not topic:
        logger.warning("ntfy_enabled=true ma ntfy_topic non configurato")
        return

    url = n.get("ntfy_url", "https://ntfy.sh").rstrip("/") + "/" + topic

    response = requests.post(
        url,
        data=message.encode("utf-8"),
        headers={"Title": title},
        timeout=5,
    )
    response.raise_for_status()
    logger.info("Notifica inviata: %s", title)


def open_event(conn, severity, message):
    conn.execute(
        text("""
            INSERT INTO alert_events(rule_id,status,severity,message)
            VALUES(NULL,'open',:severity,:message)
        """),
        {"severity": severity, "message": message},
    )


def emit_alert(conn, cfg, key, severity, title, message):
    if not can_emit_alert(key, cfg):
        return

    open_event(conn, severity, message)

    try:
        send_ntfy(cfg, title, message)
    except Exception as exc:
        logger.error("Errore invio notifica: %s", exc)


def check_last_sample(engine, cfg):
    minutes = int(cfg.get("thresholds", {}).get("shelly_offline_minutes", 3))

    with engine.begin() as conn:
        last = conn.execute(text("SELECT max(ts) FROM measures_raw")).scalar()

        if not last:
            emit_alert(
                conn,
                cfg,
                "no_samples",
                "high",
                "EnergyDash Pro",
                "Nessun campione presente nel database",
            )
            return

        if last.tzinfo is None:
            now = datetime.utcnow()
        else:
            now = datetime.now(timezone.utc).astimezone(last.tzinfo)

        age = (now - last).total_seconds() / 60

        if age > minutes:
            emit_alert(
                conn,
                cfg,
                "shelly_offline",
                "high",
                "EnergyDash Pro - Allarme",
                f"Shelly offline o collector fermo: ultimo campione {age:.1f} minuti fa",
            )


def check_consumption(engine, cfg):
    threshold = float(cfg.get("thresholds", {}).get("high_consumption_w", 6000))

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT ts, cons_power_w FROM measures_raw ORDER BY ts DESC LIMIT 1")
        ).mappings().first()

        if row and row["cons_power_w"] is not None and row["cons_power_w"] > threshold:
            emit_alert(
                conn,
                cfg,
                "high_consumption",
                "medium",
                "EnergyDash Pro - Consumo elevato",
                f"Consumo elevato: {row['cons_power_w']:.0f} W alle {row['ts']}",
            )


def main():
    cfg = load_config()
    engine = create_engine(cfg["database"]["url"], pool_pre_ping=True, future=True)

    logger.info("EnergyDash Pro alert worker avviato")

    while True:
        try:
            check_last_sample(engine, cfg)
            check_consumption(engine, cfg)
        except Exception as exc:
            logger.exception("Errore alert worker: %s", exc)

        time.sleep(60)


if __name__ == "__main__":
    main()
