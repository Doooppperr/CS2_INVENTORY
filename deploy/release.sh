#!/usr/bin/env bash
set -euo pipefail

archive=${1:?archive required}
commit=${2:?commit required}
base=/opt/cs2-inventory
release="$base/releases/$commit"
current="$base/current"
old=$(readlink -f "$current" 2>/dev/null || true)
state=/var/lib/cs2-inventory
backup="$state/pre-deploy-$commit"
legacy_active=$(systemctl is-active cs2-inventory.service 2>/dev/null || true)

rollback() {
  code=$?
  if [[ $code -ne 0 ]]; then
    systemctl stop cs2-inventory-web cs2-inventory-worker >/dev/null 2>&1 || true
    if [[ -n "$old" && -d "$old" ]]; then ln -sfn "$old" "$current.rollback"; mv -Tf "$current.rollback" "$current"; fi
    if [[ -f "$backup/cs2_inventory.db" ]]; then cp -f "$backup/cs2_inventory.db" "$state/cs2_inventory.db"; chown cs2inventory:cs2inventory "$state/cs2_inventory.db"; fi
    for unit in web worker schedule.service schedule.timer; do
      src="$backup/cs2-inventory-$unit"
      [[ -f "$src" ]] && cp -f "$src" "/etc/systemd/system/cs2-inventory-$unit"
    done
    systemctl daemon-reload
    if [[ "$legacy_active" == "active" ]]; then
      systemctl enable --now cs2-inventory.service || true
    else
      systemctl restart cs2-inventory-web cs2-inventory-worker || true
    fi
    echo "ROLLBACK old=$old exit=$code" >&2
  fi
  exit "$code"
}
trap rollback EXIT

install -d -o cs2inventory -g cs2inventory -m 0750 "$state"
install -d -o root -g root -m 0755 "$backup" "$release"
[[ -f "$state/cs2_inventory.db" ]] && cp -a "$state/cs2_inventory.db" "$backup/cs2_inventory.db"
for unit in web worker schedule.service schedule.timer; do
  src="/etc/systemd/system/cs2-inventory-$unit"
  [[ -f "$src" ]] && cp -a "$src" "$backup/cs2-inventory-$unit"
done

tar -xzf "$archive" -C "$release"
chown -R root:root "$release"
python3 -m venv "$base/venv"
"$base/venv/bin/pip" install --disable-pip-version-check -q -r "$release/requirements.txt"
PYTHONPATH="$release/src" CS2_STATE_DIR="$state" INVENTORY_OBSERVATION_CACHE="$state/observations.json" \
  "$base/venv/bin/python" -m cs2_inventory.cli init-db
chown -R cs2inventory:cs2inventory "$state"

install -o root -g root -m 0644 "$release/deploy/cs2-inventory-web.service" /etc/systemd/system/
install -o root -g root -m 0644 "$release/deploy/cs2-inventory-worker.service" /etc/systemd/system/
install -o root -g root -m 0644 "$release/deploy/cs2-inventory-schedule.service" /etc/systemd/system/
install -o root -g root -m 0644 "$release/deploy/cs2-inventory-schedule.timer" /etc/systemd/system/
ln -sfn "$release" "$current.next"
mv -Tf "$current.next" "$current"
systemctl daemon-reload
systemctl disable --now cs2-inventory.service >/dev/null 2>&1 || true
systemctl enable --now cs2-inventory-web cs2-inventory-worker cs2-inventory-schedule.timer

healthy=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:5060/ready >/tmp/cs2-ready.json; then healthy=1; break; fi
  sleep 1
done
test "$healthy" -eq 1
cat /tmp/cs2-ready.json; echo
systemctl is-active cs2-inventory-web cs2-inventory-worker
systemctl is-enabled cs2-inventory-schedule.timer
echo "DEPLOYED_COMMIT=$commit"
echo "OLD_RELEASE=$old"
echo "NEW_RELEASE=$release"
trap - EXIT
