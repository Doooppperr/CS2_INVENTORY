#!/usr/bin/env bash
set -euo pipefail
action=${1:?prepare, activate or restore-cutover required}
backup=${2:?absolute backup directory required}
here=$(cd "$(dirname "$0")" && pwd)
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 2; }
[[ "$backup" == /var/backups/cs2-domain-* ]] || { echo "Unexpected backup path" >&2; exit 2; }
units=(cs2-inventory-web.service cs2-inventory-worker.service cs2-inventory-schedule.service cs2-inventory-schedule.timer cs2-inventory-cleanup.service cs2-inventory-cleanup.timer)

restore_cutover() {
    test -f "$backup/previous-release.txt"
    systemctl stop "${units[@]}"
    python3 "$here/domain_config.py" restore --backup "$backup"
    systemctl daemon-reload
    while read -r unit enabled active; do
        if [[ "$enabled" == enabled ]]; then systemctl enable "$unit"; else systemctl disable "$unit"; fi
        if [[ "$active" == active ]]; then systemctl start "$unit"; fi
    done < "$backup/unit-states.txt"
    apache2ctl -t
    systemctl reload apache2
    curl -fsS http://127.0.0.1:5060/ready
    echo CUTOVER_RESTORED
}

case "$action" in
prepare)
    test ! -e "$backup"
    install -d -m 0700 "$backup"
    cp -a /etc/apache2 "$backup/apache2"
    cp -a /etc/cs2-inventory.env "$backup/"
    readlink -f /opt/cs2-inventory/current > "$backup/previous-release.txt"
    for unit in "${units[@]}"; do
        cp -a "/etc/systemd/system/$unit" "$backup/"
        enabled=$(systemctl is-enabled "$unit" || true)
        active=$(systemctl is-active "$unit" || true)
        printf '%s %s %s\n' "$unit" "$enabled" "$active" >> "$backup/unit-states.txt"
    done
    systemctl show healthdoc healthdoc-notifications -p MainPID -p ActiveEnterTimestamp > "$backup/healthdoc-processes.txt"
    install -d -m 0755 /var/www/cs2-acme/.well-known/acme-challenge
    install -m 0644 "$here/apache-cs2-acme.conf" /etc/apache2/sites-available/cs2-domain-acme.conf
    ln -s ../sites-available/cs2-domain-acme.conf /etc/apache2/sites-enabled/cs2-domain-acme.conf
    apache2ctl -t
    systemctl reload apache2
    echo DOMAIN_PREPARED
    ;;
activate)
    test -f "$backup/previous-release.txt"
    test -f /etc/letsencrypt/live/cs2inventory.cn/fullchain.pem
    trap 'code=$?; if [[ $code -ne 0 ]]; then restore_cutover; fi; exit "$code"' EXIT
    python3 "$here/domain_config.py" stage
    apache2ctl -t
    systemctl restart cs2-inventory-web
    ready=0
    for _ in $(seq 1 30); do
        if curl -fsS http://127.0.0.1:5060/ready; then ready=1; break; fi
        sleep 1
    done
    test "$ready" -eq 1
    systemctl reload apache2
    curl -fsS --resolve cs2inventory.cn:443:127.0.0.1 https://cs2inventory.cn/ready
    curl -fsS --resolve healthdoc.cn:443:127.0.0.1 https://healthdoc.cn/api/health
    test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1/cs2_inventory/)" = 404
    test "$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1/healthdoc/)" = 404
    echo DOMAIN_ACTIVATED
    trap - EXIT
    ;;
restore-cutover)
    restore_cutover
    ;;
*) echo "Unknown action" >&2; exit 2 ;;
esac
