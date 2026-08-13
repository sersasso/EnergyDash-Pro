#!/usr/bin/env bash
set -euo pipefail

cd /opt/energydash-pro
cp config.yaml /etc/energydash-pro/config.yaml
chown root:energydash /etc/energydash-pro/config.yaml
chmod 640 /etc/energydash-pro/config.yaml

./venv/bin/python init_db.py
bash systemd_units.sh
cp nginx_energydash.conf /etc/nginx/sites-available/energydash-pro.conf
ln -sf /etc/nginx/sites-available/energydash-pro.conf /etc/nginx/sites-enabled/energydash-pro.conf
nginx -t
systemctl reload nginx
systemctl start energydash-collector energydash-api energydash-web energydash-alert energydash-daily-update.timer
systemctl status energydash-collector --no-pager
systemctl status energydash-api --no-pager
systemctl status energydash-web --no-pager
curl -s http://127.0.0.1:8080/api/health | jq .
