#!/usr/bin/env bash
set -euo pipefail

echo "== systemd failed =="
systemctl --no-pager --failed || true

echo
echo "== EnergyDash services =="
systemctl status energydash-collector energydash-api energydash-web energydash-alert energydash-daily-update.timer --no-pager || true

echo
echo "== API health =="
curl -s http://127.0.0.1:8080/api/health | jq . || true

echo
echo "== PostgreSQL quick check =="
runuser -u postgres -- psql -d energydash -c "SELECT COUNT(*) AS measures FROM measures_raw;" || true
runuser -u postgres -- psql -d energydash -c "SELECT MIN(day), MAX(day), COUNT(*) FROM energy_daily;" || true

echo
echo "== Porte =="
ss -ltnp | grep -E ':80|:8050|:8080' || true

echo
echo "== Ultimi log API =="
journalctl -u energydash-api -n 30 --no-pager || true

echo
echo "== Ultimi log WEB =="
journalctl -u energydash-web -n 30 --no-pager || true

echo
echo "== Ultimi log collector =="
journalctl -u energydash-collector -n 30 --no-pager || true

echo
echo "== Ultimi log alert =="
journalctl -u energydash-alert -n 30 --no-pager || true


echo
echo "== Utente energydash e gruppi =="
id energydash || true
groups energydash || true

echo
echo "== Test accesso journal come energydash =="
runuser -u energydash -- journalctl -u energydash-web.service -n 3 --no-pager || true

