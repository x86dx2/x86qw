import importlib.util
import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from x86qw_runtime.platform import display
except ImportError:
    display = None


ROOT = Path(__file__).resolve().parents[2]
GAMEPLAY_SPEC = importlib.util.spec_from_file_location(
    "gameplay_display_boundary",
    ROOT / "dist/installer/bin/gameplay.py",
)
gameplay = importlib.util.module_from_spec(GAMEPLAY_SPEC)
assert GAMEPLAY_SPEC.loader is not None
sys.modules[GAMEPLAY_SPEC.name] = gameplay
GAMEPLAY_SPEC.loader.exec_module(gameplay)


def display_profile(main: dict[str, object]) -> str:
    return json.dumps({
        "SPDisplaysDataType": [{"spdisplays_ndrvs": [main]}],
    })


class MacOSDisplayAdapterTests(unittest.TestCase):
    def test_macos_host_decision_normalizes_native_platform_names(self):
        self.assertIsNotNone(display, "the canonical display adapter is missing")
        assert display is not None
        decision = getattr(display, "is_macos_host", None)
        self.assertTrue(callable(decision), "the macOS host decision is missing")
        self.assertTrue(decision(platform_name="darwin"))
        self.assertFalse(decision(platform_name="linux"))
        self.assertFalse(decision(platform_name="win32"))

    def test_macos_main_display_returns_the_primary_display_fact(self):
        """The adapter must expose the complete primary-display fact."""

        self.assertIsNotNone(display, "the canonical display adapter is missing")
        assert display is not None
        primary = {
            "_name": "Built-in Retina Display",
            "spdisplays_main": "spdisplays_yes",
            "spdisplays_connection_type": "spdisplays_internal",
            "spdisplays_pixelresolution": "spdisplays_3024x1964Retina",
        }
        with mock.patch.object(
            display.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                ["system_profiler"], 0, stdout=display_profile(primary), stderr="",
            ),
        ):
            self.assertEqual(primary, display.macos_main_display())

    def test_macos_main_display_rejects_a_malformed_profile(self):
        """A non-list display payload must not be treated as a monitor fact."""

        self.assertIsNotNone(display, "the canonical display adapter is missing")
        assert display is not None
        with mock.patch.object(
            display.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                ["system_profiler"], 0,
                stdout=json.dumps({"SPDisplaysDataType": {}}), stderr="",
            ),
        ), self.assertRaisesRegex(display.DisplayAdapterError, "lista de monitores ausente"):
            display.macos_main_display()

    def test_macos_main_display_keeps_a_noninternal_primary_display_as_fact(self):
        """Connection policy belongs to gameplay, not to the collector."""

        self.assertIsNotNone(display, "the canonical display adapter is missing")
        assert display is not None
        primary = {
            "_name": "External Display",
            "spdisplays_main": "spdisplays_yes",
            "spdisplays_connection_type": "spdisplays_displayport",
        }
        with mock.patch.object(
            display.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                ["system_profiler"], 0, stdout=display_profile(primary), stderr="",
            ),
        ):
            self.assertEqual(primary, display.macos_main_display())

    def test_macos_main_display_normalizes_a_command_failure(self):
        """A system_profiler failure must cross the boundary as a typed error."""

        self.assertIsNotNone(display, "the canonical display adapter is missing")
        assert display is not None
        with mock.patch.object(
            display.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, ["system_profiler"]),
        ), self.assertRaises(display.DisplayAdapterError):
            display.macos_main_display()

    def test_macos_main_display_normalizes_a_command_timeout(self):
        """A system_profiler timeout must cross the boundary as a typed error."""

        self.assertIsNotNone(display, "the canonical display adapter is missing")
        assert display is not None
        with mock.patch.object(
            display.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["system_profiler"], 8),
        ), self.assertRaises(display.DisplayAdapterError):
            display.macos_main_display()


class GameplayDisplayBoundaryTests(unittest.TestCase):
    @staticmethod
    def _player(root: Path):
        player = object.__new__(gameplay.GameplayPlayerMixin)
        player.target = root
        return player

    def test_fullscreen_configuration_obeys_the_display_adapter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            original = b'vid_fullscreen "1"\nvid_usedesktopres "1"\n'
            config.write_bytes(original)
            player = self._player(root)
            decision = getattr(gameplay, "is_macos_host", None)
            self.assertTrue(callable(decision), "gameplay does not consume the display adapter")

            with mock.patch.object(gameplay, "is_macos_host", return_value=False), \
                    mock.patch.object(gameplay.sys, "platform", "darwin"), \
                    mock.patch.object(
                        player,
                        "macos_notched_fullscreen_settings",
                        side_effect=AssertionError("macOS display policy leaked into gameplay"),
                    ):
                player.configure_macos_fullscreen()

            self.assertEqual(original, config.read_bytes())
            self.assertFalse((root / gameplay.MACOS_FULLSCREEN_LAYOUT).exists())

    def test_legacy_video_cleanup_obeys_the_display_adapter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / gameplay.LEGACY_MACOS_VIDEO_LAYOUT
            marker.parent.mkdir(parents=True)
            marker.mkdir()
            player = self._player(root)
            decision = getattr(gameplay, "is_macos_host", None)
            self.assertTrue(callable(decision), "gameplay does not consume the display adapter")

            with mock.patch.object(gameplay, "is_macos_host", return_value=False), \
                    mock.patch.object(gameplay.sys, "platform", "darwin"):
                player.remove_legacy_macos_video_layout()

            self.assertTrue(marker.is_dir())


if __name__ == "__main__":
    unittest.main()
