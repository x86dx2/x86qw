import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class GameplayManagerCycleTests(unittest.TestCase):
    def test_gameplay_import_and_player_factory_do_not_import_manager(self):
        """Pure gameplay import must work before the manager adapter is available."""
        probe = textwrap.dedent(
            f"""
            import importlib.abc
            import importlib.util
            import sys
            from pathlib import Path

            root = Path({str(ROOT)!r})
            sys.path.insert(0, str(root))
            sys.modules.pop("manager", None)

            class RejectManager(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "manager":
                        raise AssertionError("gameplay imported manager during module execution")
                    return None

            sys.meta_path.insert(0, RejectManager())
            spec = importlib.util.spec_from_file_location(
                "gameplay_cycle_probe", root / "dist/installer/bin/gameplay.py",
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            assert "manager" not in sys.modules

            class InstallerBase:
                pass

            adapter = module.create_player_adapter(InstallerBase)
            assert issubclass(adapter, InstallerBase)
            assert adapter.__name__ == "Player"
            assert "manager" not in sys.modules
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
