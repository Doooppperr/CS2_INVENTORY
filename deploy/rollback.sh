#!/usr/bin/env bash
set -euo pipefail
target=${1:?previous release path required}
backup=${2:?pre-deploy backup directory required}
test -d "$target"
test -d "$backup"
systemctl stop cs2-inventory-web cs2-inventory-worker cs2-inventory-schedule.timer
systemctl stop cs2-inventory-schedule.service cs2-inventory-cleanup.timer cs2-inventory-cleanup.service >/dev/null 2>&1 || true
test -f "$backup/cs2_inventory.db"
rm -f /var/lib/cs2-inventory/cs2_inventory.db-wal /var/lib/cs2-inventory/cs2_inventory.db-shm
install -o cs2inventory -g cs2inventory -m 0640 "$backup/cs2_inventory.db" /var/lib/cs2-inventory/cs2_inventory.db
for unit in web worker schedule.service schedule.timer cleanup.service cleanup.timer; do
  src="$backup/cs2-inventory-$unit"
  if [[ -f "$src" ]]; then
    install -o root -g root -m 0644 "$src" "/etc/systemd/system/cs2-inventory-$unit"
  else
    rm -f "/etc/systemd/system/cs2-inventory-$unit"
  fi
done
ln -sfn "$target" /opt/cs2-inventory/current.rollback
mv -Tf /opt/cs2-inventory/current.rollback /opt/cs2-inventory/current
systemctl daemon-reload
systemctl enable cs2-inventory-web cs2-inventory-worker cs2-inventory-schedule.timer
systemctl restart cs2-inventory-web cs2-inventory-worker
systemctl start cs2-inventory-schedule.timer
curl -fsS http://127.0.0.1:5060/ready
