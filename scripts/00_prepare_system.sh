#!/usr/bin/env bash
set -euo pipefail

APP_USER="energydash"
APP_HOME="/opt/energydash-pro"
APP_ETC="/etc/energydash-pro"
APP_DATA="/var/lib/energydash-pro"
APP_LOG="/var/log/energydash-pro"
DB_NAME="energydash"
DB_USER="energydash"
DB_PASSWORD="CHANGE_ME_STRONG_PASSWORD"

apt update
apt full-upgrade -y
apt install -y python3 python3-venv python3-pip python3-dev build-essential \
  postgresql postgresql-contrib postgresql-client sqlite3 curl jq git nano rsync \
  nginx ufw logrotate ca-certificates openssl

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_HOME" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_HOME" "$APP_ETC" "$APP_DATA"/reports "$APP_DATA"/backup "$APP_DATA"/import/metern "$APP_LOG"
chown -R "$APP_USER":"$APP_USER" "$APP_HOME" "$APP_DATA" "$APP_LOG"
chmod 750 "$APP_ETC" "$APP_DATA" "$APP_LOG"

sudo -u postgres psql <<SQL
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$DB_USER') THEN
      CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD';
   END IF;
END
\$\$;
SELECT 'CREATE DATABASE $DB_NAME OWNER $DB_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$DB_NAME')\gexec
SQL

cd "$APP_HOME"
python3 -m venv venv
./venv/bin/pip install --upgrade pip wheel
./venv/bin/pip install -r requirements.txt

cat >/etc/logrotate.d/energydash-pro <<'LOGROTATE'
/var/log/energydash-pro/*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
LOGROTATE

echo "Sistema base pronto. Modificare DB_PASSWORD e /etc/energydash-pro/config.yaml prima dello start."
