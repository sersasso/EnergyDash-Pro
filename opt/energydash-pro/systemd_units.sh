#!/usr/bin/env bash
set -euo pipefail

cat >/etc/systemd/system/energydash-collector.service <<'EOF'
[Unit]
Description=EnergyDash Pro Shelly Collector
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/energydash-pro
ExecStart=/opt/energydash-pro/venv/bin/python /opt/energydash-pro/collector.py
Restart=always
RestartSec=5
User=energydash
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/energydash-api.service <<'EOF'
[Unit]
Description=EnergyDash Pro API
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/energydash-pro
ExecStart=/opt/energydash-pro/venv/bin/uvicorn main_api:app --host 127.0.0.1 --port 8080
Restart=always
RestartSec=5
User=energydash
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/energydash-web.service <<'EOF'
[Unit]
Description=EnergyDash Pro Web Dashboard
After=network-online.target postgresql.service energydash-api.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/energydash-pro
ExecStart=/opt/energydash-pro/venv/bin/python /opt/energydash-pro/dashboard.py
Restart=always
RestartSec=5
User=energydash
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/energydash-alert.service <<'EOF'
[Unit]
Description=EnergyDash Pro Alert Worker
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/energydash-pro
ExecStart=/opt/energydash-pro/venv/bin/python /opt/energydash-pro/alert_worker.py
Restart=always
RestartSec=10
User=energydash
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/energydash-daily-update.service <<'EOF'
[Unit]
Description=EnergyDash Pro Daily Aggregation
After=network-online.target postgresql.service

[Service]
Type=oneshot
WorkingDirectory=/opt/energydash-pro
ExecStart=/opt/energydash-pro/venv/bin/python /opt/energydash-pro/aggregate_daily.py --days 14
User=energydash
Environment=PYTHONUNBUFFERED=1
EOF

cat >/etc/systemd/system/energydash-daily-update.timer <<'EOF'
[Unit]
Description=Run EnergyDash Pro Daily Aggregation periodically

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true
Unit=energydash-daily-update.service

[Install]
WantedBy=timers.target
EOF

cat >/etc/systemd/system/energydash-report.service <<'EOF'
[Unit]
Description=EnergyDash Pro Report Generation
After=network-online.target postgresql.service

[Service]
Type=oneshot
WorkingDirectory=/opt/energydash-pro
ExecStart=/opt/energydash-pro/venv/bin/python /opt/energydash-pro/reporting.py
User=energydash
Environment=PYTHONUNBUFFERED=1
EOF

cat >/etc/systemd/system/energydash-report.timer <<'EOF'
[Unit]
Description=Run EnergyDash Pro monthly report

[Timer]
OnCalendar=monthly
Persistent=true
Unit=energydash-report.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable energydash-collector energydash-api energydash-web energydash-alert energydash-daily-update.timer energydash-report.timer

echo "Unit systemd create e abilitate. Avviare con: systemctl start energydash-collector energydash-api energydash-web energydash-alert energydash-daily-update.timer"
