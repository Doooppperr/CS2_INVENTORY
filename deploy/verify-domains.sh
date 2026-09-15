#!/usr/bin/env bash
set -euo pipefail
check() {
    expected=$1; url=$2; shift 2
    code=$(curl --max-time 20 -sS "$@" -o /dev/null -w '%{http_code}' "$url")
    printf '%s => %s (expected %s)\n' "$url" "$code" "$expected"
    test "$code" = "$expected"
}
for path in / /app /app/monitors/1 /monitors/1 /health /ready /static/theme.js /static/site-footer.css; do
    check 200 "https://cs2inventory.cn$path"
done
for base in http://cs2inventory.cn http://www.cs2inventory.cn https://www.cs2inventory.cn; do
    check 308 "$base/app?page=2&view=monitors"
    location=$(curl --max-time 20 -sSI "$base/app?page=2&view=monitors" | tr -d '\r' | sed -n 's/^[Ll]ocation: //p')
    test "$location" = 'https://cs2inventory.cn/app?page=2&view=monitors'
    echo "LOCATION=$location"
done
for host in cs2inventory.cn www.cs2inventory.cn healthdoc.cn www.healthdoc.cn; do
    for scheme in http https; do
        for path in /cs2_inventory /cs2_inventory/ /cs2_inventory/app /cs2_inventory/api/bootstrap; do
            check 404 "$scheme://$host$path"
        done
    done
done
for path in / /cs2_inventory/ /healthdoc/ /healthdoc/api/health /api/bootstrap; do
    check 404 "http://111.229.87.94$path"
    # Only negative IP tests skip hostname verification; named sites never do.
    check 404 "https://111.229.87.94$path" -k
done
check 404 http://111.229.87.94/ -H 'Host: unknown.invalid'
check 404 https://111.229.87.94/ -k -H 'Host: unknown.invalid'
for host in healthdoc.cn www.healthdoc.cn; do
    check 200 "https://$host/"
    check 200 "https://$host/api/health"
done
html=$(curl --max-time 20 -fsS https://cs2inventory.cn/)
test "$(grep -o '沪ICP备2026034136号-2' <<< "$html" | wc -l)" -eq 1
grep -q 'href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer"' <<< "$html"
echo ICP_FOOTER_OK
cookie=$(curl --max-time 20 -sSI https://cs2inventory.cn/api/bootstrap | grep -i '^set-cookie:')
grep -q 'Secure;' <<< "$cookie"
grep -q 'HttpOnly;' <<< "$cookie"
grep -q 'Path=/;' <<< "$cookie"
grep -q 'SameSite=Lax' <<< "$cookie"
! grep -qi 'Domain=' <<< "$cookie"
echo SECURE_HOST_ONLY_ROOT_COOKIE_OK
echo DOMAIN_MATRIX_OK
