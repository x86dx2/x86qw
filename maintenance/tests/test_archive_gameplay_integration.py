from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dist/installer/bin"))

import gameplay  # noqa: E402


def pak_entry(name: str, offset: int, size: int) -> bytes:
    encoded = name.encode("ascii")
    return encoded + b"\0" * (56 - len(encoded)) + struct.pack("<II", offset, size)


class ArchiveGameplayIntegrationTests(unittest.TestCase):
    def test_pk3_gamecode_reader_scans_every_member_before_returning_gamecode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "gamecode.pk3"
            game = SimpleNamespace(label="Fixture")
            player = object.__new__(gameplay.GameplayPlayerMixin)

            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("qwprogs.dat", b"verified-gamecode")
            with mock.patch.object(gameplay.GameplayPlayerMixin, "game_program_path", return_value=package):
                self.assertEqual(b"verified-gamecode", player.local_game_program(game))

            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("qwprogs.dat", b"must-not-be-returned")
                archive.writestr("../late-escape.cfg", b"hostile")
            with mock.patch.object(gameplay.GameplayPlayerMixin, "game_program_path", return_value=package):
                with self.assertRaisesRegex(
                    gameplay.InstallerError,
                    "Gamecode qwprogs.dat não encontrado",
                ):
                    player.local_game_program(game)

    def test_pak_gamecode_reader_preflights_every_directory_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "gamecode.pak"
            directory = (
                pak_entry("qwprogs.dat", 12, 8)
                + pak_entry("maps/dm6.bsp", 4096, 1)
            )
            package.write_bytes(
                b"PACK" + struct.pack("<II", 20, len(directory)) + b"gamecode" + directory
            )
            game = SimpleNamespace(label="Fixture")
            player = object.__new__(gameplay.GameplayPlayerMixin)

            with mock.patch.object(gameplay.GameplayPlayerMixin, "game_program_path", return_value=package):
                with self.assertRaisesRegex(gameplay.InstallerError, "Membro PAK inválido"):
                    player.local_game_program(game)


if __name__ == "__main__":
    unittest.main()
