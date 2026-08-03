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

echo "Mapas gerados em $SCRIPT_DIR/generated"
