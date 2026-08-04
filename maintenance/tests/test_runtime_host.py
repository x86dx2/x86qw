import hashlib
import struct
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

try:
    from x86qw_runtime.platform import host
except ImportError:
    host = None


class HostPlatformAdapterTests(unittest.TestCase):
    SERVICE_RUNTIMES = {
        runtime: {
            "platforms": [
                {"system": "macos", "architecture": "arm64", "variant": "macos-arm64"},
                {"system": "linux", "architecture": "amd64", "variant": "linux-amd64"},
                {"system": "windows", "architecture": "amd64", "variant": "windows-x64"},
            ],
        }
        for runtime in ("mvdsv", "qtv", "qwfwd")
    }
    HOST_PLATFORMS = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}
    ARCHITECTURE_ALIASES = {
        "arm64": ["arm64", "aarch64"],
        "amd64": ["amd64", "x86_64", "x64"],
    }

    def test_native_host_facts_are_exposed_by_the_adapter(self):
        self.assertIsNotNone(host, "the canonical host adapter is missing")
        assert host is not None
        for name in ("system", "machine", "python_version"):
            self.assertTrue(callable(getattr(host, name, None)), name)
        with mock.patch.object(
            host.platform, "system", return_value="Darwin",
        ), mock.patch.object(
            host.platform, "machine", return_value="arm64",
        ), mock.patch.object(
            host.platform, "python_version", return_value="3.13.5",
        ):
            self.assertEqual("Darwin", host.system())
            self.assertEqual("arm64", host.machine())
            self.assertEqual("3.13.5", host.python_version())

    def test_service_variant_normalizes_host_platforms_and_architecture_aliases(self):
        self.assertIsNotNone(host, "the canonical host adapter is missing")
        assert host is not None
        cases = (
            ("Darwin", "aarch64", "macos-arm64"),
            ("Linux", "x64", "linux-amd64"),
            ("Windows", "AMD64", "windows-x64"),
        )
        for system, machine, expected in cases:
            with self.subTest(system=system, machine=machine):
                self.assertEqual(
                    expected,
                    host.service_runtime_variant(
                        self.SERVICE_RUNTIMES,
                        self.ARCHITECTURE_ALIASES,
                        self.HOST_PLATFORMS,
                        runtime_id="qtv",
                        system=system,
                        machine=machine,
                    ),
                )

    def test_service_variant_rejects_unsupported_or_ambiguous_host(self):
        self.assertIsNotNone(host, "the canonical host adapter is missing")
        assert host is not None
        with self.assertRaisesRegex(host.HostPlatformError, "indisponível"):
            host.service_runtime_variant(
                self.SERVICE_RUNTIMES,
                self.ARCHITECTURE_ALIASES,
                self.HOST_PLATFORMS,
                runtime_id="qtv",
                system="Darwin",
                machine="x86_64",
            )
        ambiguous = {
            **self.SERVICE_RUNTIMES,
            "qtv": {
                "platforms": [
                    {"system": "linux", "architecture": "amd64", "variant": "linux-amd64"},
                    {"system": "linux", "architecture": "amd64", "variant": "linux-alt"},
                ],
            },
        }
        with self.assertRaisesRegex(host.HostPlatformError, "indisponível"):
            host.service_runtime_variant(
                ambiguous,
                self.ARCHITECTURE_ALIASES,
                self.HOST_PLATFORMS,
                runtime_id="qtv",
                system="Linux",
                machine="x86_64",
            )

    def test_service_executable_rejects_unsafe_or_nonexecutable_paths(self):
        self.assertIsNotNone(host, "the canonical host adapter is missing")
        assert host is not None
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "service"
            executable.write_bytes(b"runtime")
            executable.chmod(0o755)
            self.assertEqual(
                executable,
                host.service_runtime_executable(executable, os_name="posix"),
            )

            executable.chmod(0o600)
            with mock.patch.object(host.os, "access", return_value=False):
                with self.assertRaisesRegex(
                    host.HostPlatformError, "permissão de execução",
                ):
                    host.service_runtime_executable(executable, os_name="posix")
            self.assertEqual(
                executable,
                host.service_runtime_executable(executable, os_name="nt"),
            )

            directory = root / "directory"
            directory.mkdir()
            with self.assertRaisesRegex(host.HostPlatformError, "ausente ou inseguro"):
                host.service_runtime_executable(directory, os_name="posix")

            linked = root / "linked"
            try:
                linked.symlink_to(executable)
            except OSError as error:
                self.skipTest(f"symlinks indisponíveis neste runner: {error}")
            with self.assertRaisesRegex(host.HostPlatformError, "ausente ou inseguro"):
                host.service_runtime_executable(linked, os_name="posix")

    def test_client_executable_rejects_a_portable_symlink(self):
        self.assertIsNotNone(host, "the canonical host adapter is missing")
        assert host is not None
        with self.subTest(system="Linux"):
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                executable = root / "ezquake.AppImage"
                executable.write_bytes(b"runtime")
                self.assertEqual(
                    executable,
                    host.client_executable(executable, system="Linux"),
                )
                linked = root / "linked.AppImage"
                linked.symlink_to(executable)
                with self.assertRaisesRegex(
                    host.HostPlatformError, "executável",
                ):
                    host.client_executable(linked, system="Linux")

    def test_launch_target_rejects_a_replaced_or_modified_executable(self):
        """The program checked by the caller must still be the one spawned."""

        self.assertIsNotNone(host, "the canonical host adapter is missing")
        assert host is not None
        self.assertIsNotNone(
            getattr(host, "executable_launch_target", None),
            "the canonical executable launch contract is missing",
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "ezquake"
            original = b"verified runtime\n"
            executable.write_bytes(original)
            executable.chmod(0o755)
            target = host.executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(original).hexdigest(),
            )

            replacement = root / "replacement"
            replacement.write_bytes(b"hostile runtime!\n")
            replacement.chmod(0o755)
            replacement.replace(executable)
            with self.assertRaisesRegex(host.HostPlatformError, "mudou"):
                host.revalidate_launch_target(target)

            executable.write_bytes(original)
            executable.chmod(0o755)
            target = host.executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(original).hexdigest(),
            )
            executable.write_bytes(b"modified runtime\n")
            with self.assertRaisesRegex(host.HostPlatformError, "mudou"):
                host.revalidate_launch_target(target)

    def test_macos_launch_target_binds_every_bundle_directory(self):
        """Replacing a validated bundle parent must not redirect the launch."""

        self.assertIsNotNone(host, "the canonical host adapter is missing")
        assert host is not None
        self.assertIsNotNone(
            getattr(host, "client_launch_target", None),
            "the canonical client launch contract is missing",
        )
        with TemporaryDirectory() as temporary:
            app = Path(temporary) / "ezQuake.app"
            executable = app / "Contents/MacOS/ezQuake"
            executable.parent.mkdir(parents=True)
            payload = b"verified Mach-O fixture"
            executable.write_bytes(payload)
            executable.chmod(0o755)
            target = host.client_launch_target(
                app,
                system="Darwin",
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )

            original = app / "Contents/MacOS-original"
            executable.parent.rename(original)
            try:
                executable.parent.symlink_to(original, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlinks indisponíveis neste runner: {error}")
            with self.assertRaisesRegex(host.HostPlatformError, "mudou"):
                host.revalidate_launch_target(target)

    def test_portable_binary_inspection_validates_linux_and_windows_formats(self):
        """Client identity must be derived only after the declared native format."""

        self.assertIsNotNone(host, "the canonical host adapter is missing")
        assert host is not None
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            linux = root / "ezquake.AppImage"
            linux_payload = bytearray(20)
            linux_payload[:5] = b"\x7fELF\x02"
            struct.pack_into("<H", linux_payload, 18, 62)
            linux.write_bytes(linux_payload)
            linux.chmod(0o755)

            windows = root / "ezquake.exe"
            windows_payload = bytearray(0x40 + 26)
            windows_payload[:2] = b"MZ"
            struct.pack_into("<I", windows_payload, 0x3C, 0x40)
            windows_payload[0x40:0x44] = b"PE\0\0"
            struct.pack_into("<H", windows_payload, 0x44, 0x8664)
            struct.pack_into("<H", windows_payload, 0x40 + 24, 0x20B)
            windows.write_bytes(windows_payload)

            self.assertEqual(
                hashlib.sha256(linux_payload).hexdigest(),
                host.inspect_portable_binary(linux, platform_id="linux", os_name="posix"),
            )
            self.assertEqual(
                hashlib.sha256(windows_payload).hexdigest(),
                host.inspect_portable_binary(windows, platform_id="windows", os_name="nt"),
            )

    def test_portable_binary_inspection_rejects_wrong_format_and_permissions(self):
        self.assertIsNotNone(host, "the canonical host adapter is missing")
        assert host is not None
        with TemporaryDirectory() as temporary:
            binary = Path(temporary) / "client"
            binary.write_bytes(b"not a native client")
            binary.chmod(0o755)
            with self.assertRaisesRegex(host.HostPlatformError, "Linux"):
                host.inspect_portable_binary(
                    binary, platform_id="linux", os_name="posix",
                )

            payload = bytearray(20)
            payload[:5] = b"\x7fELF\x02"
            struct.pack_into("<H", payload, 18, 62)
            binary.write_bytes(payload)
            binary.chmod(0o600)
            with mock.patch.object(host.os, "access", return_value=False):
                with self.assertRaisesRegex(host.HostPlatformError, "executable"):
                    host.inspect_portable_binary(
                        binary, platform_id="linux", os_name="posix",
                    )

    def test_user_cache_directory_follows_each_native_contract(self):
        self.assertIsNotNone(host, "the canonical host adapter is missing")
        assert host is not None
        cases = (
            ("Darwin", {}, Path("/Users/test"), Path("/private/cache/x86qw")),
            (
                "Windows", {"LOCALAPPDATA": "C:/Users/test/AppData/Local"},
                Path("C:/Users/test"), Path("C:/Users/test/AppData/Local/x86qw"),
            ),
            (
                "Linux", {"XDG_CACHE_HOME": "/home/test/.local/cache"},
                Path("/home/test"), Path("/home/test/.local/cache/x86qw"),
            ),
            ("Linux", {}, Path("/home/test"), Path("/home/test/.cache/x86qw")),
        )
        for system, environment, home, expected in cases:
            with self.subTest(system=system, environment=environment), mock.patch.object(
                host.subprocess, "run",
                return_value=subprocess.CompletedProcess(
                    ["getconf"], 0, stdout=b"/private/cache/\n", stderr=b"",
                ),
            ):
                self.assertEqual(
                    expected,
                    host.user_cache_directory(
                        "x86qw",
                        system=system,
                        environment=environment,
                        home=home,
                    ),
                )

    def test_macos_cache_probe_fails_conservatively(self):
        self.assertIsNotNone(host, "the canonical host adapter is missing")
        assert host is not None
        with mock.patch.object(
            host.subprocess, "run",
            return_value=subprocess.CompletedProcess(
                ["getconf"], 1, stdout=b"", stderr=b"probe failed",
            ),
        ), self.assertRaisesRegex(host.HostPlatformError, "cache.*macOS"):
            host.user_cache_directory(
                "x86qw", system="Darwin", environment={}, home=Path("/Users/test"),
            )


if __name__ == "__main__":
    unittest.main()
