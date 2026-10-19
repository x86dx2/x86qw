#!/bin/bash
# oracle.sh SCENARIO... — local parity check of the Go maintenance commands
# against the Python CLI of this checkout.
#
# For each side (py, go) it APFS-clones $ORACLE_SOURCE, gives the side its own
# HOME (with a ~/.local/bin/x86qw link) and its own installer cache (a clone
# of the real cache, so nothing real is ever deleted), ages both clones the
# same way, runs the command with Python (pyrun.py) and with $GO_BIN, then
# requires identical trees, modes, caches, links and normalized output, and,
# when the installation should survive, that Python `verify` accepts the Go
# result. Never point ORACLE_SOURCE at an installation you cannot lose; it is
# only read.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
GO_BIN="${GO_BIN:?set GO_BIN to the x86qw Go binary}"
SOURCE="${ORACLE_SOURCE:-$HOME/Games/x86qw}"
W="${ORACLE_WORK:-$(mktemp -d)}"
PYTHON="${PYTHON:-python3}"
# Without --installed-cli Python needs a development root: use the one Go embeds.
export X86_QW_TRUST_ROOT="$HERE/../../internal/trust/root.json"
REAL_CACHE="${ORACLE_REAL_CACHE:-$(getconf DARWIN_USER_CACHE_DIR 2>/dev/null)x86qw}"
PASS=0; FAIL=0; POST=""

setup() { # side
  local side=$1 root="$W/$1"
  rm -rf "$root"; mkdir -p "$root/home/.local/bin" "$root/cache"
  cp -cR "$SOURCE" "$root/target"
  ln -s "$root/target/x86qw.sh" "$root/home/.local/bin/x86qw"
  if [ -d "$REAL_CACHE" ]; then cp -cR "$REAL_CACHE" "$root/cache/x86qw"; else
    mkdir -p "$root/cache/x86qw"; printf 'x86qw-cache-v1\n' > "$root/cache/x86qw/.x86qw-cache"
    chmod 700 "$root/cache/x86qw"; chmod 600 "$root/cache/x86qw/.x86qw-cache"; fi
  mkdir -p "$root/cache/x86-qw"; printf 'x86-qw-cache-v1\n' > "$root/cache/x86-qw/.x86-qw-cache"
  chmod 700 "$root/cache/x86-qw"; chmod 600 "$root/cache/x86-qw/.x86-qw-cache"
}

run_side() { # side args...
  local side=$1; shift
  local root="$W/$side"
  local args=("$@")
  args=("${args[@]//@TARGET@/$root/target}")
  args=("${args[@]//@ROOT@/$root}")
  if [ "$side" = py ]; then
    (cd "$root" && HOME="$root/home" X86QW_CACHE_DIR="$root/cache/x86qw" "$PYTHON" "$HERE/pyrun.py" "${PY_PREFIX[@]}" "${args[@]}" < /dev/null > "$root/out.txt" 2> "$root/err.txt")
  else
    (cd "$root" && HOME="$root/home" X86QW_CACHE_DIR="$root/cache/x86qw" "$GO_BIN" "${args[@]}" < /dev/null > "$root/out.txt" 2> "$root/err.txt")
  fi
  echo $? > "$root/exit.txt"
}

tree() { # root
  [ -d "$1" ] || { echo "(absent)"; return; }
  (cd "$1" && find . \( -path ./.x86qw/staging -o -path ./.x86qw/sessions \) -prune -o ! -name .DS_Store -print0 \
    | xargs -0 stat -f '%Sp %N' | sort)
  (cd "$1" && find . \( -path ./.x86qw/staging -o -path ./.x86qw/sessions \) -prune -o -type f ! -name .DS_Store -print0 \
    | xargs -0 shasum -a 256 | sort -k2)
}

normalize() { sed -e "s#$W/py#@ROOT@#g" -e "s#$W/go#@ROOT@#g" "$@"; }

