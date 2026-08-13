#!/usr/bin/env bash
set -euo pipefail

echo "== systemd =="
systemctl --no-pager --failed || true
systemctl status energydash-collector energydash-api energydash-web energydash-alert energydash-daily-update.timer --no-pager || true

echo "== API health =="
curl -s http://127.0.0.1:8080/api/health | jq . || true

echo "== PostgreSQL quick check =="
sudo -u postgres psql -d energydash -c "SELECT COUNT(*) AS measures FROM measures_raw;"
sudo -u postgres psql -d energydash -c "SELECT MIN(day), MAX(day), COUNT(*) FROM energy_daily;"

echo "== Porte =="
ss -ltnp | grep -E ':80|:8050|:8080' || true
