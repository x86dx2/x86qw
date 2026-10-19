"""Capture the ezQuake argv and KTX runtime config Python `play` would use."""
import json, sys, types
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "dist/installer/bin"))
sys.path.insert(0, str(REPO))
import gameplay  # noqa: E402

captured = {}

class Stop(BaseException):
    pass

def fake_write(target, bot_name_settings, startup_commands):
    captured["bot_name_settings"] = [[n, v] for n, v in bot_name_settings]
    captured["startup_commands"] = list(startup_commands)
    path = Path(target) / "qw" / "x86qw-ktx-session-ORACLE.cfg"
    return types.SimpleNamespace(path=path, ownership=None)

gameplay.write_ktx_runtime_config = fake_write
gameplay.remove_ktx_runtime_config = lambda config: True

def fake_launch(self, runtime, arguments):
    captured["argv"] = [str(a) for a in arguments]
    raise Stop()

player_cls = None
for name in dir(gameplay):
    obj = getattr(gameplay, name)
    if isinstance(obj, type) and hasattr(obj, "launch_runtime"):
        obj.launch_runtime = fake_launch

import manager  # noqa: E402
for name in dir(manager):
    obj = getattr(manager, name)
    if isinstance(obj, type) and "launch_runtime" in vars(obj):
        obj.launch_runtime = fake_launch
def _never_spawn(*args, **kwargs):
    raise RuntimeError("oracle must never spawn a client")
for module in (manager, gameplay):
    for name in ("spawn_detached_client", "spawn_client"):
        if hasattr(module, name):
            setattr(module, name, _never_spawn)
try:
    code = manager.main(["play", *sys.argv[1:]])
except Stop:
    code = 0
except SystemExit as error:
    code = error.code
captured["exit"] = code
print(json.dumps(captured, ensure_ascii=False))
