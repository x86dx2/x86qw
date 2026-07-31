from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CAPABILITIES = ROOT / "maintenance/inventory/capabilities.json"
PRODUCT = ROOT / "site/public/api/v1/product.json"


class LauncherContractTests(unittest.TestCase):
    def prepare_launcher(self, root: Path, name: str) -> tuple[Path, Path]:
        root.mkdir(parents=True)
        launcher = root / name
        shutil.copy2(ROOT / "dist/installer/bin" / name, launcher)
        if name.endswith(".sh"):
            launcher.chmod(0o755)
        app = root / ".install/cli/x86qw.pyz"
        app.parent.mkdir(parents=True)
        app.write_text(
            """import json, os, pathlib, sys
pathlib.Path(os.environ['X86QW_STUB_OUTPUT']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')
raise SystemExit(int(os.environ.get('X86QW_STUB_EXIT', '0')))
""",
            encoding="utf-8",
        )
        return launcher, app

    def test_public_command_contract_matches_catalog_help_and_launchers(self):
        commands = json.loads(CAPABILITIES.read_text(encoding="utf-8"))["commands"]
        product_commands = json.loads(PRODUCT.read_text(encoding="utf-8"))["commands"]
        self.assertEqual(commands, product_commands)
        help_result = subprocess.run(
            [sys.executable, str(ROOT / "dist/installer/bin/manager.py"), "--help"],
            check=True, capture_output=True, text=True,
        )
        shell = (ROOT / "dist/installer/bin/x86qw.sh").read_text(encoding="utf-8")
        batch = (ROOT / "dist/installer/bin/x86qw.cmd").read_text(encoding="utf-8")
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, help_result.stdout)
                self.assertIn(command, shell)
                self.assertIn(command, batch)

    @unittest.skipIf(os.name == "nt", "launcher Unix é exercitado nos runners POSIX")
    def test_unix_launcher_forwards_repair_and_long_play_arguments_exactly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "install with spaces"
            launcher, _ = self.prepare_launcher(root, "x86qw.sh")
            output = root / "arguments.json"
            environment = dict(os.environ, X86QW_STUB_OUTPUT=str(output))
            arguments = [
                "play", "ktx", "--mode", "duel", "--map", "dm6",
                "--bots", "2", "--bot-skill", "8", "--no-color",
            ]
            completed = subprocess.run(
                [str(launcher), *arguments], env=environment, check=False,
            )
            self.assertEqual(0, completed.returncode)
            self.assertEqual(
                [*arguments, "--target", str(root)],
                json.loads(output.read_text(encoding="utf-8")),
            )
            completed = subprocess.run(
                [str(launcher), "repair", "--dry-run"], env=environment, check=False,
            )
            self.assertEqual(0, completed.returncode)
            self.assertEqual(
                ["--online-only", "--installed-cli", "repair", str(root), "--dry-run"],
                json.loads(output.read_text(encoding="utf-8")),
            )

    @unittest.skipUnless(os.name == "nt", "cmd.exe é exercitado somente no runner Windows")
    def test_windows_launcher_forwards_more_than_nine_arguments_and_exit_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "install with spaces"
            launcher, _ = self.prepare_launcher(root, "x86qw.cmd")
            output = root / "arguments.json"
            environment = dict(os.environ, X86QW_STUB_OUTPUT=str(output))
            arguments = [
                "play", "ktx", "--mode", "duel", "--map", "dm6",
                "--bots", "2", "--bot-skill", "8", "--no-color",
            ]
            completed = subprocess.run(
                [
                    os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call",
                    str(launcher), *arguments,
                ],
                env=environment, check=False,
            )
            self.assertEqual(0, completed.returncode)
            received = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(arguments, received[:-2])
            self.assertEqual("--target", received[-2])
            self.assertEqual(root.resolve(), Path(received[-1]).resolve())

            environment["X86QW_STUB_EXIT"] = "23"
            completed = subprocess.run(
                [
                    os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call",
                    str(launcher), "repair", "--dry-run",
                ],
                env=environment, check=False,
            )
            self.assertEqual(23, completed.returncode)
            received = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(["--online-only", "--installed-cli", "repair", "--dry-run"], received[:-1])
            self.assertEqual(root.resolve(), Path(received[-1]).resolve())


if __name__ == "__main__":
    unittest.main()
