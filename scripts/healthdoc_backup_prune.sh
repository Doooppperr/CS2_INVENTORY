#!/usr/bin/env bash
set -euo pipefail

root=/var/backups/healthdoc
stamp=$(date -u +%Y%m%dT%H%M%SZ)
fresh="$root/$stamp"
current=$(readlink -f /opt/healthdoc/current)
test -d "$current"
install -d -o root -g root -m 0700 "$fresh"

restart_healthdoc() {
  docker start healthdoc-gaussdb >/dev/null 2>&1 || true
  for _ in $(seq 1 40); do docker exec healthdoc-gaussdb pg_isready >/dev/null 2>&1 && break; sleep 2; done
  systemctl start healthdoc healthdoc-notifications || true
}
trap restart_healthdoc EXIT

curl -fsS -H 'Host: healthdoc.cn' http://127.0.0.1/api/health >/dev/null 2>&1 || curl -fsS https://healthdoc.cn/ >/dev/null
systemctl stop healthdoc healthdoc-notifications
docker stop -t 60 healthdoc-gaussdb >/dev/null

tar -C "$(dirname "$current")" -czf "$fresh/current-release.tar.gz.partial" "$(basename "$current")"
tar -C /var/lib/healthdoc -czf "$fresh/uploads.tar.gz.partial" uploads
tar -C /var/lib/healthdoc -czf "$fresh/opengauss.tar.gz.partial" opengauss
for name in current-release uploads opengauss; do
  tar -tzf "$fresh/$name.tar.gz.partial" >/dev/null
  mv "$fresh/$name.tar.gz.partial" "$fresh/$name.tar.gz"
done
cp -a /etc/healthdoc/healthdoc.env "$fresh/healthdoc.env"
cp -a /etc/apache2/sites-available/healthdoc.conf "$fresh/healthdoc.conf"
cp -a /etc/apache2/sites-available/healthdoc-le-ssl.conf "$fresh/healthdoc-le-ssl.conf"
sha256sum "$fresh"/*.tar.gz >"$fresh/SHA256SUMS"
sha256sum -c "$fresh/SHA256SUMS"

restart_healthdoc
trap - EXIT
curl -fsS https://healthdoc.cn/ >/dev/null
systemctl is-active healthdoc healthdoc-notifications apache2
docker inspect -f '{{.State.Running}}' healthdoc-gaussdb | grep -qx true

mapfile -t backups < <(find "$root" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | grep -E '^20[0-9]{6}T[0-9]{6}Z$' | sort)
remove_count=$((${#backups[@]} - 6))
if (( remove_count > 0 )); then
  for ((i=0; i<remove_count; i++)); do
    candidate="$root/${backups[$i]}"
    resolved=$(readlink -f "$candidate")
    [[ "$resolved" == "$root/"20* ]] || { echo "unsafe backup path: $resolved" >&2; exit 1; }
    rm -rf --one-file-system -- "$resolved"
    echo "REMOVED_BACKUP=$resolved"
  done
fi

apt-get clean
rm -rf --one-file-system /root/.cache/pip
journalctl --vacuum-size=150M
echo "FRESH_BACKUP=$fresh"
du -sh "$root" "$fresh"
df -h /
