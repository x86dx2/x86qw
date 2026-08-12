import plistlib
import shutil
import struct
import unittest
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

try:
    from x86qw_runtime.platform import macos
except ImportError:
    macos = None


class MacOSAdapterTests(unittest.TestCase):
    @staticmethod
    def universal_binary(*, fat64: bool = False) -> bytes:
        """A minimal universal bundle with real arm64 and x86_64 Mach-O slices."""

        slice_size = 64
        x86_offset = 4096
        arm_offset = x86_offset + slice_size
        binary = bytearray(arm_offset + slice_size)
        if fat64:
            struct.pack_into(">II", binary, 0, 0xCAFEBABF, 2)
            struct.pack_into(
                ">IIQQII", binary, 8, 0x01000007, 0, x86_offset, slice_size, 0, 0,
            )
            struct.pack_into(
                ">IIQQII", binary, 40, 0x0100000C, 0, arm_offset, slice_size, 0, 0,
            )
        else:
            struct.pack_into(">II", binary, 0, 0xCAFEBABE, 2)
            struct.pack_into(">IIIII", binary, 8, 0x01000007, 0, x86_offset, slice_size, 0)
            struct.pack_into(">IIIII", binary, 28, 0x0100000C, 0, arm_offset, slice_size, 0)
        struct.pack_into("<II", binary, x86_offset, 0xFEEDFACF, 0x01000007)
        struct.pack_into("<II", binary, arm_offset, 0xFEEDFACF, 0x0100000C)
        return bytes(binary)

    @classmethod
    def write_bundle(cls, app: Path, *, binary: bytes | None = None) -> None:
        binary = cls.universal_binary() if binary is None else binary
        files = {
            "Contents/Info.plist": plistlib.dumps({
                "CFBundleShortVersionString": "3.6.9",
                "CFBundleVersion": "3.6.9",
            }),
            "Contents/MacOS/ezQuake": binary,
            "Contents/_CodeSignature/CodeResources": b"signature",
        }
        for relative, payload in files.items():
            destination = app / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)

    def test_bundle_inspection_and_launch_reject_intermediate_symlinks(self):
        """Bundle members must never be reached through a redirected directory."""

        assert macos is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (
                Path("Contents"),
                Path("Contents/MacOS"),
                Path("Contents/_CodeSignature"),
            ):
                with self.subTest(relative=relative):
                    app = root / f"{relative.name}.app"
                    self.write_bundle(app)
                    boundary = app / relative
                    external = root / f"{relative.name}.external"
                    shutil.copytree(boundary, external)
                    shutil.rmtree(boundary)
                    boundary.symlink_to(external, target_is_directory=True)
                    with self.assertRaisesRegex(
                        macos.MacOSAdapterError, "bundle macOS",
                    ):
                        macos.inspect_ezquake_bundle(app, verify_signature=False)
                    if relative != Path("Contents/_CodeSignature"):
                        with self.assertRaisesRegex(
                            macos.MacOSAdapterError, "bundle macOS",
                        ):
                            macos.app_executable(app)

    def test_bundle_inspection_requires_real_bounded_universal_slices(self):
        """FAT headers alone cannot prove a universal executable is launchable."""

        assert macos is not None
        corruptions = (
            ("zero-size", 20, 0),
            ("outside-file", 36, 9000),
            ("overlap", 36, 4096),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, offset, value in corruptions:
                with self.subTest(corruption=name):
                    binary = bytearray(self.universal_binary())
                    struct.pack_into(">I", binary, offset, value)
                    app = root / f"{name}.app"
                    self.write_bundle(app, binary=bytes(binary))
                    with self.assertRaisesRegex(macos.MacOSAdapterError, "Mach-O"):
                        macos.inspect_ezquake_bundle(app, verify_signature=False)

            wrong_cputype = bytearray(self.universal_binary())
            struct.pack_into("<I", wrong_cputype, 4096 + 4, 0x0100000C)
            app = root / "wrong-cputype.app"
            self.write_bundle(app, binary=bytes(wrong_cputype))
            with self.assertRaisesRegex(macos.MacOSAdapterError, "Mach-O"):
                macos.inspect_ezquake_bundle(app, verify_signature=False)

            invalid_magic = bytearray(self.universal_binary())
            invalid_magic[4096:4100] = b"\0\0\0\0"
            app = root / "invalid-magic.app"
            self.write_bundle(app, binary=bytes(invalid_magic))
            with self.assertRaisesRegex(macos.MacOSAdapterError, "Mach-O"):
                macos.inspect_ezquake_bundle(app, verify_signature=False)

    def test_bundle_inspection_accepts_real_fat32_and_fat64_slices(self):
        """Both supported FAT layouts must contain inspectable Mach-O slices."""

        assert macos is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for fat64 in (False, True):
                with self.subTest(fat64=fat64):
                    app = root / ("fat64.app" if fat64 else "fat32.app")
                    self.write_bundle(app, binary=self.universal_binary(fat64=fat64))
                    version, digest = macos.inspect_ezquake_bundle(
                        app, verify_signature=False,
                    )
                    self.assertEqual("3.6.9", version)
                    self.assertEqual(64, len(digest))

    def test_bundle_inspection_rejects_symlinked_security_boundaries(self):
        """No bundle identity may be derived through a replaceable symlink."""

        assert macos is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real.app"
            self.write_bundle(real)
            app_link = root / "linked.app"
            app_link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(macos.MacOSAdapterError, "bundle macOS"):
                macos.inspect_ezquake_bundle(app_link, verify_signature=False)

            for relative in (
                "Contents/Info.plist",
                "Contents/_CodeSignature/CodeResources",
            ):
                with self.subTest(relative=relative):
                    app = root / (Path(relative).name + ".app")
                    self.write_bundle(app)
                    boundary = app / relative
                    payload = boundary.read_bytes()
                    boundary.unlink()
                    external = root / (boundary.name + ".external")
                    external.write_bytes(payload)
                    boundary.symlink_to(external)
                    with self.assertRaisesRegex(
                        macos.MacOSAdapterError, "bundle macOS",
                    ):
                        macos.inspect_ezquake_bundle(app, verify_signature=False)

    def test_app_executable_never_follows_a_bundle_symlink(self):
        """Launching must resolve the executable only inside the selected app."""

        assert macos is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "ezQuake.app"
            self.write_bundle(app)
            executable = app / "Contents/MacOS/ezQuake"
            self.assertEqual(executable, macos.app_executable(app))

            linked = root / "linked.app"
            linked.symlink_to(app, target_is_directory=True)
            with self.assertRaisesRegex(macos.MacOSAdapterError, "bundle macOS"):
                macos.app_executable(linked)

    def test_bundle_inspection_normalizes_a_malformed_plist(self):
        """Malformed plist input must not leak an untyped parser exception."""

        assert macos is not None
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "ezQuake.app"
            self.write_bundle(app)
            (app / "Contents/Info.plist").write_bytes(b"not a plist")

            with self.assertRaisesRegex(
                macos.MacOSAdapterError, "Info.plist inválido",
            ):
                macos.inspect_ezquake_bundle(app, verify_signature=False)

    def test_sandbox_detection_parses_entitlements_instead_of_diagnostics(self):
        """A diagnostic mentioning app-sandbox must not become an entitlement."""

        assert macos is not None
        entitlements = plistlib.dumps({
            "com.apple.security.app-sandbox": False,
        })
        diagnostic = b"warning: com.apple.security.app-sandbox mentioned\n"
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "ezQuake.app"
            self.write_bundle(app)
            with mock.patch.object(
                macos.sys, "platform", "darwin",
            ), mock.patch.object(
                macos.subprocess, "run",
                return_value=subprocess.CompletedProcess(
                    ["codesign"], 0, stdout=b"", stderr=diagnostic + entitlements,
                ),
            ):
                self.assertFalse(macos.app_is_sandboxed(app))

    def test_sandbox_detection_accepts_signed_bundle_without_entitlements(self):
        """An upstream bundle without an entitlement plist is not sandboxed."""

        assert macos is not None
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "ezQuake.app"
            self.write_bundle(app)
            diagnostic = (
                b"Executable=/tmp/ezQuake.app/Contents/MacOS/ezQuake\n"
                b"warning: Specifying ':' in the path is deprecated\n"
            )
            with mock.patch.object(
                macos.sys, "platform", "darwin",
            ), mock.patch.object(
                macos.subprocess, "run",
                return_value=subprocess.CompletedProcess(
                    ["codesign"], 0, stdout=b"", stderr=diagnostic,
                ),
            ):
                self.assertFalse(macos.app_is_sandboxed(app))

    def test_nightly_preparation_updates_plist_and_runs_codesign_contract(self):
        """Nightly preparation owns plist mutation and both codesign phases."""

        assert macos is not None
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "ezQuake.app"
            self.write_bundle(app)
            sandbox_states = iter((True, False))
            commands = []

            changed = macos.prepare_nightly_bundle(
                app,
                sandbox_probe=lambda _app: next(sandbox_states),
                command_runner=lambda arguments: commands.append(arguments),
            )

            metadata = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            self.assertEqual((True, True), changed)
            self.assertIs(
                False,
                metadata["NSPrefersDisplaySafeAreaCompatibilityMode"],
            )
            self.assertEqual([
                ["codesign", "--force", "--deep", "--sign", "-", str(app)],
                ["codesign", "--verify", "--deep", "--strict", str(app)],
            ], commands)

    def test_nightly_preparation_preserves_the_plist_only_contract(self):
        """Preparation may update staged metadata before bundle members exist."""

        assert macos is not None
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "ezQuake.app"
            contents = app / "Contents"
            contents.mkdir(parents=True)
            (contents / "Info.plist").write_bytes(plistlib.dumps({}))
            sandboxed = iter((True, False))

            changed = macos.prepare_nightly_bundle(
                app,
                sandbox_probe=lambda _app: next(sandboxed),
                command_runner=lambda _arguments: None,
            )

            self.assertEqual((True, True), changed)

    def test_process_probe_distinguishes_running_absent_and_inconclusive(self):
        """Only pgrep's exact absent outcome may let installation continue."""

        self.assertIsNotNone(macos, "the canonical macOS adapter is missing")
        assert macos is not None
        self.assertTrue(
            hasattr(macos, "ensure_process_absent"),
            "the process probe is missing from the macOS adapter",
        )
        outcomes = (
            (0, macos.MacOSAdapterError, "Feche o ezQuake"),
            (1, None, None),
            (2, macos.MacOSAdapterError, "Não foi possível verificar"),
        )
        for returncode, error_type, message in outcomes:
            with self.subTest(returncode=returncode), mock.patch.object(
                macos.sys, "platform", "darwin",
            ), mock.patch.object(
                macos.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    ["pgrep"], returncode, stdout=b"", stderr=b"probe failed",
                ),
            ):
                if error_type is None:
                    macos.ensure_process_absent("ezQuake")
                else:
                    with self.assertRaisesRegex(error_type, message):
                        macos.ensure_process_absent("ezQuake")

    def test_preference_roundtrip_restores_only_the_managed_keys(self):
        """Rollback must not overwrite an unrelated preference changed meanwhile."""

        self.assertIsNotNone(macos, "the canonical macOS adapter is missing")
        assert macos is not None
        domain = "io.ezQuake"
        keys = ("basedir", "version", "NSOSPLastRootDirectory")
        state = {
            "basedir": "/Games/x86QW",
            "version": 7,
            "NSOSPLastRootDirectory": b"bookmark",
            "volume": 0.5,
        }

        def export(_domain):
            self.assertEqual(domain, _domain)
            return dict(state)

        def publish(_domain, values):
            self.assertEqual(domain, _domain)
            state.clear()
            state.update(values)

        with mock.patch.object(macos, "_export_preference_domain", side_effect=export), mock.patch.object(
            macos, "_publish_preference_domain", side_effect=publish,
        ):
            snapshot = macos.snapshot_preference_keys(domain, keys)
            macos.clear_preference_keys(snapshot)
            self.assertEqual({"volume": 0.5}, state)
            state["volume"] = 0.75
            macos.restore_preference_keys(snapshot)

        self.assertEqual({
            "basedir": "/Games/x86QW",
            "version": 7,
            "NSOSPLastRootDirectory": b"bookmark",
            "volume": 0.75,
        }, state)

    def test_partial_preference_publish_is_restored_before_reporting_failure(self):
        """A failed defaults import must not leave selected keys half-cleared."""

        self.assertIsNotNone(macos, "the canonical macOS adapter is missing")
        assert macos is not None
        domain = "io.ezQuake"
        keys = ("basedir", "version", "NSOSPLastRootDirectory")
        original = {
            "basedir": "/Games/x86QW",
            "version": 7,
            "NSOSPLastRootDirectory": b"bookmark",
            "volume": 0.5,
        }
        state = dict(original)
        attempts = 0

        def export(_domain):
            return dict(state)

        def publish(_domain, values):
            nonlocal attempts
            attempts += 1
            state.clear()
            state.update(values)
            if attempts == 1:
                raise macos.MacOSAdapterError("simulated partial import")

        with mock.patch.object(macos, "_export_preference_domain", side_effect=export), mock.patch.object(
            macos, "_publish_preference_domain", side_effect=publish,
        ):
            snapshot = macos.snapshot_preference_keys(domain, keys)
            with self.assertRaisesRegex(
                macos.MacOSAdapterError, "simulated partial import",
            ):
                macos.clear_preference_keys(snapshot)

        self.assertEqual(original, state)

    def test_preference_change_after_snapshot_blocks_the_clear(self):
        """A concurrent bookmark change must never be erased by a stale plan."""

        self.assertIsNotNone(macos, "the canonical macOS adapter is missing")
        assert macos is not None
        domain = "io.ezQuake"
        keys = ("basedir", "version", "NSOSPLastRootDirectory")
        state = {"basedir": "/Games/original", "volume": 0.5}

        def publish(_domain, values):
            state.clear()
            state.update(values)

        with mock.patch.object(
            macos, "_export_preference_domain", side_effect=lambda _domain: dict(state),
        ), mock.patch.object(
            macos, "_publish_preference_domain",
            side_effect=publish,
        ):
            snapshot = macos.snapshot_preference_keys(domain, keys)
            state["basedir"] = "/Games/new-choice"
            with self.assertRaisesRegex(
                macos.MacOSAdapterError, "mudaram depois da confirmação",
            ):
                macos.clear_preference_keys(snapshot)

        self.assertEqual({"basedir": "/Games/new-choice", "volume": 0.5}, state)

    def test_clearing_absent_managed_preferences_is_a_noop(self):
        """A clean first install must not import an empty defaults domain."""

        self.assertIsNotNone(macos, "the canonical macOS adapter is missing")
        assert macos is not None
        domain = "io.ezQuake"
        keys = ("basedir", "version", "NSOSPLastRootDirectory")

        with mock.patch.object(
            macos, "_export_preference_domain", return_value={},
        ), mock.patch.object(
            macos, "_publish_preference_domain",
        ) as publish:
            snapshot = macos.snapshot_preference_keys(domain, keys)
            self.assertEqual(snapshot, macos.clear_preference_keys(snapshot))

        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
