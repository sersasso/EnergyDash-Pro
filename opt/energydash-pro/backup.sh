#!/usr/bin/env bash
set -euo pipefail
CONFIG="/etc/energydash-pro/config.yaml"
BACKUP_DIR="/var/lib/energydash-pro/backup"
STAMP="$(date +%Y%m%d_%H%M%S)"
DB_NAME="energydash"
mkdir -p "$BACKUP_DIR"
pg_dump "$DB_NAME" | gzip > "$BACKUP_DIR/energydash_db_$STAMP.sql.gz"
tar -czf "$BACKUP_DIR/energydash_config_$STAMP.tar.gz" /etc/energydash-pro /etc/systemd/system/energydash-* 2>/dev/null || true
find "$BACKUP_DIR" -type f -mtime +30 -delete
echo "Backup completato in $BACKUP_DIR"
