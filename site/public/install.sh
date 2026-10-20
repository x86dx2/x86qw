#!/bin/sh
# x86QW bootstrap: downloads the native x86QW CLI pinned by size and SHA-256
# and runs `x86qw setup`. No Python required. The body only runs from the
# last line, so a truncated download does nothing.
set -eu

x86qw_install_main() {
VERSION="2.1.0"
URL_BASE="https://github.com/x86dx2/x86qw/releases/download/x86qw-cli-${VERSION}"
PROTOCOLS="=https"
# Test hook: a local mirror of the same pinned files (still size+SHA-256 checked).
if [ -n "${X86QW_BOOTSTRAP_MIRROR:-}" ]; then URL_BASE=$X86QW_BOOTSTRAP_MIRROR; PROTOCOLS="=https,file"; fi

case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) FILE="x86qw-${VERSION}-darwin-arm64"; SHA256="58b9056a9c6372849163dd2ef32c453a0b2d56ac0db736954fcf9e5760ecf106"; SIZE="14921842" ;;
  Darwin/x86_64) FILE="x86qw-${VERSION}-darwin-amd64"; SHA256="75c483bc5ed2d8d472a0892fa9899520dce133ef45fc415b6acebb907ea77308"; SIZE="16064032" ;;
  Linux/x86_64|Linux/amd64) FILE="x86qw-${VERSION}-linux-amd64"; SHA256="2ace8b47653bddb998208f3d30a53ac8916d9dda896c44fa95a9a4b0dee6dbb4"; SIZE="15745184" ;;
  Linux/aarch64|Linux/arm64) FILE="x86qw-${VERSION}-linux-arm64"; SHA256="7b07c748e83377dc863db018b255c77b00a3f820fc5250013fe94cdd8a925181"; SIZE="14549152" ;;
  *) echo "x86QW: sistema não suportado: $(uname -s)/$(uname -m)" >&2; exit 1 ;;
esac

if command -v sha256sum >/dev/null 2>&1; then
  digest() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
  digest() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
  echo "x86QW: sha256sum ou shasum é necessário para verificar o download." >&2; exit 1
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/x86qw-bootstrap.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM
binary="$work/x86qw"

echo "· Baixando x86QW ${VERSION} (${FILE})"
curl --proto "$PROTOCOLS" --tlsv1.2 -fsSL --retry 3 --retry-all-errors --connect-timeout 15 --max-time 300 \
  -o "$binary" "${URL_BASE}/${FILE}"

actual_size=$(wc -c < "$binary" | tr -d ' ')
if [ "$actual_size" != "$SIZE" ]; then
  echo "x86QW: tamanho inesperado (${actual_size}, esperado ${SIZE}); nada foi instalado." >&2; exit 1
fi
if [ "$(digest "$binary")" != "$SHA256" ]; then
  echo "x86QW: SHA-256 divergente; nada foi instalado." >&2; exit 1
fi
chmod 0755 "$binary"
echo "✓ Download verificado"

if (exec < /dev/tty) 2>/dev/null; then
  "$binary" setup "$@" < /dev/tty
else
  "$binary" setup "$@"
fi
}

x86qw_install_main "$@"
