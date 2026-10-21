#!/bin/sh
# x86QW bootstrap: downloads the native x86QW CLI pinned by size and SHA-256
# and runs `x86qw setup`. No Python required. The body only runs from the
# last line, so a truncated download does nothing.
set -eu

x86qw_install_main() {
VERSION="2.5.0"
URL_BASE="https://github.com/x86dx2/x86qw/releases/download/x86qw-cli-${VERSION}"
PROTOCOLS="=https"
# Test hook: a local mirror of the same pinned files (still size+SHA-256 checked).
if [ -n "${X86QW_BOOTSTRAP_MIRROR:-}" ]; then URL_BASE=$X86QW_BOOTSTRAP_MIRROR; PROTOCOLS="=https,file"; fi

case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) FILE="x86qw-${VERSION}-darwin-arm64"; SHA256="1c621cf3f4304b9a8c4b3ed68407c9c87bfe20b694bf3c38aa72d4539782c797"; SIZE="13286610" ;;
  Darwin/x86_64) FILE="x86qw-${VERSION}-darwin-amd64"; SHA256="8514ccdb4aa64e6166968052747f593b03b939a1bd60c3edd1bea95e5e7c9889"; SIZE="14355440" ;;
  Linux/x86_64|Linux/amd64) FILE="x86qw-${VERSION}-linux-amd64"; SHA256="a7beedd46b6cc1b998278277b472b1a86dcbdfd993cdf3a2f40963cb67c1167f"; SIZE="14057632" ;;
  Linux/aarch64|Linux/arm64) FILE="x86qw-${VERSION}-linux-arm64"; SHA256="33fb583b6c6e4470b230af27da034a9daa979417886ad3c09c70e70973150e12"; SIZE="12976288" ;;
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
