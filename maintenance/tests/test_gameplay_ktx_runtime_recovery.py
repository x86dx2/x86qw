import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from x86qw_runtime.gameplay import runtime_configs
from x86qw_runtime.io import atomic as atomic_io
from x86qw_runtime.platform.processes import ProcessProbe

GAMEPLAY_SPEC = importlib.util.spec_from_file_location(
    "gameplay_ktx_recovery", ROOT / "dist/installer/bin/gameplay.py",
)
gameplay = importlib.util.module_from_spec(GAMEPLAY_SPEC)
assert GAMEPLAY_SPEC.loader is not None
sys.modules[GAMEPLAY_SPEC.name] = gameplay
GAMEPLAY_SPEC.loader.exec_module(gameplay)


class KtxRuntimeConfigRecoveryTests(unittest.TestCase):
    def child_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((
            str(ROOT), str(ROOT / "dist/installer/bin"),
        ))
        return environment

    def crash_after_config_creation(self, target: Path) -> Path:
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os, pathlib, sys; import gameplay; "
                    "config = gameplay.write_ktx_runtime_config("
                    "pathlib.Path(sys.argv[1]), ((\"k_fb_name_0\", \"Luffy\"),)); "
                    "assert config.path.is_file(); os._exit(23)"
                ),
                str(target),
            ],
            check=False,
            env=self.child_environment(),
        )
        self.assertEqual(23, child.returncode)
        abandoned = tuple((target / "qw").glob("x86qw-ktx-session-*.cfg"))
        self.assertEqual(1, len(abandoned))
        return abandoned[0]

    def test_next_preparation_recovers_config_left_by_hard_crash(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            abandoned = self.crash_after_config_creation(target)

            current = gameplay.write_ktx_runtime_config(
                target, (("k_fb_name_0", "Zoro"),),
            )
            try:
                self.assertFalse(abandoned.exists())
                self.assertTrue(current.path.is_file())
                self.assertFalse((target / ".x86qw/sessions/active.lock").exists())
            finally:
                self.assertTrue(gameplay.remove_ktx_runtime_config(current))

    def test_crash_after_durable_intent_never_leaves_an_unjournaled_public_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os, pathlib, sys; import gameplay; "
                        "from x86qw_runtime.gameplay import runtime_configs; "
                        "real_link = os.link; "
                        "runtime_configs.os.link = lambda source, destination, **kwargs: "
                        "os._exit(31) if str(destination).endswith('.cfg') else "
                        "real_link(source, destination, **kwargs); "
                        "gameplay.write_ktx_runtime_config(pathlib.Path(sys.argv[1]), ())"
                    ),
                    str(target),
                ],
                check=False,
                env=self.child_environment(),
            )
            self.assertEqual(31, child.returncode)
            self.assertEqual([], list((target / "qw").glob("x86qw-ktx-session-*.cfg")))
            journal_directory = target / ".x86qw/gameplay/ktx-runtime-configs"
            self.assertEqual(1, len(list(journal_directory.glob("*.json"))))
            self.assertEqual(1, len(list(journal_directory.glob("*.stage"))))

            current = gameplay.write_ktx_runtime_config(target, ())
            try:
                self.assertTrue(current.path.is_file())
                self.assertEqual([], list(journal_directory.glob("*.stage")))
                self.assertEqual(1, len(list(journal_directory.glob("*.json"))))
            finally:
                self.assertTrue(gameplay.remove_ktx_runtime_config(current))
            self.assertEqual([], list(journal_directory.glob("*.json")))

    def test_committed_intent_failure_cleans_journal_by_committed_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)

            with mock.patch.object(
                atomic_io,
                "_fsync_directory",
                side_effect=OSError("simulated intent directory sync failure"),
            ):
                with self.assertRaisesRegex(
                    gameplay.InstallerError, "intenção KTX",
                ):
                    gameplay.write_ktx_runtime_config(target, ())

            journal_directory = target / ".x86qw/gameplay/ktx-runtime-configs"
            self.assertEqual([], list(journal_directory.glob("*.json")))
            self.assertEqual([], list(journal_directory.glob("*.stage")))
            self.assertEqual([], list((target / "qw").glob("*.cfg")))

    def test_partial_atomic_staging_is_preserved_with_actionable_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os, pathlib, sys\n"
                        "import gameplay\n"
                        "from x86qw_runtime.gameplay import runtime_configs\n"
                        "real_create = runtime_configs.atomic_create_bytes\n"
                        "def partial_create(path, payload, **kwargs):\n"
                        "    if str(path).endswith('.stage'):\n"
                        "        descriptor = runtime_configs.private_fs."
                        "create_private_file(path)\n"
                        "        os.write(descriptor, payload[:8])\n"
                        "        os.fsync(descriptor)\n"
                        "        os._exit(33)\n"
                        "    return real_create(path, payload, **kwargs)\n"
                        "runtime_configs.atomic_create_bytes = partial_create\n"
                        "gameplay.write_ktx_runtime_config(pathlib.Path(sys.argv[1]), ())\n"
                    ),
                    str(target),
                ],
                check=False,
                env=self.child_environment(),
            )
            self.assertEqual(33, child.returncode)
            journal_directory = target / ".x86qw/gameplay/ktx-runtime-configs"
            journals = list(journal_directory.glob("*.json"))
            self.assertEqual(1, len(journals))
            document = json.loads(journals[0].read_text(encoding="utf-8"))
            self.assertEqual("intent", document["state"])
            self.assertIsNone(document["config"]["device"])
            self.assertIsNone(document["config"]["inode"])
            self.assertEqual([], list((target / "qw").glob("*.cfg")))
            staging = list(journal_directory.glob("*.stage"))
            self.assertEqual(1, len(staging))
            self.assertLess(staging[0].stat().st_size, document["config"]["size"])

            with self.assertRaisesRegex(
                gameplay.InstallerError, "execute repair",
            ):
                gameplay.write_ktx_runtime_config(target, ())

            self.assertTrue(journals[0].is_file())
            self.assertTrue(staging[0].is_file())

    def test_crash_after_complete_staging_before_ready_recovers_from_intent(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os, pathlib, sys; import gameplay; "
                        "from x86qw_runtime.gameplay import runtime_configs; "
                        "runtime_configs.atomic_write_bytes = "
                        "lambda *args, **kwargs: os._exit(34); "
                        "gameplay.write_ktx_runtime_config(pathlib.Path(sys.argv[1]), ())"
                    ),
                    str(target),
                ],
                check=False,
                env=self.child_environment(),
            )
            self.assertEqual(34, child.returncode)
            journal_directory = target / ".x86qw/gameplay/ktx-runtime-configs"
            journals = list(journal_directory.glob("*.json"))
            staging = list(journal_directory.glob("*.stage"))
            self.assertEqual(1, len(journals))
            self.assertEqual(1, len(staging))
            document = json.loads(journals[0].read_text(encoding="utf-8"))
            self.assertEqual("intent", document["state"])
            self.assertIsNone(document["config"]["device"])
            self.assertIsNone(document["config"]["inode"])
            self.assertGreater(staging[0].stat().st_size, 0)

            current = gameplay.write_ktx_runtime_config(target, ())
            try:
                self.assertFalse(journals[0].exists())
                self.assertFalse(staging[0].exists())
                self.assertTrue(current.path.is_file())
            finally:
                self.assertTrue(gameplay.remove_ktx_runtime_config(current))

    def test_crash_after_publication_recovers_both_hardlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os, pathlib, sys; import gameplay; "
                        "from x86qw_runtime.gameplay import runtime_configs; "
                        "real_unlink = runtime_configs.private_fs.unlink_private_file; "
                        "runtime_configs.private_fs.unlink_private_file = "
                        "lambda path, **kwargs: os._exit(32) if "
                        "str(path).endswith('.stage') else real_unlink(path, **kwargs); "
                        "gameplay.write_ktx_runtime_config(pathlib.Path(sys.argv[1]), ())"
                    ),
                    str(target),
                ],
                check=False,
                env=self.child_environment(),
            )
            self.assertEqual(32, child.returncode)
            journal_directory = target / ".x86qw/gameplay/ktx-runtime-configs"
            self.assertEqual(1, len(list((target / "qw").glob("*.cfg"))))
            self.assertEqual(1, len(list(journal_directory.glob("*.stage"))))
            self.assertEqual(1, len(list(journal_directory.glob("*.json"))))

            current = gameplay.write_ktx_runtime_config(target, ())
            try:
                self.assertEqual([current.path], list((target / "qw").glob("*.cfg")))
                self.assertEqual([], list(journal_directory.glob("*.stage")))
            finally:
                self.assertTrue(gameplay.remove_ktx_runtime_config(current))

    def test_recovery_preserves_a_regular_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            abandoned = self.crash_after_config_creation(target)
            replacement = abandoned.with_suffix(".replacement")
            replacement.write_bytes(b"personal replacement\n")
            os.replace(replacement, abandoned)

            with self.assertRaisesRegex(gameplay.InstallerError, "preservados"):
                gameplay.write_ktx_runtime_config(target, ())

            self.assertEqual(b"personal replacement\n", abandoned.read_bytes())
            self.assertEqual(1, len(list(
                (target / ".x86qw/gameplay/ktx-runtime-configs").glob("*.json")
            )))

    def test_recovery_preserves_an_identical_inode_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            abandoned = self.crash_after_config_creation(target)
            payload = abandoned.read_bytes()
            replacement = abandoned.with_suffix(".replacement")
            replacement.write_bytes(payload)
            os.replace(replacement, abandoned)

            with self.assertRaisesRegex(gameplay.InstallerError, "preservados"):
                gameplay.write_ktx_runtime_config(target, ())

            self.assertEqual(payload, abandoned.read_bytes())

    def test_unjournaled_prefix_match_is_never_inferred_as_owned(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            directory = target / "qw"
            directory.mkdir(parents=True)
            personal = directory / "x86qw-ktx-session-000000000000000000000000.cfg"
            personal.write_bytes(b"personal\n")

            current = gameplay.write_ktx_runtime_config(target, ())
            try:
                self.assertEqual(b"personal\n", personal.read_bytes())
            finally:
                self.assertTrue(gameplay.remove_ktx_runtime_config(current))

    def test_recovery_preserves_symlink_and_its_target(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks indisponíveis")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "quake-world"
            (target / "qw").mkdir(parents=True)
            abandoned = self.crash_after_config_creation(target)
            personal = root / "personal.cfg"
            personal.write_bytes(b"personal\n")
            abandoned.unlink()
            try:
                abandoned.symlink_to(personal)
            except OSError as error:
                self.skipTest(f"symlink indisponível: {error}")

            with self.assertRaisesRegex(gameplay.InstallerError, "preservados"):
                gameplay.write_ktx_runtime_config(target, ())

            self.assertTrue(abandoned.is_symlink())
            self.assertEqual(b"personal\n", personal.read_bytes())

    def test_recovery_preserves_directory_and_personal_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            abandoned = self.crash_after_config_creation(target)
            abandoned.unlink()
            abandoned.mkdir()
            sentinel = abandoned / "personal.txt"
            sentinel.write_bytes(b"personal\n")

            with self.assertRaisesRegex(gameplay.InstallerError, "preservados"):
                gameplay.write_ktx_runtime_config(target, ())

            self.assertTrue(abandoned.is_dir())
            self.assertEqual(b"personal\n", sentinel.read_bytes())

    def test_recovery_keeps_journal_when_staging_cleanup_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            abandoned = self.crash_after_config_creation(target)
            journal_directory = target / ".x86qw/gameplay/ktx-runtime-configs"
            journal = next(journal_directory.glob("*.json"))
            document = json.loads(journal.read_text(encoding="utf-8"))
            staging = target.joinpath(*Path(document["config"]["staging_path"]).parts)
            staging.mkdir()
            sentinel = staging / "personal.txt"
            sentinel.write_bytes(b"personal\n")

            with self.assertRaisesRegex(gameplay.InstallerError, "journal"):
                gameplay.write_ktx_runtime_config(target, ())

            self.assertFalse(abandoned.exists())
            self.assertTrue(journal.is_file())
            self.assertEqual(b"personal\n", sentinel.read_bytes())

    def test_release_keeps_journal_when_staging_cleanup_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            current = gameplay.write_ktx_runtime_config(target, ())
            current.ownership.staging.mkdir()
            sentinel = current.ownership.staging / "personal.txt"
            sentinel.write_bytes(b"personal\n")

            self.assertFalse(gameplay.remove_ktx_runtime_config(current))

            self.assertFalse(current.path.exists())
            self.assertTrue(current.ownership.journal.is_file())
            self.assertEqual(b"personal\n", sentinel.read_bytes())

    def test_concurrent_preparation_preserves_live_controller_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    (
                        "import pathlib, sys, time; import gameplay; "
                        "config = gameplay.write_ktx_runtime_config("
                        "pathlib.Path(sys.argv[1]), ((\"k_fb_name_0\", \"Luffy\"),)); "
                        "print(config.path, flush=True); time.sleep(60)"
                    ),
                    str(target),
                ],
                env=self.child_environment(),
                stdout=subprocess.PIPE,
                text=True,
            )
            assert child.stdout is not None
            active = Path(child.stdout.readline().strip())
            try:
                self.assertTrue(active.is_file())
                current = gameplay.write_ktx_runtime_config(target, ())
                try:
                    self.assertTrue(active.is_file())
                    self.assertNotEqual(active, current.path)
                    self.assertFalse((target / ".x86qw/sessions/active.lock").exists())
                finally:
                    self.assertTrue(gameplay.remove_ktx_runtime_config(current))
            finally:
                child.terminate()
                child.wait(timeout=10)
                child.stdout.close()

            recovered = gameplay.write_ktx_runtime_config(target, ())
            try:
                self.assertFalse(active.exists())
            finally:
                self.assertTrue(gameplay.remove_ktx_runtime_config(recovered))

    def test_inconclusive_controller_preserves_config_and_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            (target / "qw").mkdir(parents=True)
            current = gameplay.write_ktx_runtime_config(target, ())
            journal = current.ownership.journal
            try:
                with mock.patch.object(
                    runtime_configs,
                    "probe_expected_process",
                    return_value=ProcessProbe("inconclusive", detail="simulated"),
                ):
                    with self.assertRaisesRegex(
                        gameplay.InstallerError, "não pôde ser confirmado",
                    ):
                        gameplay.write_ktx_runtime_config(target, ())
                self.assertTrue(current.path.is_file())
                self.assertTrue(journal.is_file())
            finally:
                self.assertTrue(gameplay.remove_ktx_runtime_config(current))


if __name__ == "__main__":
    unittest.main()
