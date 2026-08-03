#!/bin/bash
set -euo pipefail

INSTALLER_VERSION="0.7.1"
INSTALLER_FILE="x86qw-installer-${INSTALLER_VERSION}.zip"
INSTALLER_SHA256="a0946ffcc8a4e1181dbc55ea08caf54691b18b12e901d12069eb2064b38c0d80"
INSTALLER_URLS=(
  "https://github.com/x86dx2/x86qw/releases/download/x86qw-installer-${INSTALLER_VERSION}/${INSTALLER_FILE}"
  "https://gitlab.com/api/v4/projects/84813414/packages/generic/x86qw-installer/${INSTALLER_VERSION}/${INSTALLER_FILE}"
)

fail() {
  printf 'x86QW: %s\n' "$1" >&2
  exit 1
}

resolve_python() {
  for candidate in python3 python; do
    resolved=$(command -v "$candidate" 2>/dev/null) || continue
    if "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      printf '%s\n' "$resolved"
      return 0
    fi
  done
  return 1
}

python_runtime=$(resolve_python) || fail "Python 3.10 ou mais recente não foi encontrado. Instale-o e execute novamente."
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

actual=$("$python_runtime" - "$archive" <<'PY'
import hashlib
import sys

digest = hashlib.sha256()
with open(sys.argv[1], "rb") as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
print(digest.hexdigest())
PY
)
[[ "$actual" == "$INSTALLER_SHA256" ]] || fail "o instalador baixado falhou na verificação SHA-256."

unzip -q "$archive" -d "$work_dir"
root="$work_dir/x86qw-installer-$INSTALLER_VERSION"
"$python_runtime" "$root/x86qw.pyz" --online-only "$@"
