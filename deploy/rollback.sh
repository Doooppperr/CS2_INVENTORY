#!/usr/bin/env bash
set -euo pipefail
target=${1:?previous release path required}
test -d "$target"
ln -sfn "$target" /opt/cs2-inventory/current.rollback
mv -Tf /opt/cs2-inventory/current.rollback /opt/cs2-inventory/current
systemctl restart cs2-inventory-web cs2-inventory-worker
curl -fsS http://127.0.0.1:5060/ready
