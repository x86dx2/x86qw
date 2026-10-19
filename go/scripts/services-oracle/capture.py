"""Capture what Python `host`/`proxy`/`qtv` would start, never starting it.

Usage: X86QW_SERVICES_ORACLE=out.json python3 capture.py ACTION [ARGS...]

run_processes and spawn_background_controller are replaced, so MVDSV, QTV,
QWFWD and the background controller are never spawned. The capture holds
the process specs, the ephemeral cfg contents and the journal as they are at
the moment the supervisor would start; the normal cleanup then runs.
"""
import json, os, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "dist/installer/bin"))
sys.path.insert(0, str(REPO))
OUT = os.environ["X86QW_SERVICES_ORACLE"]

import manager  # noqa: E402

services = manager.load_services_module()


class Stop(BaseException):
    pass


def write(document):
    with open(OUT, "w", encoding="utf-8") as output:
        output.write(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def spec_document(spec):
    rcon, ready = spec.startup_rcon, spec.readiness
    return {
        "label": spec.label,
        "arguments": [str(item) for item in spec.arguments],
        "cwd": str(spec.cwd),
        "expected_sha256": spec.launch_target.expected_sha256 if spec.launch_target else "",
        "parameters": [[name, value] for name, value in spec.parameters],
        "startup_rcon": None if rcon is None else {
            "address": rcon.address, "port": rcon.port, "password": rcon.password,
            "config_name": rcon.config_name, "expected_map": rcon.expected_map,
            "expected_gamedir": rcon.expected_gamedir,
        },
        "readiness": None if ready is None else {
            "kind": ready.kind, "address": ready.address, "port": ready.port,
            "upstream": ready.upstream,
        },
    }


def fake_run_processes(specs, journal=None, **_kwargs):
    configs, materialized = {}, []
    if journal is not None:
        for entry in journal.data.get("temporary_files", []):
            path = journal.target / entry["path"]
            configs[path.name] = path.read_bytes().decode("latin-1")
        for entry in journal.data.get("materialized_files", []):
            materialized.append(str(journal.target / entry["path"]))
    write({
        "action": sys.argv[1],
        "specs": [spec_document(spec) for spec in specs],
        "configs": configs,
        "materialized": materialized,
        "journal": json.loads(json.dumps(journal.data)) if journal is not None else None,
    })
    return 0


def fake_spawn_background_controller(command, cwd, request, **_kwargs):
    write({
        "action": sys.argv[1],
        "background": {"arguments": [str(item) for item in command[2:]], "request": request.decode("utf-8")},
    })
    raise Stop()


def never_spawn(*_args, **_kwargs):
    raise RuntimeError("oracle must never spawn a service")


services.run_processes = fake_run_processes
services.spawn_background_controller = fake_spawn_background_controller
for module in (manager, services):
    for name in ("spawn_guardian", "spawn_detached_client", "spawn_client"):
        if hasattr(module, name):
            setattr(module, name, never_spawn)
manager.load_services_module = lambda: services

import subprocess  # noqa: E402

_popen = subprocess.Popen


class GuardedPopen(_popen):
    def __init__(self, args, *rest, **kwargs):
        names = {Path(str(item)).name.split(".")[0] for item in (args if isinstance(args, (list, tuple)) else [args])}
        if names & {"mvdsv", "qtv", "qwfwd", "manager"}:
            raise RuntimeError("oracle must never spawn a service")
        super().__init__(args, *rest, **kwargs)


subprocess.Popen = GuardedPopen

try:
    code = manager.main(sys.argv[1:])
except Stop:
    code = 0
except SystemExit as error:
    code = error.code
print("EXIT", code)