compare() { # scenario expect_target(yes/no) output_filter
  local name=$1 keep=$2 filter=$3 problems=()
  [ "$(cat "$W/py/exit.txt")" = "$(cat "$W/go/exit.txt")" ] || problems+=("exit py=$(cat "$W/py/exit.txt") go=$(cat "$W/go/exit.txt")")
  diff <(tree "$W/py/target") <(tree "$W/go/target") > "$W/tree.diff" || problems+=("tree")
  for side in py go; do
    ls -A "$W/$side/cache" > "$W/$side/caches.txt"
    [ -e "$W/$side/home/.local/bin/x86qw" ] || [ -L "$W/$side/home/.local/bin/x86qw" ] && echo link > "$W/$side/link.txt" || echo nolink > "$W/$side/link.txt"
  done
  cmp -s "$W/py/caches.txt" "$W/go/caches.txt" || problems+=("caches")
  cmp -s "$W/py/link.txt" "$W/go/link.txt" || problems+=("link")
  if [ -n "$filter" ]; then
    diff <(normalize "$W/py/out.txt" | eval "$filter") <(normalize "$W/go/out.txt" | eval "$filter") > "$W/out.diff" || problems+=("output")
    diff <(normalize "$W/py/err.txt" | grep '^✗') <(normalize "$W/go/err.txt" | grep '^✗') >> "$W/out.diff" || problems+=("error")
    diff <(normalize "$W/py/out.txt" "$W/py/err.txt" | grep '^! ' | sort) <(normalize "$W/go/out.txt" "$W/go/err.txt" | grep '^! ' | sort) >> "$W/out.diff" || problems+=("warnings")
  fi
  if [ "$keep" = yes ] && [ -d "$W/go/target" ]; then
    (cd "$W/go" && HOME="$W/go/home" X86QW_CACHE_DIR="$W/go/cache/x86qw" "$PYTHON" "$HERE/pyrun.py" --online-only --installed-cli verify --json "$W/go/target" < /dev/null 2>/dev/null) \
      | grep -q '"verified":true' || problems+=("python-verify")
  fi
  if [ ${#problems[@]} -eq 0 ]; then PASS=$((PASS+1)); echo "PASS $name"; else
    FAIL=$((FAIL+1)); echo "FAIL $name: ${problems[*]}"
    [ -s "$W/tree.diff" ] && head -8 "$W/tree.diff" | sed 's/^/    /'
    [ -s "$W/out.diff" ] && head -8 "$W/out.diff" | sed 's/^/    /'
    head -3 "$W/py/err.txt" "$W/go/err.txt" | sed 's/^/    /'
  fi
  rm -f "$W/out.diff" "$W/tree.diff"
}

scenario() { # name age-fn keep filter command...
  local name=$1 age=$2 keep=$3 filter=$4; shift 4
  for side in py go; do setup $side; [ -n "$age" ] && $age "$W/$side"; run_side $side "$@"; [ -n "$POST" ] && $POST "$W/$side"; done
  compare "$name" "$keep" "$filter"
}

# --- aging helpers (argument: side root) ----------------------------------
age_library() { mkdir -p "$1/target/qw"; printf '%s\n' '{"favorites": [{"address": "a.example:27500", "freshness": "2026-01-01T00:00:00Z", "origin": "user", "title": "A"}, {"address": "b.example:27500", "freshness": "2026-01-02T00:00:00Z", "origin": "hub", "title": "B"}], "format": 1, "kind": "library", "recents": [{"address": "c.example:28000", "freshness": "2026-01-03T00:00:00Z", "origin": "local", "title": "C"}]}' > "$1/target/qw/x86qw-library.json"; }
age_profile() { printf '// mine\n' >> "$1/target/ezquake/configs/config.cfg"; printf 'set x 1\n' > "$1/target/qw/x86qw-extra.cfg"; }
age_restore() { age_profile "$1"; mkdir -p "$1/stash"
  (cd "$1" && HOME="$1/home" X86QW_CACHE_DIR="$1/cache/x86qw" "$PYTHON" "$HERE/pyrun.py" --online-only --installed-cli profile "$1/target" --backup "$1/stash/backup.zip" < /dev/null > /dev/null 2>&1)
  printf 'changed\n' > "$1/target/ezquake/configs/config.cfg"; rm -f "$1/target/qw/x86qw-extra.cfg"; }
# The library stamps entries with the current time; pin it after the run.
pin_freshness() { sed -i '' -E 's/"freshness": "[^"]*"/"freshness": "@NOW@"/' "$1/target/qw/x86qw-library.json"; }
age_changes() { printf 'x\n' > "$1/target/qw/new file[1].cfg"; printf 'tamper\n' >> "$1/target/qw/x86qw-ktx-help.cfg"; rm -f "$1/target/arena/x86qw-arena.cfg"; }
age_cleanup() { mkdir -p "$1/target/ezquake/temp" "$1/target/ezquake/sb/cache" "$1/target/td2/demos" "$1/target/logs" "$1/target/fortress/progs";
  printf 'x' > "$1/target/ezquake/temp/a"; printf 'y' > "$1/target/ezquake/sb/cache/b"; : > "$1/target/td2/demos/empty.mvd";
  printf 'demo' > "$1/target/td2/demos/real.mvd"; printf 'tmp' > "$1/target/fortress/x.tmp"; printf 'log' > "$1/target/logs/l.txt";
  printf 'dl' > "$1/target/fortress/progs/downloaded.mdl"; printf 'h' > "$1/target/qw/qconsole.log"; }
age_public_marker() { age_cleanup "$1"; chmod 644 "$1/cache/x86qw/.x86qw-cache"; }
age_public_legacy() { age_cleanup "$1"; chmod 644 "$1/cache/x86-qw/.x86-qw-cache"; }
age_repair() { rm -f "$1/target/qw/x86qw-ktx-help.cfg" "$1/target/td2/x86qw_td2.dat" "$1/target/qw/pak.lst"; chmod -x "$1/target/mvdsv"; }
age_repair_state() { "$PYTHON" - "$1/target/.x86qw/state.json" <<'PY'
import hashlib, json, sys
path = sys.argv[1]
document = json.load(open(path))
document["recorded_components"].remove("qtv")
document["component_fingerprint"] = hashlib.sha256("".join(i + "\n" for i in sorted(document["recorded_components"])).encode()).hexdigest()
open(path, "w").write(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
PY
}

json_only='grep "^{" | sed -E "s/\"freshness\": \"[^\"]*\"/F/g"'
# Go prints warnings on stderr (Python on stdout); compare() checks them apart.
# Python's banner and "Preparando" line are interface chrome Go does not print.
full="awk '{line[NR] = \$0} /^Preparando interface do instalador/ {skip = NR} END {for (i = skip + 1; i <= NR; i++) print line[i]}' | awk 'NF {seen = 1} seen'"
messages="$full | grep -v '^! '"
lines_only='grep -E "^(  |[AMD]  |x86QW|favorites|recents|profile|cache|personal|Arquivos)" | grep -v "Perfil gravado"'

PY_PREFIX=(--online-only --installed-cli)
run_all() {
  for wanted in "$@"; do case $wanted in
    library-add) POST=pin_freshness; scenario library-add "" yes "$full | sed -E 's/  [0-9T:-]+Z$//'" library @TARGET@ --add Quake.Example.com:27500; POST="" ;;
    library-remove) scenario library-remove age_library yes "$full" library @TARGET@ --remove B.EXAMPLE:27500 ;;
    profile) scenario profile age_profile yes "$full" profile @TARGET@ ;;
    profile-backup) scenario profile-backup age_profile yes "$full" profile @TARGET@ --backup @ROOT@/backup.zip
      for side in py go; do (cd "$W/$side" && rm -rf x && mkdir x && cd x && unzip -q ../backup.zip && find . -type f | sort | xargs shasum -a 256) > "$W/$side/zip.txt"; done
      cmp -s "$W/py/zip.txt" "$W/go/zip.txt" && echo "PASS profile-backup-zip" || { FAIL=$((FAIL+1)); echo "FAIL profile-backup-zip"; } ;;
    profile-restore) scenario profile-restore age_restore yes "$full" profile @TARGET@ --restore @ROOT@/stash/backup.zip ;;
    ui) scenario ui "" yes "$full" ui @TARGET@ --output @ROOT@/panel.html
      diff <(normalize "$W/py/panel.html") <(normalize "$W/go/panel.html") > /dev/null && echo "PASS ui-html" || { FAIL=$((FAIL+1)); echo "FAIL ui-html"; } ;;
    changes) scenario changes age_changes no "$full" changes @TARGET@ ;;
    changes-sync) scenario changes-sync age_changes no "$full" changes @TARGET@ --sync-gitignore ;;
    cleanup) scenario cleanup age_cleanup yes "$messages" cleanup @TARGET@ ;;
    cleanup-all) scenario cleanup-all age_cleanup yes "$messages" cleanup @TARGET@ --downloads --personal-data ;;
    cleanup-public-marker) scenario cleanup-public-marker age_public_marker yes "$messages" cleanup @TARGET@ ;;
    cleanup-public-legacy) scenario cleanup-public-legacy age_public_legacy yes "$messages" cleanup @TARGET@ ;;
    purge-public-marker) scenario purge-public-marker age_public_marker yes "$messages" uninstall @TARGET@ --purge ;;
    migrate-dry) scenario migrate-dry "" yes "$full" migrate @TARGET@ --dry-run ;;
    migrate) scenario migrate "" yes "$full" migrate @TARGET@ ;;
    uninstall) scenario uninstall "" no "$messages" uninstall @TARGET@ ;;
    uninstall-modified) scenario uninstall-modified age_changes no "$messages" uninstall @TARGET@ ;;
    purge) scenario purge "" no "$messages" uninstall @TARGET@ --purge ;;
    repair-dry) PY_PREFIX=(--online-only); scenario repair-dry age_repair no "$full" repair @TARGET@ --dry-run --json; PY_PREFIX=(--online-only --installed-cli) ;;
    repair) PY_PREFIX=(--online-only); scenario repair age_repair yes "tail -1" repair @TARGET@; PY_PREFIX=(--online-only --installed-cli) ;;
    repair-state) PY_PREFIX=(--online-only); scenario repair-state age_repair_state yes "tail -1" repair @TARGET@; PY_PREFIX=(--online-only --installed-cli) ;;
    *) echo "unknown scenario $wanted"; FAIL=$((FAIL+1)) ;;
  esac; done
}

if [ $# -eq 0 ]; then
  set -- library-add library-remove profile profile-backup profile-restore ui changes changes-sync cleanup cleanup-all \
    cleanup-public-marker cleanup-public-legacy purge-public-marker \
    migrate-dry migrate uninstall uninstall-modified purge repair-dry repair repair-state
fi
run_all "$@"
echo "pass=$PASS fail=$FAIL (work: $W)"
