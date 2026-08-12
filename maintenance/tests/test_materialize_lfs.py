from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MATERIALIZER = ROOT / "maintenance/tools/materialize_lfs.py"


def load_materializer():
    if not MATERIALIZER.is_file():
        raise AssertionError("materialize_lfs.py ainda não foi implementado")
    spec = importlib.util.spec_from_file_location("materialize_lfs_under_test", MATERIALIZER)
    if spec is None or spec.loader is None:
        raise AssertionError("não foi possível carregar o materializador LFS")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MaterializeLfsTests(unittest.TestCase):
    def test_pointer_parser_returns_content_addressed_identity(self):
        module = load_materializer()
        digest = "a" * 64
        self.assertEqual(
            (digest, 12),
            module.parse_pointer(
                f"version https://git-lfs.github.com/spec/v1\noid sha256:{digest}\nsize 12\n".encode()
            ),
        )

    def test_materializer_downloads_and_reuses_verified_object(self):
        module = load_materializer()
        payload = b"real-lfs-bytes"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "dist/example.bin"
            path.parent.mkdir(parents=True)
            path.write_bytes(
                f"version https://git-lfs.github.com/spec/v1\noid sha256:{digest}\nsize {len(payload)}\n".encode()
            )
            calls: list[tuple[str, Path, int, str]] = []

            def fetch(url: str, destination: Path, size: int, expected_sha256: str) -> None:
                calls.append((url, destination, size, expected_sha256))
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)

            module.materialize(root, "x86dx2/x86qw", "f" * 40, fetch=fetch)
            self.assertEqual(
                payload,
                (root / ".git/lfs/objects" / digest[:2] / digest[2:4] / digest).read_bytes(),
            )
            self.assertEqual(1, len(calls))
            self.assertIn("raw.githubusercontent.com/x86dx2/x86qw/" + "f" * 40, calls[0][0])

            module.materialize(root, "x86dx2/x86qw", "f" * 40, fetch=fetch)
            self.assertEqual(1, len(calls))

    def test_pointer_parser_rejects_unbounded_or_malformed_input(self):
        module = load_materializer()
        with self.assertRaises(ValueError):
            module.parse_pointer(b"version https://git-lfs.github.com/spec/v1\n")
        with self.assertRaises(ValueError):
            module.parse_pointer(
                ("version https://git-lfs.github.com/spec/v1\n"
                 "oid sha256:" + "a" * 64 + "\nsize 999999999999999999999\n").encode()
            )


if __name__ == "__main__":
    unittest.main()
