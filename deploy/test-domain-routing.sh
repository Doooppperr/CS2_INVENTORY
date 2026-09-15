#!/usr/bin/env bash
# Run on the server as root. The extra Apache instance binds loopback only.
set -euo pipefail
backup=${1:?cutover backup required}
here=$(cd "$(dirname "$0")" && pwd)
root=$(mktemp -d /tmp/cs2-domain-test.XXXXXX)
install -d "$root/etc/systemd/system" "$root/opt/cs2-inventory" "$root/run" "$root/lock" "$root/log"
cp -a "$backup/apache2" "$root/etc/"
cp -a "$backup/cs2-inventory.env" "$root/etc/"
cp -a "$backup"/cs2-inventory-*.service "$backup"/cs2-inventory-*.timer "$root/etc/systemd/system/"
ln -s "$(cat "$backup/previous-release.txt")" "$root/opt/cs2-inventory/current"
python3 "$here/domain_config.py" stage --root "$root"
grep -qx CS2_COOKIE_PATH=/ "$root/etc/cs2-inventory.env"
test ! -e "$root/etc/apache2/sites-enabled/cs2-inventory.conf"
# Point every Apache include/port/run path at the independent fixture.
configure_fixture() {
python3 - "$root" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
for path in (root / 'etc/apache2').rglob('*'):
    if path.is_symlink() and str(path.readlink()).startswith('/etc/apache2/'):
        target = str(path.readlink()).replace('/etc/apache2', str(root / 'etc/apache2'), 1)
        path.unlink()
        path.symlink_to(target)
for path in (root / 'etc/apache2').rglob('*.conf'):
    if path.is_symlink():
        continue
    content = path.read_text()
    content = content.replace('/etc/apache2', str(root / 'etc/apache2'))
    content = content.replace('<VirtualHost *:80>', '<VirtualHost 127.0.0.1:18080>')
    content = content.replace('<VirtualHost *:443>', '<VirtualHost 127.0.0.1:18443>')
    content = content.replace('Listen 80', 'Listen 127.0.0.1:18080').replace('Listen 443', 'Listen 127.0.0.1:18443')
    path.write_text(content)
PY
}
configure_fixture
export APACHE_RUN_USER=www-data APACHE_RUN_GROUP=www-data
export APACHE_RUN_DIR="$root/run" APACHE_LOCK_DIR="$root/lock" APACHE_LOG_DIR="$root/log" APACHE_PID_FILE="$root/run/apache2.pid"
apache=(/usr/sbin/apache2 -d "$root/etc/apache2" -f "$root/etc/apache2/apache2.conf")
"${apache[@]}" -t
"${apache[@]}" -k start
trap '"${apache[@]}" -k stop 2>/dev/null || true' EXIT
sleep 1
check() {
    expected=$1; host=$3; url=${2/127.0.0.1/$host}
    code=$(curl -ksS --resolve "$host:18080:127.0.0.1" --resolve "$host:18443:127.0.0.1" -o /dev/null -w '%{http_code}' -H "Host: $host" "$url")
    echo "$host $url => $code (expected $expected)"
    test "$code" = "$expected"
}
check 308 http://127.0.0.1:18080/app cs2inventory.cn
check 308 https://127.0.0.1:18443/app www.cs2inventory.cn
check 200 https://127.0.0.1:18443/ cs2inventory.cn
check 200 https://127.0.0.1:18443/api/health healthdoc.cn
for host in cs2inventory.cn www.cs2inventory.cn healthdoc.cn www.healthdoc.cn; do
    check 404 http://127.0.0.1:18080/cs2_inventory/api/bootstrap "$host"
    check 404 https://127.0.0.1:18443/cs2_inventory/ "$host"
done
for host in 111.229.87.94 unknown.invalid; do
    check 404 http://127.0.0.1:18080/healthdoc/ "$host"
    check 404 https://127.0.0.1:18443/ "$host"
done
"${apache[@]}" -k stop
trap - EXIT
python3 "$here/domain_config.py" restore --root "$root" --backup "$backup"
python3 - "$root" "$backup" <<'PY'
from pathlib import Path
import hashlib, sys
root, backup = map(Path, sys.argv[1:])
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
for source in (backup / 'apache2').rglob('*'):
    destination = root / 'etc/apache2' / source.relative_to(backup / 'apache2')
    if source.is_symlink():
        assert destination.is_symlink() and destination.readlink() == source.readlink(), source
    elif source.is_file():
        assert digest(source) == digest(destination), source
assert digest(backup / 'cs2-inventory.env') == digest(root / 'etc/cs2-inventory.env')
for source in backup.glob('cs2-inventory-*'):
    if source.suffix in ('.service', '.timer'):
        assert digest(source) == digest(root / 'etc/systemd/system' / source.name)
assert str((root / 'opt/cs2-inventory/current').readlink()) == (backup / 'previous-release.txt').read_text().strip()
assert not (root / 'etc/apache2/sites-enabled/cs2-domain.conf').exists()
print('ROLLBACK_CONFIG_HASHES_AND_LINKS_RESTORED')
PY
configure_fixture
"${apache[@]}" -t
"${apache[@]}" -k start
trap '"${apache[@]}" -k stop 2>/dev/null || true' EXIT
sleep 1
check 200 http://127.0.0.1:18080/cs2_inventory/ 111.229.87.94
check 200 http://127.0.0.1:18080/healthdoc/ 111.229.87.94
check 200 https://127.0.0.1:18443/api/health healthdoc.cn
"${apache[@]}" -k stop
trap - EXIT
python3 "$here/domain_config.py" restore --root "$root" --backup "$backup"
echo ROLLBACK_BASELINE_BEHAVIOR_RESTORED
echo "ROUTING_AND_ROLLBACK_OK fixture=$root"
