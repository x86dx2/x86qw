#!/bin/bash
# client-oracle.sh fresh-nightly|nightly-older|nightly-unprepared
#
# Local parity check of the ezQuake client paths against the Python CLI.
#   PYZ      Python installer bundle (x86qw.pyz from x86qw-installer-1.0.16)
#   GO_BIN   the Go x86qw binary
#   WORK     scratch directory (APFS for cheap clones); never a real install
#   NIGHTLY  the macOS nightly zip (for nightly-unprepared)
# fresh-nightly installs essential+nightly with both CLIs (note: like the
# Python installer, this clears ezQuake's macOS directory preferences).
# The other scenarios clone $WORK/fn-py, age it identically and run
# `update --yes` with both CLIs. Trees and modes must match and Python
# verify must accept the Go result.
set -u
: "${PYZ:?}" "${GO_BIN:?}" "${WORK:?}"
scenario=$1
W=$WORK
modes() { (cd "$1" && find . -path ./.x86qw/staging -prune -o -path ./.x86qw/sessions -prune -o -path ./.x86qw/cli -prune -o ! -name x86qw.sh ! -name .DS_Store -print0 | xargs -0 stat -f '%Sp %N' | sort); }
compare() {
  diff -rq -x staging -x sessions -x cli -x x86qw.sh -x .DS_Store "$1" "$2" > "$W/diff.txt" 2>&1
  if [ -s "$W/diff.txt" ]; then echo "TREES DIFFER"; head -20 "$W/diff.txt"; else echo "TREES IDENTICAL"; fi
  diff <(modes "$1") <(modes "$2") > "$W/modes.txt" && echo "MODES IDENTICAL" || { echo "MODES DIFFER"; head -10 "$W/modes.txt"; }
}
if [ "$scenario" = fresh-nightly ]; then
  rm -rf "$W/fn-py" "$W/fn-go" "$W/home"; mkdir -p "$W/home"
  (cd "$W" && HOME="$W/home" python3 -I "$PYZ" --online-only install --non-interactive \
    --platform macos --channel nightly --release latest --profile essential "$W/fn-py" < /dev/null > "$W/fn-py.log" 2>&1)
  echo "python exit=$?"
  "$GO_BIN" install --profile essential --channel nightly "$W/fn-go" > "$W/fn-go.log" 2>&1; echo "go exit=$?"
  compare "$W/fn-py" "$W/fn-go"
  exit
fi
OLD=20260101-000000_a86996a
NEW=20260616-101233_a86996a
age() {
  root=$1
  receipt="$root/.x86qw/clients/ezquake/macos/nightly.receipt"
  case $scenario in
    nightly-older) sed -i '' "s/$NEW/$OLD/g" "$receipt" ;;
    nightly-unprepared)
      : "${NIGHTLY:?}"
      rm -rf "$root/ezQuake Nightly.app" "$W/raw"; mkdir -p "$W/raw"
      (cd "$W/raw" && unzip -q "$NIGHTLY")
      cp -R "$W/raw/ezQuake.app" "$root/ezQuake Nightly.app"
      raw=$(shasum -a 256 "$root/ezQuake Nightly.app/Contents/MacOS/ezQuake" | cut -d' ' -f1)
      sed -i '' "s/^binary_sha256	.*/binary_sha256	$raw/" "$receipt" ;;
  esac
}
for side in py go; do rm -rf "$W/co-$side"; cp -cR "$W/fn-py" "$W/co-$side"; age "$W/co-$side"; done
(cd "$W/co-py" && ./x86qw.sh update --dry-run --json < /dev/null 2>&1 | tail -1) > "$W/py-plan.json"
"$GO_BIN" update --dry-run --json "$W/co-go" | sed "s#$W/co-go#$W/co-py#" > "$W/go-plan.json"
cmp -s "$W/py-plan.json" "$W/go-plan.json" && echo "PLANS IDENTICAL" || { echo "PLANS DIFFER"; cat "$W/py-plan.json" "$W/go-plan.json"; }
(cd "$W/co-py" && ./x86qw.sh update --yes < /dev/null > "$W/py.log" 2>&1); echo "python exit=$?"
"$GO_BIN" update --yes "$W/co-go" > "$W/go.log" 2>&1; echo "go exit=$?"
compare "$W/co-py" "$W/co-go"
(cd "$W/co-go" && ./x86qw.sh verify --json < /dev/null 2>&1 | tail -1)
