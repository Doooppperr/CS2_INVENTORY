#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
commit=$(git -C "$root" rev-parse HEAD)
output=${1:-"$root/dist/cs2-inventory-$commit.tar.gz"}
mkdir -p "$(dirname "$output")"
git -C "$root" archive --format=tar.gz --output="$output" HEAD
tar -tzf "$output" >/dev/null
sha256sum "$output"
