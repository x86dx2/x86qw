from __future__ import annotations

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


class ArchiveGameplayIntegrationTests(unittest.TestCase):
    def test_pk3_gamecode_reader_scans_every_member_before_returning_gamecode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "gamecode.pk3"
            game = SimpleNamespace(label="Fixture")
            player = object.__new__(gameplay.Player)

            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("qwprogs.dat", b"verified-gamecode")
            with mock.patch.object(gameplay.Player, "game_program_path", return_value=package):
                self.assertEqual(b"verified-gamecode", player.local_game_program(game))

            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("qwprogs.dat", b"must-not-be-returned")
                archive.writestr("../late-escape.cfg", b"hostile")
            with mock.patch.object(gameplay.Player, "game_program_path", return_value=package):
                with self.assertRaisesRegex(
                    gameplay.InstallerError,
                    "Gamecode qwprogs.dat não encontrado",
                ):
                    player.local_game_program(game)


if __name__ == "__main__":
    unittest.main()
