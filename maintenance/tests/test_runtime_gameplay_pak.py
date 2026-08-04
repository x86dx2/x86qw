from __future__ import annotations

import importlib
import importlib.util
import inspect
import struct
import tempfile
import unittest
from pathlib import Path

from x86qw_runtime.io.archive import ArchiveLimits


def pak_bytes(members: list[tuple[str, bytes]]) -> bytes:
    data = bytearray()
    directory = bytearray()
    offset = 12
    for name, payload in members:
        encoded = name.encode("ascii")
        if len(encoded) > 55:
            raise ValueError("fixture member name is too long")
        data.extend(payload)
        directory.extend(encoded + b"\0" * (56 - len(encoded)))
        directory.extend(struct.pack("<II", offset, len(payload)))
        offset += len(payload)
    return b"PACK" + struct.pack("<II", offset, len(directory)) + data + directory


def pak_layout(
    entries: list[tuple[bytes, int, int]],
    *,
    payload: bytes = b"",
) -> bytes:
    directory_offset = 12 + len(payload)
    directory = bytearray()
    for raw_name, data_offset, data_size in entries:
        if len(raw_name) > 56:
            raise ValueError("fixture member name is too long")
        directory.extend(raw_name + b"\0" * (56 - len(raw_name)))
        directory.extend(struct.pack("<II", data_offset, data_size))
    return (
        b"PACK"
        + struct.pack("<II", directory_offset, len(directory))
        + payload
        + directory
    )


