#!/bin/bash
set -euo pipefail

INSTALLER_VERSION="0.1.20"
INSTALLER_FILE="x86qw-installer-${INSTALLER_VERSION}.zip"
INSTALLER_SHA256="d30a836693100869e72a56df3d14aa8715bf111a4f1350948f3d34be04134304"
INSTALLER_URLS=(
  "https://github.com/x86dx2/x86qw/releases/download/x86qw-installer-${INSTALLER_VERSION}/${INSTALLER_FILE}"
  "https://gitlab.com/api/v4/projects/84813414/packages/generic/x86qw-installer/${INSTALLER_VERSION}/${INSTALLER_FILE}"
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
python3 "$root/x86qw.pyz" --online-only "$@"
