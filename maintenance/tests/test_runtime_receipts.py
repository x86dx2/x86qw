from __future__ import annotations

import importlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


class EzQuakeReceiptTests(unittest.TestCase):
    def test_stable_receipt_round_trip_matches_existing_tsv_bytes(self) -> None:
        """Field coercion or reordering would make installed client receipts diverge."""

        spec = importlib.util.find_spec("x86qw_runtime.receipts")
        self.assertIsNotNone(spec, "receipt codecs must be owned by x86qw_runtime")
        receipts = importlib.import_module("x86qw_runtime.receipts")
        payload = (
            b"format\t1\n"
            b"platform\tlinux\n"
            b"architecture\tx86_64\n"
            b"channel\tstable\n"
            b"selection\t3.6.9\n"
            b"install_name\tezquake-stable-x86_64.AppImage\n"
            b"bundle_version\t3.6.9\n"
            b"artifact_name\tezQuake-linux-x86_64.zip\n"
            b"artifact_url\thttps://example.invalid/ezQuake-linux-x86_64.zip\n"
            b"artifact_sha256\t" + b"a" * 64 + b"\n"
            b"binary_sha256\t" + b"b" * 64 + b"\n"
        )
        context = receipts.EzQuakeReceiptContext(
            platform="linux",
            architecture="x86_64",
            channel="stable",
            install_name="ezquake-stable-x86_64.AppImage",
            stable_archive="ezQuake-linux-x86_64.zip",
            nightly_suffix="-linux-x86_64.zip",
        )

        receipt = receipts.parse_ezquake_receipt(payload, context=context)

        self.assertEqual(receipt.format, "1")
        self.assertEqual(receipt.selection, "3.6.9")
        self.assertEqual(receipt.artifact_sha256, "a" * 64)
        self.assertEqual(receipt.to_legacy_dict()["binary_sha256"], "b" * 64)
        self.assertEqual(receipts.serialize_ezquake_receipt(receipt), payload)


class ComponentReceiptTests(unittest.TestCase):
    def test_component_receipt_and_inventory_preserve_exact_bytes_and_order(self) -> None:
        """Sorting parsed inventory or changing receipt order would break its bound hash."""

        receipts = importlib.import_module("x86qw_runtime.receipts")
        receipt_payload = (
            b"format\t1\n"
            b"component\tktx\n"
            b"selection\t1.47+x86qw.18\n"
            b"source\thttps://example.invalid/ktx.zip\n"
            b"inventory_sha256\t" + b"c" * 64 + b"\n"
        )
        inventory_payload = (
            b"qw/z-last.pk3\t" + b"d" * 64 + b"\n"
            b"qw/a-first.pk3\t" + b"e" * 64 + b"\n"
        )
        for name in (
            "parse_component_receipt",
            "parse_inventory",
            "serialize_component_receipt",
            "serialize_inventory",
        ):
            self.assertTrue(hasattr(receipts, name), f"missing runtime receipt API: {name}")

        receipt = receipts.parse_component_receipt(
            receipt_payload,
            component="ktx",
        )
        inventory = receipts.parse_inventory(inventory_payload)

        self.assertEqual(receipt.selection, "1.47+x86qw.18")
        self.assertEqual(receipt.to_legacy_dict()["source"], "https://example.invalid/ktx.zip")
        self.assertEqual(
            tuple(entry.path for entry in inventory),
            ("qw/z-last.pk3", "qw/a-first.pk3"),
        )
        self.assertEqual(receipts.serialize_component_receipt(receipt), receipt_payload)
        self.assertEqual(receipts.serialize_inventory(inventory), inventory_payload)


class CliReceiptTests(unittest.TestCase):
    def test_compact_cli_receipt_preserves_extensions_and_serializes_canonically(self) -> None:
        """Dropping extensions would change canonical/legacy divergence detection."""

        receipts = importlib.import_module("x86qw_runtime.receipts")
        self.assertTrue(hasattr(receipts, "parse_cli_receipt"))
        self.assertTrue(hasattr(receipts, "serialize_cli_receipt"))
        payload = b'{"version":"0.7.1","project":"x86qw","format":1,"future":"kept"}'

        receipt = receipts.parse_cli_receipt(payload)

        self.assertEqual(receipt.version, "0.7.1")
        self.assertEqual(receipt.to_legacy_dict()["future"], "kept")
        self.assertEqual(
            receipts.serialize_cli_receipt(receipt),
            (
                b'{\n  "format": 1,\n  "future": "kept",\n'
                b'  "project": "x86qw",\n  "version": "0.7.1"\n}\n'
            ),
        )


class LegacyNQuakeReceiptTests(unittest.TestCase):
    def test_legacy_nquake_receipt_remains_parseable_during_one_way_migration(self) -> None:
        """Removing the historical codec would strand pre-component installations."""

        receipts = importlib.import_module("x86qw_runtime.receipts")
        self.assertTrue(hasattr(receipts, "parse_legacy_nquake_receipt"))
        payload = (
            b"format\t1\n"
            b"distfiles_commit\t" + b"a" * 40 + b"\n"
            b"inventory_sha256\t" + b"b" * 64 + b"\n"
        )

        receipt = receipts.parse_legacy_nquake_receipt(payload)

        self.assertEqual(receipt.distfiles_commit, "a" * 40)
        self.assertEqual(receipt.to_legacy_dict()["inventory_sha256"], "b" * 64)


class BoundedMetadataReadTests(unittest.TestCase):
    def test_regular_metadata_reader_rejects_payload_larger_than_its_limit(self) -> None:
        """Receipts and inventories must not be loaded with unbounded read_text calls."""

        spec = importlib.util.find_spec("x86qw_runtime.io.metadata")
        self.assertIsNotNone(spec, "bounded metadata I/O must be a runtime boundary")
        metadata = importlib.import_module("x86qw_runtime.io.metadata")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt"
            path.write_bytes(b"x" * 65)

            with self.assertRaises(metadata.MetadataFileError):
                metadata.read_bounded_regular_file(path, maximum_size=64)


if __name__ == "__main__":
    unittest.main()