class PakRuntimeTests(unittest.TestCase):
    def pak_module(self):
        spec = importlib.util.find_spec("x86qw_runtime.gameplay.pak")
        self.assertIsNotNone(spec, "o runtime PAK canônico ainda não existe")
        return importlib.import_module("x86qw_runtime.gameplay.pak")

    def test_lists_only_direct_bsp_map_members(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "maps.pak"
            package.write_bytes(pak_bytes([
                ("maps/dm6.bsp", b"dm6"),
                ("MAPS/aerowalk.BSP", b"aero"),
                ("maps/deep/ignored.bsp", b"deep"),
                ("sound/misc/menu1.wav", b"sound"),
            ]))

            self.assertEqual(
                {"aerowalk", "dm6"},
                self.pak_module().list_bsp_names(package),
            )

    def test_reads_an_exact_member_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "gamecode.pak"
            package.write_bytes(pak_bytes([
                ("progs.dat", b"not-the-requested-member"),
                ("QWPROGS.DAT", b"verified-gamecode"),
            ]))

            module = self.pak_module()
            self.assertTrue(hasattr(module, "read_member"), "a leitura exata ainda não existe")
            self.assertEqual(b"verified-gamecode", module.read_member(package, "qwprogs.dat"))

    def test_listing_rejects_member_offsets_and_sizes_outside_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            name = b"maps/dm6.bsp" + b"\0" * (56 - len("maps/dm6.bsp"))
            invalid_entries = {
                "offset": struct.pack("<II", 4096, 1),
                "size": struct.pack("<II", 12, 4096),
            }
            for label, coordinates in invalid_entries.items():
                with self.subTest(label=label):
                    package = Path(temporary) / f"invalid-{label}.pak"
                    directory = name + coordinates
                    package.write_bytes(b"PACK" + struct.pack("<II", 12, 64) + directory)
                    module = self.pak_module()
                    with self.assertRaises(module.PakError):
                        module.list_bsp_names(package)

    def test_rejects_a_directory_over_the_caller_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "large-directory.pak"
            package.write_bytes(pak_bytes([
                ("maps/dm6.bsp", b"dm6"),
                ("maps/aerowalk.bsp", b"aero"),
            ]))
            module = self.pak_module()
            self.assertIn("limits", inspect.signature(module.list_bsp_names).parameters)

            with self.assertRaises(module.PakError):
                module.list_bsp_names(package, limits=ArchiveLimits(max_metadata_size=64))

    def test_rejects_truncated_and_invalid_directories(self) -> None:
        fixtures = {
            "truncated-header": b"PACK\0\0",
            "header-overlap": b"PACK" + struct.pack("<II", 8, 0),
            "unaligned-directory": b"PACK" + struct.pack("<II", 12, 1) + b"x",
            "truncated-directory": b"PACK" + struct.pack("<II", 12, 64) + b"x" * 63,
        }
        module = self.pak_module()
        with tempfile.TemporaryDirectory() as temporary:
            for label, payload in fixtures.items():
                with self.subTest(label=label):
                    package = Path(temporary) / f"{label}.pak"
                    package.write_bytes(payload)
                    with self.assertRaises(module.PakError):
                        module.list_bsp_names(package)

    def test_rejects_a_member_over_the_caller_limit_before_reading_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "large-member.pak"
            package.write_bytes(pak_bytes([("qwprogs.dat", b"12345")]))
            module = self.pak_module()

            with self.assertRaises(module.PakError):
                module.read_member(package, "qwprogs.dat", limits=ArchiveLimits(max_member_size=4))

    def test_rejects_an_invalid_utf8_directory_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "invalid-name.pak"
            raw_name = b"maps/" + b"\xff" + b".bsp"
            directory = raw_name + b"\0" * (56 - len(raw_name)) + struct.pack("<II", 12, 0)
            package.write_bytes(b"PACK" + struct.pack("<II", 12, 64) + directory)
            module = self.pak_module()

            with self.assertRaises(module.PakError):
                module.list_bsp_names(package)

    def test_rejects_symlink_and_non_regular_sources(self) -> None:
        module = self.pak_module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.pak"
            target.write_bytes(pak_bytes([("maps/dm6.bsp", b"dm6")]))
            symlink = root / "linked.pak"
            try:
                symlink.symlink_to(target)
            except (NotImplementedError, OSError):
                self.skipTest("symlink indisponível neste host")
            directory = root / "directory.pak"
            directory.mkdir()

            for source in (symlink, directory):
                with self.subTest(source=source.name):
                    with self.assertRaises(module.PakError):
                        module.list_bsp_names(source)

    def test_rejects_nonportable_member_names(self) -> None:
        invalid_names = (
            "",
            "/maps/dm6.bsp",
            "maps\\dm6.bsp",
            ".",
            "maps/./dm6.bsp",
            "maps/../dm6.bsp",
            "maps/dm6:bsp",
            "maps/dm6.bsp.",
            "maps/dm6.bsp ",
            "maps/CON.cfg",
            "maps/com1.bsp",
            "maps/dm6\x01.bsp",
        )
        module = self.pak_module()
        with tempfile.TemporaryDirectory() as temporary:
            for index, name in enumerate(invalid_names):
                with self.subTest(name=repr(name)):
                    package = Path(temporary) / f"unsafe-{index}.pak"
                    package.write_bytes(pak_bytes([(name, b"")]))
                    with self.assertRaises(module.PakError):
                        module.list_bsp_names(package)

    def test_rejects_exact_casefold_and_nfc_name_collisions(self) -> None:
        collisions = (
            (b"maps/dm6.bsp", b"maps/dm6.bsp"),
            (b"maps/DM6.bsp", b"maps/dm6.bsp"),
            (
                "maps/caf\N{LATIN SMALL LETTER E WITH ACUTE}.bsp".encode("utf-8"),
                "maps/cafe\N{COMBINING ACUTE ACCENT}.bsp".encode("utf-8"),
            ),
        )
        module = self.pak_module()
        with tempfile.TemporaryDirectory() as temporary:
            for index, names in enumerate(collisions):
                with self.subTest(names=names):
                    package = Path(temporary) / f"collision-{index}.pak"
                    package.write_bytes(pak_layout([
                        (names[0], 12, 0),
                        (names[1], 12, 0),
                    ]))
                    with self.assertRaisesRegex(module.PakError, "colisão"):
                        module.list_bsp_names(package)

    def test_rejects_member_regions_overlapping_directory_or_another_member(self) -> None:
        fixtures = {
            "directory": pak_layout([(b"maps/dm6.bsp", 18, 4)], payload=b"abcdefgh"),
            "member": pak_layout([
                (b"maps/dm6.bsp", 12, 6),
                (b"maps/aerowalk.bsp", 16, 4),
            ], payload=b"abcdefghij"),
        }
        module = self.pak_module()
        with tempfile.TemporaryDirectory() as temporary:
            for label, payload in fixtures.items():
                with self.subTest(label=label):
                    package = Path(temporary) / f"overlap-{label}.pak"
                    package.write_bytes(payload)
                    with self.assertRaises(module.PakError):
                        module.list_bsp_names(package)

    def test_read_member_preflights_every_entry_before_returning_payload(self) -> None:
        module = self.pak_module()
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "late-invalid.pak"
            package.write_bytes(pak_layout([
                (b"qwprogs.dat", 12, 4),
                (b"maps/../escape.bsp", 16, 4),
            ], payload=b"goodbad!"))

            with self.assertRaises(module.PakError):
                module.read_member(package, "qwprogs.dat")


if __name__ == "__main__":
    unittest.main()
