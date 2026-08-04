import unittest
import subprocess
from unittest import mock

try:
    from x86qw_runtime.platform import macos
except ImportError:
    macos = None


class MacOSAdapterTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
