from __future__ import annotations

import base64
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from maintenance.tools.prepare_tuf_handoff import (
    TufHandoffError,
    prepare_tuf_handoff,
)


def bundle(*members: tuple[str, bytes | None, str]) -> str:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, payload, kind in members:
            if kind == "dir":
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                assert payload is not None
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                if kind == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = "../outside"
                    info.size = 0
                archive.addfile(info, io.BytesIO(payload) if kind == "file" else None)
    return base64.b64encode(stream.getvalue()).decode("ascii")


class PrepareTufHandoffTests(unittest.TestCase):
    def test_extracts_only_regular_metadata_and_target_files(self) -> None:
        encoded = bundle(
            ("metadata", None, "dir"),
            ("targets", None, "dir"),
            ("metadata/1.root.json", b"root", "file"),
            ("targets/catalog/catalog.json", b"catalog", "file"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "signed-tuf"
            result = prepare_tuf_handoff(encoded=encoded, output=output)
            self.assertEqual("prepared", result["status"])
            self.assertEqual(2, result["file_count"])
            self.assertEqual(b"root", (output / "metadata/1.root.json").read_bytes())
            self.assertEqual(b"catalog", (output / "targets/catalog/catalog.json").read_bytes())

    def test_rejects_traversal_and_leaves_no_output(self) -> None:
        encoded = bundle(("metadata/../outside.json", b"bad", "file"))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "signed-tuf"
            with self.assertRaisesRegex(TufHandoffError, "caminho inseguro"):
                prepare_tuf_handoff(encoded=encoded, output=output)
            self.assertFalse(output.exists())

    def test_rejects_symlinks_and_duplicates(self) -> None:
        for members, message in (
            ([
                ("metadata/root.json", b"bad", "symlink"),
            ], "symlink"),
            ([
                ("metadata/root.json", b"one", "file"),
                ("metadata/root.json", b"two", "file"),
            ], "duplicado"),
        ):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaisesRegex(TufHandoffError, message):
                    prepare_tuf_handoff(
                        encoded=bundle(*members),
                        output=Path(temporary) / "signed-tuf",
                    )

    def test_rejects_invalid_or_oversized_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "signed-tuf"
            with self.assertRaisesRegex(TufHandoffError, "base64"):
                prepare_tuf_handoff(encoded="!not-base64!", output=output)
            with self.assertRaisesRegex(TufHandoffError, "limite"):
                prepare_tuf_handoff(encoded="A" * 65536, output=output)


if __name__ == "__main__":
    unittest.main()
