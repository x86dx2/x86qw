#!/bin/sh
# x86QW bootstrap: downloads the native x86QW CLI pinned by size and SHA-256
# and runs `x86qw setup`. No Python required. The body only runs from the
# last line, so a truncated download does nothing.
set -eu

x86qw_install_main() {
VERSION="{{.Version}}"
URL_BASE="https://github.com/x86dx2/x86qw/releases/download/x86qw-cli-${VERSION}"
PROTOCOLS="=https"
# Test hook: a local mirror of the same pinned files (still size+SHA-256 checked).
if [ -n "${X86QW_BOOTSTRAP_MIRROR:-}" ]; then URL_BASE=$X86QW_BOOTSTRAP_MIRROR; PROTOCOLS="=https,file"; fi

case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) FILE="x86qw-${VERSION}-darwin-arm64"; SHA256="{{index .SHA256 "darwin-arm64"}}"; SIZE="{{index .Size "darwin-arm64"}}" ;;
  Darwin/x86_64) FILE="x86qw-${VERSION}-darwin-amd64"; SHA256="{{index .SHA256 "darwin-amd64"}}"; SIZE="{{index .Size "darwin-amd64"}}" ;;
  Linux/x86_64|Linux/amd64) FILE="x86qw-${VERSION}-linux-amd64"; SHA256="{{index .SHA256 "linux-amd64"}}"; SIZE="{{index .Size "linux-amd64"}}" ;;
  Linux/aarch64|Linux/arm64) FILE="x86qw-${VERSION}-linux-arm64"; SHA256="{{index .SHA256 "linux-arm64"}}"; SIZE="{{index .Size "linux-arm64"}}" ;;
  *) echo "x86QW: unsupported system: $(uname -s)/$(uname -m)" >&2; exit 1 ;;
esac

if command -v sha256sum >/dev/null 2>&1; then
  digest() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
  digest() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
  echo "x86QW: sha256sum or shasum is required to verify the download." >&2; exit 1
fi

# Some Linux systems mount /tmp noexec: there the binary runs from the user's cache.
parent="${TMPDIR:-/tmp}"
if [ "$(uname -s)" = Linux ]; then parent="${XDG_CACHE_HOME:-$HOME/.cache}"; mkdir -p "$parent"; fi
work=$(mktemp -d "$parent/x86qw-bootstrap.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM
binary="$work/x86qw"

echo "· Downloading x86QW ${VERSION} (${FILE})"
# No deadline, so a slow link finishes; a stalled one fails and retries.
curl --proto "$PROTOCOLS" --tlsv1.2 -fsSL --retry 3 --retry-all-errors --connect-timeout 15 --speed-limit 1024 --speed-time 60 \
  -o "$binary" "${URL_BASE}/${FILE}"

actual_size=$(wc -c < "$binary" | tr -d ' ')
if [ "$actual_size" != "$SIZE" ]; then
  echo "x86QW: unexpected size (${actual_size}, expected ${SIZE}); nothing was installed." >&2; exit 1
fi
if [ "$(digest "$binary")" != "$SHA256" ]; then
  echo "x86QW: SHA-256 mismatch; nothing was installed." >&2; exit 1
fi
chmod 0755 "$binary"
echo "✓ Download verified"

if (exec < /dev/tty) 2>/dev/null; then
  "$binary" setup "$@" < /dev/tty
else
  "$binary" setup "$@"
fi
}

x86qw_install_main "$@"
