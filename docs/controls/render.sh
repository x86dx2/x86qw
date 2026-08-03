#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CHROME=${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}

if [ ! -x "$CHROME" ]; then
  echo "Google Chrome não encontrado em: $CHROME" >&2
  exit 1
fi

mkdir -p "$SCRIPT_DIR/generated"

BROWSER_DATA=$(mktemp -d "${TMPDIR:-/tmp}/x86qw-control-maps.XXXXXX")
cleanup_browser_data() {
  rm -rf -- "$BROWSER_DATA"
}
trap cleanup_browser_data EXIT HUP INT TERM

rendered=$(python3 - "$CHROME" "$BROWSER_DATA/validate-all" "file://$SCRIPT_DIR/index.html?profile=ktx&layout=windows-ansi&validate=all" <<'PY'
import subprocess
import sys

chrome, browser_data, url = sys.argv[1:]
command = [
    chrome,
    "--headless=new",
    "--hide-scrollbars",
    "--disable-background-mode",
    "--disable-background-networking",
    "--disable-extensions",
    "--disable-gpu",
    "--no-first-run",
    "--force-device-scale-factor=1",
    "--window-size=1920,1080",
    f"--user-data-dir={browser_data}",
    "--dump-dom",
    url,
]
try:
    result = subprocess.run(command, capture_output=True, timeout=12, check=False)
    payload = result.stdout
    if result.returncode:
        sys.stderr.buffer.write(result.stderr)
        raise SystemExit(result.returncode)
except subprocess.TimeoutExpired as error:
    payload = error.stdout or b""
sys.stdout.buffer.write(payload)
PY
)
for marker in \
  'data-control-map-valid="true"' \
  'data-control-map-geometry-valid="true"' \
  'data-control-map-descriptions-valid="true"' \
  'data-control-map-all-profiles-valid="true"'
do
  case "$rendered" in
    *"$marker"*) ;;
    *)
      echo "Geometria do mapa de controles inválida ou bind sem descrição: $marker" >&2
      exit 1
      ;;
  esac
done

for profile in ktx final-arena pro-x team-fortress td2; do
  for layout in windows-ansi macos-en-us keychron-k3-v3; do

    if [ "$layout" = windows-ansi ]; then
      output="$SCRIPT_DIR/generated/$profile.png"
    else
      output="$SCRIPT_DIR/generated/$profile--$layout.png"
    fi
    python3 - "$CHROME" "$BROWSER_DATA/$profile-$layout-render" "$output" "file://$SCRIPT_DIR/index.html?profile=$profile&layout=$layout" <<'PY'
import os
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

chrome, browser_data, output_name, url = sys.argv[1:]
output = Path(output_name)
output.unlink(missing_ok=True)
command = [
    chrome,
    "--headless=new",
    "--hide-scrollbars",
    "--disable-background-mode",
    "--disable-background-networking",
    "--disable-extensions",
    "--disable-gpu",
    "--no-first-run",
    "--force-device-scale-factor=1",
    "--window-size=1920,1080",
    f"--user-data-dir={browser_data}",
    f"--screenshot={output}",
    url,
]
process = subprocess.Popen(
    command,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
deadline = time.monotonic() + 20
last_size = -1
stable = 0
try:
    while time.monotonic() < deadline:
        if output.is_file():
            size = output.stat().st_size
            stable = stable + 1 if size == last_size and size > 24 else 0
            last_size = size
            if stable >= 5:
                break
        if process.poll() is not None and not output.is_file():
            raise SystemExit(f"Chrome encerrou sem gerar {output.name}")
        time.sleep(0.1)
    else:
        raise SystemExit(f"Tempo esgotado ao gerar {output.name}")
finally:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)

payload = output.read_bytes()
if (payload[:8] != b"\x89PNG\r\n\x1a\n" or
        struct.unpack(">II", payload[16:24]) != (1920, 1080) or
        payload[-12:] != b"\x00\x00\x00\x00IEND\xaeB`\x82"):
    raise SystemExit(f"PNG inválido: {output.name}")
PY
  done
done

python3 - "$SCRIPT_DIR" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])


def canonical_text_bytes(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


images = {
    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted((root / "generated").glob("*.png"))
}
manifest = {
    "format": 1,
    "source": "../index.html",
    "source_sha256": hashlib.sha256(canonical_text_bytes(root / "index.html")).hexdigest(),
    "images": images,
}
(root / "generated" / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

echo "Mapas gerados em $SCRIPT_DIR/generated"
