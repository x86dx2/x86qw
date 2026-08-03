#!/bin/bash
set -euo pipefail

INSTALLER_VERSION="0.7.1"
INSTALLER_FILE="x86qw-installer-${INSTALLER_VERSION}.zip"
INSTALLER_SHA256="a0946ffcc8a4e1181dbc55ea08caf54691b18b12e901d12069eb2064b38c0d80"
INSTALLER_SIZE="157113"
DOWNLOAD_BUDGET_SECONDS="180"
DOWNLOAD_TRANSFER_SECONDS="120"
DOWNLOAD_ATTEMPTS="3"
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
headers="$work_dir/curl-headers"

download_deadline=$("$python_runtime" - "$DOWNLOAD_BUDGET_SECONDS" <<'PY'
import sys
import time

print(time.monotonic() + float(sys.argv[1]))
PY
)

remaining_budget() {
  "$python_runtime" - "$download_deadline" "${1:-}" <<'PY'
import sys
import time

remaining = float(sys.argv[1]) - time.monotonic()
if remaining <= 0:
    raise SystemExit(1)
if sys.argv[2]:
    remaining = min(remaining, float(sys.argv[2]))
print(f"{remaining:.6f}")
PY
}

wait_before_retry() {
  "$python_runtime" - "$download_deadline" "$1" "${2:-}" <<'PY'
import random
import sys
import time

deadline = float(sys.argv[1])
attempt = int(sys.argv[2])
retry_after = sys.argv[3]
if retry_after:
    delay = float(retry_after)
else:
    base = min(8.0, 0.5 * (2 ** (attempt - 1)))
    delay = base * (0.8 + (0.4 * random.random()))
remaining = deadline - time.monotonic()
if remaining <= 0:
    raise SystemExit(2)
if delay >= remaining:
    raise SystemExit(3)
time.sleep(delay)
PY
}

http_status_from_headers() {
  "$python_runtime" - "$headers" <<'PY'
import os
import sys

status = "000"
path = sys.argv[1]
if os.path.isfile(path):
    with open(path, "rb") as stream:
        for line in stream:
            if line.startswith(b"HTTP/"):
                fields = line.split(None, 2)
                if len(fields) >= 2 and len(fields[1]) == 3 and fields[1].isdigit():
                    status = fields[1].decode("ascii")
print(status)
PY
}

final_header_value() {
  "$python_runtime" - "$headers" "$1" <<'PY'
import os
import sys

path, expected_name = sys.argv[1:]
blocks = []
current = None
if os.path.isfile(path):
    with open(path, "rb") as stream:
        for raw_line in stream:
            line = raw_line.rstrip(b"\r\n")
            if line.startswith(b"HTTP/"):
                current = []
                blocks.append(current)
            elif current is not None:
                current.append(line)
if not blocks:
    print("__missing__")
    raise SystemExit
values = []
invalid = False
for line in blocks[-1]:
    if not line:
        continue
    if line[:1] in (b" ", b"\t") or b":" not in line:
        invalid = True
        continue
    name, value = line.split(b":", 1)
    if name.strip().lower() == expected_name.encode("ascii").lower():
        try:
            values.append(value.strip().decode("ascii"))
        except UnicodeDecodeError:
            invalid = True
if invalid or len(values) > 1:
    print("__invalid__")
elif not values:
    print("__missing__")
else:
    print(values[0])
PY
}

content_length_from_headers() {
  value=$(final_header_value "Content-Length")
  case "$value" in
    __missing__)
      printf '%s\n' "missing"
      ;;
    __invalid__|''|*[!0-9]*)
      printf '%s\n' "invalid"
      ;;
    *)
      printf '%s\n' "$value"
      ;;
  esac
}

retry_after_from_headers() {
  value=$(final_header_value "Retry-After")
  "$python_runtime" - "$value" <<'PY'
import email.utils
import sys
import time
from datetime import timezone

value = sys.argv[1].strip()
if value in {"", "__missing__", "__invalid__"}:
    raise SystemExit
if len(value) <= 20 and value.isascii() and value.isdecimal():
    print(float(value))
    raise SystemExit
try:
    parsed = email.utils.parsedate_to_datetime(value)
except (TypeError, ValueError, OverflowError):
    raise SystemExit
if parsed is None:
    raise SystemExit
if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)
print(max(0.0, parsed.timestamp() - time.time()))
PY
}

