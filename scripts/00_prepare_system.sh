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
# Determine repository location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Repository root: $REPO_ROOT"
echo "Application home: $APP_HOME"

# Copy application files
mkdir -p "$APP_HOME/config"
mkdir -p "$APP_HOME/database"
mkdir -p "$APP_HOME/scripts"

cp -a "$REPO_ROOT/scripts/." "$APP_HOME/scripts/"
cp -a "$REPO_ROOT/src/." "$APP_HOME/"
cp -a "$REPO_ROOT/config/." "$APP_HOME/config/"
cp -a "$REPO_ROOT/database/." "$APP_HOME/database/"

cp "$REPO_ROOT/requirements.txt" "$APP_HOME/"
cp "$REPO_ROOT/README.md" "$APP_HOME/" 2>/dev/null || true

chown -R "$APP_USER:$APP_USER" "$APP_HOME"

runuser -u postgres -- psql <<SQL
DO \$\$
BEGIN
   IF NOT EXISTS (
      SELECT FROM pg_catalog.pg_roles
      WHERE rolname = '$DB_USER'
   ) THEN
      CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD';
   ELSE
      ALTER ROLE $DB_USER WITH PASSWORD '$DB_PASSWORD';
   END IF;
END
\$\$;
SQL

DB_ENCODING="$(runuser -u postgres -- psql -Atc "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname='$DB_NAME';")"

if [ -z "$DB_ENCODING" ]; then
    runuser -u postgres -- psql <<SQL
CREATE DATABASE $DB_NAME
OWNER $DB_USER
ENCODING 'UTF8'
LC_COLLATE 'C.UTF-8'
LC_CTYPE 'C.UTF-8'
TEMPLATE template0;
SQL
elif [ "$DB_ENCODING" != "UTF8" ]; then
    echo "ERROR: Database $DB_NAME exists with encoding $DB_ENCODING, but UTF8 is required."
    echo "For a fresh dev install, drop and recreate it manually."
    exit 1
fi

cd "$APP_HOME"

echo "Checking copied files..."

test -f requirements.txt || {
    echo "ERROR: requirements.txt not found in $APP_HOME"
    exit 1
}

ls -l requirements.txt
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
