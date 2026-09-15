#!/usr/bin/env bash
set -euo pipefail
if [[ "${RENEWED_LINEAGE:-}" == /etc/letsencrypt/live/cs2inventory.cn ]]; then
    apache2ctl -t
    systemctl reload apache2
fi