is_transient_curl_failure() {
  case "$1" in
    5|6|7|16|18|28|35|52|55|56|92)
      return 0
      ;;
    22)
      status=$(http_status_from_headers)
      case "$status" in
        408|425|429|500|502|503|504)
          return 0
          ;;
      esac
      ;;
  esac
  return 1
}

receive_archive() {
  "$python_runtime" /dev/fd/3 "$archive" "$INSTALLER_SIZE" "$INSTALLER_SHA256" \
    "$download_deadline" 3<<'PY'
import hashlib
import os
import sys
import tempfile
import time

destination, encoded_size, expected_sha256, encoded_deadline = sys.argv[1:]
expected_size = int(encoded_size)
deadline = float(encoded_deadline)
descriptor, temporary_name = tempfile.mkstemp(
    prefix=".x86qw-bootstrap-", dir=os.path.dirname(destination),
)
if os.name != "nt":
    os.fchmod(descriptor, 0o600)

digest = hashlib.sha256()
received = 0
try:
    with os.fdopen(descriptor, "wb") as output:
        while True:
            block = sys.stdin.buffer.read(min(64 * 1024, expected_size - received + 1))
            if not block:
                break
            received += len(block)
            if received > expected_size:
                raise ValueError("resposta maior que o limite permitido")
            digest.update(block)
            output.write(block)
        if received != expected_size:
            raise ValueError("tamanho do instalador divergente")
        if digest.hexdigest() != expected_sha256:
            raise ValueError("SHA-256 do instalador divergente")
        output.flush()
        os.fsync(output.fileno())
    if time.monotonic() >= deadline:
        raise TimeoutError("prazo total do download excedido")
    os.replace(temporary_name, destination)
except BaseException as error:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
    print(f"x86QW: {error}", file=sys.stderr)
    raise SystemExit(1)
PY
}

downloaded=0
budget_exhausted=0
for url in "${INSTALLER_URLS[@]}"; do
  attempt=1
  while (( attempt <= DOWNLOAD_ATTEMPTS )); do
    if ! remaining_seconds=$(remaining_budget "$DOWNLOAD_TRANSFER_SECONDS"); then
      budget_exhausted=1
      break 2
    fi
    rm -f "$archive" "$headers"
    printf 'x86QW: baixando instalador %s (tentativa %d/%d)...\n' \
      "$INSTALLER_VERSION" "$attempt" "$DOWNLOAD_ATTEMPTS"
    if curl --disable --fail --location \
      --proto '=https' --proto-redir '=https' \
      --connect-timeout 15 --max-time "$remaining_seconds" \
      --dump-header "$headers" \
      "$url" | receive_archive; then
      pipeline_status=(0 0)
    else
      pipeline_status=("${PIPESTATUS[@]}")
    fi
    curl_status="${pipeline_status[0]:-1}"
    receiver_status="${pipeline_status[1]:-1}"

    if [[ "$curl_status" == 0 && "$receiver_status" == 0 ]]; then
      declared_length=$(content_length_from_headers)
      if [[ "$declared_length" != "missing" && "$declared_length" != "$INSTALLER_SIZE" ]]; then
        printf 'x86QW: Content-Length do instalador é inválido ou divergente.\n' >&2
        receiver_status=1
      elif remaining_budget >/dev/null; then
        downloaded=1
        break 2
      fi
      if [[ "$receiver_status" == 0 ]]; then
        rm -f "$archive"
        budget_exhausted=1
        break 2
      fi
    fi

    rm -f "$archive"
    if (( attempt < DOWNLOAD_ATTEMPTS )) && is_transient_curl_failure "$curl_status"; then
      retry_after=$(retry_after_from_headers)
      if wait_before_retry "$attempt" "$retry_after"; then
        (( attempt += 1 ))
        continue
      else
        wait_status=$?
      fi
      if [[ "$wait_status" == 2 ]]; then
        budget_exhausted=1
        break 2
      fi
      break
    fi
    break
  done
  printf 'x86QW: mirror rejeitado por indisponibilidade ou integridade: %s\n' "$url" >&2
done
[[ "$budget_exhausted" == 0 ]] || fail "o prazo total para baixar o instalador foi excedido."
[[ "$downloaded" == 1 ]] || fail "nenhum mirror entregou um instalador íntegro."

unzip -q "$archive" -d "$work_dir"
root="$work_dir/x86qw-installer-$INSTALLER_VERSION"
"$python_runtime" "$root/x86qw.pyz" --online-only "$@"
