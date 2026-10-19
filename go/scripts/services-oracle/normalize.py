"""Compare one Python capture with one Go capture of a service command.

normalize.py PY_TARGET GO_TARGET PY_CAPTURE GO_CAPTURE PY_EXIT GO_EXIT PY_ERR GO_ERR

Random tokens (cfg names, bootstrap RCON, log names, session ids), times,
device/inode numbers, the controller identity and the installation path are
normalized; everything else must match. The final on-disk journals of both
runs must also match and load with Python's load_session_journal.
"""
import json, re, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from x86qw_runtime.supervisor.sessions import load_session_journal  # noqa: E402

py_target, go_target, py_capture, go_capture, py_exit, go_exit, py_err, go_err = sys.argv[1:9]

SESSION = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00")
HEX = re.compile(r"(?<![0-9a-f])([0-9a-f]{24}|[0-9a-f]{16})(?![0-9a-f])")


def scrub(value, target):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"device", "inode"}:
                item = 0
            elif key == "controller" and isinstance(item, dict):
                item = {"command": item.get("command")}
            result[scrub(key, target)] = scrub(item, target)
        return result
    if isinstance(value, list):
        return [scrub(item, target) for item in value]
    if isinstance(value, str):
        value = value.replace(target, "<T>")
        value = SESSION.sub("<SESSION>", value)
        value = STAMP.sub("<TIME>", value)
        return HEX.sub(lambda match: "<H%d>" % len(match.group(1)), value)
    return value


def load(path, target):
    try:
        return scrub(json.loads(Path(path).read_text(encoding="utf-8")), target)
    except FileNotFoundError:
        return None


def final_journal(target):
    sessions = sorted(Path(target, ".x86qw", "sessions").glob("*/session.json"))
    return sessions[-1] if sessions else None


def presets(target):
    path = Path(target, "qw", "x86qw-host-presets.json")
    return path.read_bytes() if path.exists() else None


preset_diff = presets(py_target) != presets(go_target)
py, go = load(py_capture, py_target), load(go_capture, go_target)
if preset_diff:
    print("DIFF presets-file")
    sys.exit()
if py is None and go is None:
    if py_exit == go_exit:
        print("BOTH-REFUSE" if py_exit != "0" else "BOTH-NO-START")
    else:
        print("EXIT-DIFF py=%s go=%s | py: %s | go: %s" % (py_exit, go_exit, py_err, go_err))
    sys.exit()
if (py is None) != (go is None):
    print("MISMATCH py_exit=%s go_exit=%s | py: %s | go: %s" % (py_exit, go_exit, py_err, go_err))
    sys.exit()
diffs = []
for key in sorted(set(py) | set(go)):
    if key == "configs":
        canonical = lambda configs: {re.sub(r"<H24>", "", name): text for name, text in (configs or {}).items()}
        if canonical(py.get(key)) != canonical(go.get(key)):
            diffs.append(key)
    elif key == "materialized":
        if sorted(py.get(key) or []) != sorted(go.get(key) or []):
            diffs.append(key)
    elif py.get(key) != go.get(key):
        diffs.append(key)
if py_exit != go_exit:
    diffs.append("exit")
if "journal" in py:
    py_final, go_final = final_journal(py_target), final_journal(go_target)
    try:
        go_data = load_session_journal(go_final)
    except Exception as error:  # noqa: BLE001
        diffs.append("python-rejects-go-journal(%s)" % error)
    else:
        py_data = load_session_journal(py_final)
        if scrub(py_data, py_target) != scrub(go_data, go_target):
            diffs.append("final-journal")
if diffs and "-v" in sys.argv:
    for key in diffs:
        if key in py or key in go:
            print("PY", key, json.dumps(py.get(key), ensure_ascii=False, sort_keys=True)[:3000])
            print("GO", key, json.dumps(go.get(key), ensure_ascii=False, sort_keys=True)[:3000])
print("OK" if not diffs else "DIFF " + ",".join(diffs))
