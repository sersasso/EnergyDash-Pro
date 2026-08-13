#!/usr/bin/env bash
set -euo pipefail

APP_HOME="/opt/energydash-pro"
APP_ETC="/etc/energydash-pro"
APP_USER="energydash"

cd "$APP_HOME"

if [ ! -f "$APP_ETC/config.yaml" ]; then
    cp config/config.yaml "$APP_ETC/config.yaml"
fi

chown root:"$APP_USER" "$APP_ETC"
chmod 755 "$APP_ETC"
chown root:"$APP_USER" "$APP_ETC/config.yaml"
chmod 640 "$APP_ETC/config.yaml"

./venv/bin/python init_db.py

bash scripts/systemd_units.sh

cp config/nginx_energydash.conf /etc/nginx/sites-available/energydash-pro.conf
ln -sf /etc/nginx/sites-available/energydash-pro.conf /etc/nginx/sites-enabled/energydash-pro.conf

nginx -t
systemctl reload nginx

systemctl restart energydash-collector energydash-api energydash-web energydash-alert
systemctl restart energydash-daily-update.timer energydash-report.timer || true

systemctl status energydash-collector --no-pager || true
systemctl status energydash-api --no-pager || true
systemctl status energydash-web --no-pager || true
curl -s http://127.0.0.1:8080/api/health | jq . || true
