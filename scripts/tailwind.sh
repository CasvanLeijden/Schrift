#!/usr/bin/env bash
# Download a pinned, checksum-verified Tailwind CSS standalone CLI into ./bin and run it.
# Usage: scripts/tailwind.sh [--watch] [--minify]
set -euo pipefail

VERSION="4.3.3"
declare -A SHA256=(
  [linux-x64]="dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a"
  [linux-arm64]="55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195"
)

root="$(cd "$(dirname "$0")/.." && pwd)"
case "$(uname -m)" in
  x86_64) platform="linux-x64" ;;
  aarch64 | arm64) platform="linux-arm64" ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

bin="$root/bin/tailwindcss-$VERSION"
if [[ ! -x "$bin" ]]; then
  mkdir -p "$root/bin"
  tmp="$(mktemp)"
  curl -fsSL -o "$tmp" "https://github.com/tailwindlabs/tailwindcss/releases/download/v$VERSION/tailwindcss-$platform"
  echo "${SHA256[$platform]}  $tmp" | sha256sum -c --quiet
  chmod +x "$tmp"
  mv "$tmp" "$bin"
fi

exec "$bin" -i "$root/schrift/static/css/app.css" -o "$root/schrift/static/dist/app.css" "$@"
