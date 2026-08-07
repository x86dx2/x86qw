from __future__ import annotations

import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


class InstallStateModelTests(unittest.TestCase):
    def test_format_two_round_trip_preserves_typed_fields_and_document(self) -> None:
        """A dict-only parser could reorder, drop, or coerce persisted selections."""

        spec = importlib.util.find_spec("x86qw_runtime.state")
        self.assertIsNotNone(spec, "install state must be owned by x86qw_runtime")
        state_module = importlib.import_module("x86qw_runtime.state")
        document = {
            "format": 2,
            "project": "x86qw",
            "profile": "custom",
            "requested_components": ["qtv", "ktx"],
            "recorded_components": ["qtv", "ktx"],
            "known_components": ["ktx", "qtv", "mvdsv"],
            "capabilities": [],
            "component_fingerprint": (
                "f17c8a303df4b60be4045ad8457ae4ca91df46804da78393132656678827ca9d"
            ),
        }

        state = state_module.parse_install_state(
            document,
            allowed_profiles={"none", "custom", "essential"},
            allowed_capabilities=frozenset(),
        )

        self.assertEqual(state.format, 2)
        self.assertEqual(state.profile, "custom")
        self.assertEqual(state.requested_components, ("qtv", "ktx"))
        self.assertEqual(state.recorded_components, ("qtv", "ktx"))
        self.assertEqual(state.known_components, ("ktx", "qtv", "mvdsv"))
        self.assertEqual(state.capabilities, ())
        self.assertEqual(state.to_document(), document)

    def test_format_two_serialization_matches_the_existing_canonical_bytes(self) -> None:
        """A codec change must not silently rewrite the persisted state contract."""

        state_module = importlib.import_module("x86qw_runtime.state")
        document = {
            "format": 2,
            "project": "x86qw",
            "profile": "none",
            "requested_components": [],
            "recorded_components": [],
            "known_components": ["ktx"],
            "capabilities": [],
            "component_fingerprint": (
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            ),
        }
        state = state_module.parse_install_state(
            document,
            allowed_profiles={"none", "custom"},
            allowed_capabilities=frozenset(),
        )
        self.assertTrue(
            hasattr(state_module, "serialize_install_state"),
            "runtime state must own its canonical codec",
        )

        payload = state_module.serialize_install_state(state)

        self.assertEqual(
            payload,
            (
                b'{\n  "capabilities": [],\n'
                b'  "component_fingerprint": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",\n'
                b'  "format": 2,\n  "known_components": [\n    "ktx"\n  ],\n'
                b'  "profile": "none",\n  "project": "x86qw",\n'
                b'  "recorded_components": [],\n  "requested_components": []\n}\n'
            ),
        )

    def test_format_one_round_trip_does_not_invent_format_two_fields(self) -> None:
        """Reading historical state must be pure until migration is explicitly planned."""

        state_module = importlib.import_module("x86qw_runtime.state")
        historical = {
            "format": 1,
            "project": "x86qw",
            "profile": "custom",
            "requested_components": ["ktx", "qtv"],
            "recorded_components": ["ktx", "qtv"],
            "known_components": ["ktx", "qtv", "nquake-sounds"],
        }

        try:
            state = state_module.parse_install_state(
                historical,
                allowed_profiles={"none", "custom", "essential"},
                allowed_capabilities=frozenset(),
            )
        except state_module.StateError as error:
            self.fail(f"historical format was rejected: {error}")

        self.assertEqual(state.format, 1)
        self.assertEqual(state.capabilities, ())
        self.assertIsNone(state.component_fingerprint)
        self.assertEqual(state.to_document(), historical)

    def test_state_file_read_returns_the_typed_model(self) -> None:
        """Manager must not reimplement state parsing around a raw dictionary."""

        state_module = importlib.import_module("x86qw_runtime.state")
        self.assertTrue(
            hasattr(state_module, "read_install_state"),
            "runtime state must own its bounded filesystem read",
        )
        document = {
            "format": 2,
            "project": "x86qw",
            "profile": "none",
            "requested_components": [],
            "recorded_components": [],
            "known_components": ["ktx"],
            "capabilities": [],
            "component_fingerprint": (
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps(document), encoding="utf-8")

            state = state_module.read_install_state(
                path,
                allowed_profiles={"none", "custom"},
                allowed_capabilities=frozenset(),
            )

        self.assertEqual(state.to_document(), document)

    def test_state_file_larger_than_the_explicit_limit_is_rejected(self) -> None:
        """A valid JSON prefix plus unlimited padding must not bypass the read bound."""

        state_module = importlib.import_module("x86qw_runtime.state")
        document = {
            "format": 2,
            "project": "x86qw",
            "profile": "none",
            "requested_components": [],
            "recorded_components": [],
            "known_components": [],
            "capabilities": [],
            "component_fingerprint": (
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            ),
        }
        encoded = json.dumps(document).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_bytes(encoded + b" " * 100)

            with self.assertRaises(state_module.StateError):
                state_module.read_install_state(
                    path,
                    allowed_profiles={"none", "custom"},
                    allowed_capabilities=frozenset(),
                    maximum_size=len(encoded) + 2,
                )


class InstallStateMigrationTests(unittest.TestCase):
    def test_format_one_migration_is_pure_idempotent_and_preserves_order(self) -> None:
        """In-place or repeated migration could lose custom component selection."""

        spec = importlib.util.find_spec("x86qw_runtime.migrations")
        self.assertIsNotNone(spec, "state migrations must be owned by x86qw_runtime")
        migrations = importlib.import_module("x86qw_runtime.migrations")
        state_module = importlib.import_module("x86qw_runtime.state")
        original_document = {
            "format": 1,
            "project": "x86qw",
            "profile": "custom",
            "requested_components": ["ktx", "old-clan", "nquake-sounds"],
            "recorded_components": ["ktx", "old-clan", "nquake-sounds"],
            "known_components": ["ktx", "old-clan", "clan-arena", "nquake-sounds"],
        }
        original = state_module.parse_install_state(
            original_document,
            allowed_profiles={"none", "custom", "complete"},
            allowed_capabilities=frozenset(),
        )

        migrated = migrations.migrate_install_state(
            original,
            replacements={"old-clan": "clan-arena"},
            removals={"nquake-sounds"},
            allowed_profiles={"none", "custom", "complete"},
            allowed_capabilities=frozenset(),
        )
        migrated_again = migrations.migrate_install_state(
            migrated,
            replacements={"old-clan": "clan-arena"},
            removals={"nquake-sounds"},
            allowed_profiles={"none", "custom", "complete"},
            allowed_capabilities=frozenset(),
        )

        expected = {
            "format": 2,
            "project": "x86qw",
            "profile": "custom",
            "state_version": 2,
            "min_cli_version": "0.7.0",
            "requested_components": ["ktx", "clan-arena"],
            "recorded_components": ["ktx", "clan-arena"],
            "known_components": ["ktx", "clan-arena"],
            "capabilities": [],
            "component_fingerprint": (
                "250136492d0afcd922be251946e1abba06bfd428c5be5db23fd9b14a797d18f1"
            ),
        }
        self.assertEqual(migrated.to_document(), expected)
        self.assertEqual(migrated_again.to_document(), expected)
        self.assertEqual(original.to_document(), original_document)


if __name__ == "__main__":
    unittest.main()
