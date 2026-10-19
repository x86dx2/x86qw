#!/bin/bash
# compare.sh TARGET < cases.txt -- local parity check of `play` against the
# Python CLI: each case goes through capture.py (Python; never launches the
# client) and `x86qw play --plan-json`; argv, KTX cfg commands and bot names
# must match.
S="$(cd "$(dirname "$0")" && pwd)"
GO_BIN="${GO_BIN:?set GO_BIN to the x86qw binary}"
T=$1
pass=0; fail=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  eval "set -- $line"
  py=$(python3 "$S/capture.py" "$@" --target "$T" < /dev/null 2>/dev/null | tail -1)
  go=$("$GO_BIN" play "$@" --target "$T" --plan-json 2>&1)
  verdict=$(python3 - "$py" "$go" <<'PY'
import json, sys
py_raw, go_raw = sys.argv[1], sys.argv[2]
try:
    py = json.loads(py_raw)
except Exception:
    print("PY-UNPARSEABLE " + py_raw[:200]); sys.exit()
py_ok = "argv" in py
try:
    go = json.loads(go_raw); go_ok = True
except Exception:
    go_ok = False
if not py_ok and not go_ok:
    print("BOTH-REFUSE"); sys.exit()
if py_ok != go_ok:
    print("MISMATCH py_ok=%s go=%s" % (py_ok, go_raw[:200])); sys.exit()
norm = lambda argv: [a.replace("__X86QW_KTX_RUNTIME_CONFIG__", "x86qw-ktx-session-ORACLE.cfg") for a in argv]
diffs = []
if norm(go["argv"]) != py["argv"]: diffs.append("argv")
if (go.get("startup_commands") or []) != py.get("startup_commands", []): diffs.append("startup")
if (go.get("bot_name_settings") or []) != py.get("bot_name_settings", []): diffs.append("names")
print("OK" if not diffs else "DIFF " + ",".join(diffs))
PY
)
  case $verdict in OK|BOTH-REFUSE) pass=$((pass+1));; *) fail=$((fail+1));; esac
  echo "$verdict :: $line"
done
echo "pass=$pass fail=$fail"
