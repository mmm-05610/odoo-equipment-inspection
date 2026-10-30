#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
revision=635f19675e4f606bd719c893175a65305ad562c6
dest="$root/vendor/quality_control_oca"
if [[ -d "$dest" ]]; then
  echo 'Dependency directory already exists; verify vendor/OCA_REVISION before rebuilding.'
  test "$(cat "$root/vendor/OCA_REVISION")" = "$revision"
  exit 0
fi
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git -C "$tmp" init -q
git -C "$tmp" fetch --depth 1 https://github.com/OCA/manufacture.git "$revision"
mkdir -p "$dest"
git -C "$tmp" archive FETCH_HEAD quality_control_oca | tar -x -C "$dest" --strip-components=1
printf '%s\n' "$revision" > "$root/vendor/OCA_REVISION"
