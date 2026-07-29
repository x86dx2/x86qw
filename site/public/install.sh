#!/bin/bash
set -euo pipefail

INSTALLER_VERSION="1.0.8"
INSTALLER_FILE="x86qw-installer-${INSTALLER_VERSION}.zip"
INSTALLER_SHA256="4c5531854dd3282f128c22f3c74547ca1792db4bebebc276d3be6df3cc0acaa3"
INSTALLER_URLS=(
  "https://github.com/x86dx2/x86qw-dist/releases/download/installer-${INSTALLER_VERSION}/${INSTALLER_FILE}"
  "https://gitlab.com/api/v4/projects/84856335/packages/generic/x86qw-installer/${INSTALLER_VERSION}/${INSTALLER_FILE}"
)

fail() {
  printf 'x86QW: %s\n' "$1" >&2
  exit 1
}

command -v python3 >/dev/null 2>&1 || fail "Python 3 é necessário para executar o instalador."
command -v curl >/dev/null 2>&1 || fail "curl não foi encontrado."
command -v unzip >/dev/null 2>&1 || fail "unzip não foi encontrado."

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/x86qw-installer.XXXXXX")
trap 'rm -rf "$work_dir"' EXIT INT TERM
archive="$work_dir/$INSTALLER_FILE"

downloaded=0
for url in "${INSTALLER_URLS[@]}"; do
  printf 'x86QW: baixando instalador %s...\n' "$INSTALLER_VERSION"
  if curl -fL --retry 2 --connect-timeout 15 "$url" -o "$archive"; then
    downloaded=1
    break
  fi
done
[[ "$downloaded" == 1 ]] || fail "nenhum mirror entregou o instalador."

actual=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$archive")
[[ "$actual" == "$INSTALLER_SHA256" ]] || fail "o instalador baixado falhou na verificação SHA-256."

unzip -q "$archive" -d "$work_dir"
root="$work_dir/x86qw-installer-$INSTALLER_VERSION"
python3 "$root/install-qw.py" --online-only "$@"
