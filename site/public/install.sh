#!/bin/sh
# x86QW bootstrap: downloads the native x86QW CLI pinned by size and SHA-256
# and runs `x86qw setup`. No Python required. The body only runs from the
# last line, so a truncated download does nothing.
set -eu

x86qw_install_main() {
VERSION="2.4.1"
URL_BASE="https://github.com/x86dx2/x86qw/releases/download/x86qw-cli-${VERSION}"
PROTOCOLS="=https"
# Test hook: a local mirror of the same pinned files (still size+SHA-256 checked).
if [ -n "${X86QW_BOOTSTRAP_MIRROR:-}" ]; then URL_BASE=$X86QW_BOOTSTRAP_MIRROR; PROTOCOLS="=https,file"; fi

case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) FILE="x86qw-${VERSION}-darwin-arm64"; SHA256="a172b77474dbdd0b8164f203150dc671464575716c609ac8d540e6f52656bd40"; SIZE="13270066" ;;
  Darwin/x86_64) FILE="x86qw-${VERSION}-darwin-amd64"; SHA256="fbde646facfcd212144f64f29063a536a9501d0affce78c6f4b7e358e6456b85"; SIZE="14343120" ;;
  Linux/x86_64|Linux/amd64) FILE="x86qw-${VERSION}-linux-amd64"; SHA256="5dd29607123bd8d556e3b902b9f342ddc61f40b3123c48775788120523cdc8e4"; SIZE="14045344" ;;
  Linux/aarch64|Linux/arm64) FILE="x86qw-${VERSION}-linux-arm64"; SHA256="01968a7031effdaad9e1b5120a7fbab352c1cb1c5b8b58b859b35dc8d2fdf8c2"; SIZE="12976288" ;;
  *) echo "x86QW: unsupported system: $(uname -s)/$(uname -m)" >&2; exit 1 ;;
esac

if command -v sha256sum >/dev/null 2>&1; then
  digest() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
  digest() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
  echo "x86QW: sha256sum or shasum is required to verify the download." >&2; exit 1
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/x86qw-bootstrap.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM
binary="$work/x86qw"

echo "· Downloading x86QW ${VERSION} (${FILE})"
curl --proto "$PROTOCOLS" --tlsv1.2 -fsSL --retry 3 --retry-all-errors --connect-timeout 15 --max-time 300 \
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
