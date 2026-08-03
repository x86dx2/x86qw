#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CHROME=${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}

if [ ! -x "$CHROME" ]; then
  echo "Google Chrome não encontrado em: $CHROME" >&2
  exit 1
fi

mkdir -p "$SCRIPT_DIR/generated"

for profile in ktx final-arena pro-x team-fortress td2; do
  rendered=$(
    "$CHROME" \
      --headless=new \
      --disable-gpu \
      --dump-dom \
      "file://$SCRIPT_DIR/index.html?profile=$profile&layout=windows-ansi" 2>/dev/null
  )
  case "$rendered" in
    *'data-control-map-valid="true"'*) ;;
    *)
      echo "Mapa de controles inválido para o perfil: $profile" >&2
      exit 1
      ;;
  esac

  for layout in windows-ansi macos-en-us keychron-k3-v3; do
    if [ "$layout" = windows-ansi ]; then
      output="$SCRIPT_DIR/generated/$profile.png"
    else
      output="$SCRIPT_DIR/generated/$profile--$layout.png"
    fi
    "$CHROME" \
      --headless=new \
      --hide-scrollbars \
      --disable-gpu \
      --force-device-scale-factor=1 \
      --window-size=1920,1080 \
      --screenshot="$output" \
      "file://$SCRIPT_DIR/index.html?profile=$profile&layout=$layout" >/dev/null 2>&1
  done
done

python3 - "$SCRIPT_DIR" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
images = {
    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted((root / "generated").glob("*.png"))
}
manifest = {
    "format": 1,
    "source": "../index.html",
    "source_sha256": hashlib.sha256((root / "index.html").read_bytes()).hexdigest(),
    "images": images,
}
(root / "generated" / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

echo "Mapas gerados em $SCRIPT_DIR/generated"
