#!/bin/bash
# compare.sh PY_TARGET GO_TARGET < cases.txt -- local parity check of the
# service commands (host/proxy/qtv) against the Python CLI. Both targets must
# be disposable APFS clones (cp -cR) of one installation. Each case runs
# through capture.py (Python) and `x86qw` with X86QW_SERVICES_ORACLE set; no
# MVDSV, QTV, QWFWD or background controller is ever started. Specs (argv,
# cwd, RCON/readiness, parameters), ephemeral cfg contents, materialized files
# and the journal must match after normalizing random tokens. Refusals must
# exit with the same code. Python opens its interactive menu when --mode or
# --map is missing (exit 130 under </dev/null); the Go CLI refuses (exit 1).
S="$(cd "$(dirname "$0")" && pwd)"
GO_BIN="${GO_BIN:?set GO_BIN to the x86qw binary}"
PYTHON="${PYTHON:-python3}"
PT=$1; GT=$2
WORK=$(mktemp -d)
PW="$WORK/password"
printf 'segredo-x86\n' > "$PW"; chmod 600 "$PW"
pass=0; fail=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  case $line in \#*) continue;; esac
  eval "set -- $line"
  rm -f "$WORK/py.json" "$WORK/go.json"
  X86QW_SERVICES_ORACLE="$WORK/py.json" "$PYTHON" "$S/capture.py" "$@" --target "$PT" \
    < /dev/null > "$WORK/py.out" 2> "$WORK/py.err"
  py_exit=$(sed -n 's/^EXIT //p' "$WORK/py.out" | tail -1)
  X86QW_SERVICES_ORACLE="$WORK/go.json" "$GO_BIN" "$@" --target "$GT" \
    < /dev/null > "$WORK/go.out" 2> "$WORK/go.err"
  go_exit=$?
  verdict=$("$PYTHON" "$S/normalize.py" "$PT" "$GT" "$WORK/py.json" "$WORK/go.json" \
    "$py_exit" "$go_exit" "$(grep -v '^Traceback' "$WORK/py.err" | tail -1)" "$(tail -1 "$WORK/go.err")" ${VERBOSE:+-v})
  case $verdict in OK|BOTH-REFUSE|BOTH-NO-START) pass=$((pass+1));; *) fail=$((fail+1));; esac
  echo "$verdict :: $line"
done
rm -rf "$WORK"
echo "pass=$pass fail=$fail"
