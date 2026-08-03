from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "maintenance/tools"))

import public_upstreams  # noqa: E402
from public_upstreams import (  # noqa: E402
    DISCOVERY_MAX_BYTES,
    GITHUB_API_DEADLINE_SECONDS,
    GitTreeEntry,
    _validated_github_tree,
    github_commit_revision,
    github_latest_release,
    github_recursive_tree,
    github_ref_revision,
    remote_content_length,
)


def response(document: object, *, url: str = "https://api.github.com/") -> mock.Mock:
    return mock.Mock(
        url=url,
        data=json.dumps(document, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Length": "1"},
    )


def reference_document(revision: str = "a" * 40) -> dict[str, object]:
    return {
        "ref": "refs/heads/master",
        "object": {
            "type": "commit",
            "sha": revision,
            "url": f"https://api.github.com/repos/nQuake/distfiles/git/commits/{revision}",
        },
    }


def commit_document(
    revision: str = "a" * 40,
    tree_sha: str = "b" * 40,
) -> dict[str, object]:
    return {
        "sha": revision,
        "tree": {
            "sha": tree_sha,
            "url": f"https://api.github.com/repos/nQuake/distfiles/git/trees/{tree_sha}",
        },
    }


def tree_document(
    entries: list[dict[str, object]],
    *,
    revision: str = "a" * 40,
    truncated: bool = False,
) -> dict[str, object]:
    return {
        "sha": revision,
        "truncated": truncated,
        "tree": entries,
    }


class PublicUpstreamTests(unittest.TestCase):
    def test_github_ref_uses_bounded_metadata_on_the_official_api(self) -> None:
        with mock.patch(
            "public_upstreams.download",
            return_value=response(reference_document()),
        ) as download:
            revision = github_ref_revision(
                "https://github.com/nQuake/distfiles.git", "refs/heads/master",
            )

        self.assertEqual("a" * 40, revision)
        contract = download.call_args.args[0]
        self.assertEqual(
            "https://api.github.com/repos/nQuake/distfiles/git/ref/heads/master",
            contract.url,
        )
        self.assertEqual(DISCOVERY_MAX_BYTES, contract.maximum_size)
        self.assertLessEqual(contract.deadline_seconds, GITHUB_API_DEADLINE_SECONDS)
        self.assertGreater(contract.deadline_seconds, 0)
        self.assertEqual("application/vnd.github+json", contract.headers["Accept"])
        self.assertNotIn("Authorization", contract.headers)

    def test_github_repository_rejects_other_origins_and_ambiguous_urls_before_network(self) -> None:
        invalid = (
            "http://github.com/nQuake/distfiles.git",
            "https://example.invalid/nQuake/distfiles.git",
            "file:///tmp/repo.git",
            "git@github.com:nQuake/distfiles.git",
            "https://user@github.com/nQuake/distfiles.git",
            "https://github.com:8443/nQuake/distfiles.git",
            "https://github.com/nQuake/distfiles.git?ref=main",
            "https://github.com/nQuake/distfiles.git#main",
            "https://github.com/nQuake/distfiles\\name.git",
            "https://github.com/nQuake/distfiles extra.git",
            "https://github.com/nQuake/distfiles/extra.git",
            "https://github.com/nQuake/%64istfiles.git",
        )
        with mock.patch("public_upstreams.download") as download:
            for repository in invalid:
                with self.subTest(repository=repository), self.assertRaises(ValueError):
                    github_ref_revision(repository, "refs/heads/master")
        download.assert_not_called()

    def test_github_repository_rejection_redacts_credentials_and_controls(self) -> None:
        sentinel = "X86QW_URL_SECRET_SENTINEL"
        invalid = (
            f"https://operator:{sentinel}@github.com/nQuake/distfiles.git",
            f"https://github.com/nQuake/distfiles.git?token={sentinel}\nforged",
        )
        with mock.patch("public_upstreams.download") as download:
            for repository in invalid:
                with self.subTest(repository=repository), self.assertRaises(ValueError) as raised:
                    github_ref_revision(repository, "refs/heads/master")
                self.assertNotIn(sentinel, str(raised.exception))
                self.assertNotIn("\n", str(raised.exception))
        download.assert_not_called()

    def test_github_ref_rejects_unsafe_or_non_head_refs_before_network(self) -> None:
        invalid = (
            "--upload-pack=evil",
            "refs/heads/a..b",
            "refs/heads/a@{1}",
            "refs//heads/main",
            "refs/tags/1.0",
            "heads/main.lock",
        )
        with mock.patch("public_upstreams.download") as download:
            for ref in invalid:
                with self.subTest(ref=ref), self.assertRaises(ValueError):
                    github_ref_revision("nQuake/distfiles", ref)
        download.assert_not_called()

    def test_github_ref_rejects_an_invalid_response_schema(self) -> None:
        valid = reference_document()
        invalid = (
            {},
            {**valid, "ref": "refs/heads/other"},
            {**valid, "object": {**valid["object"], "type": "tag"}},
            {**valid, "object": {**valid["object"], "sha": "invalid"}},
            {**valid, "object": {**valid["object"], "url": "https://example.invalid/commit"}},
        )
        for document in invalid:
            with self.subTest(document=document), mock.patch(
                "public_upstreams.download", return_value=response(document),
            ), self.assertRaisesRegex(ValueError, "referencia invalida"):
                github_ref_revision("nQuake/distfiles", "master")

    def test_github_recursive_tree_shares_one_deadline_and_returns_valid_blobs(self) -> None:
        revision = "b" * 40
        tree_sha = "e" * 40
        documents = (
            response(reference_document(revision)),
            response(commit_document(revision, tree_sha)),
            response(tree_document([
                {
                    "path": "qw/file.pk3", "mode": "100644", "type": "blob",
                    "sha": "c" * 40, "size": 321,
                },
                {
                    "path": "qw", "mode": "040000", "type": "tree",
                    "sha": "d" * 40,
                },
            ], revision=tree_sha)),
        )
        with mock.patch("public_upstreams.download", side_effect=documents) as download, mock.patch(
            "public_upstreams.time.monotonic", side_effect=[100.0, 100.0, 101.0, 102.0],
        ):
            actual_revision, entries = github_recursive_tree("nQuake/distfiles", "master")

        self.assertEqual(revision, actual_revision)
        self.assertEqual([GitTreeEntry("qw/file.pk3", "c" * 40, 321)], entries)
        contracts = [call.args[0] for call in download.call_args_list]
        self.assertEqual(3, len(contracts))
        self.assertEqual(GITHUB_API_DEADLINE_SECONDS, contracts[0].deadline_seconds)
        self.assertEqual(GITHUB_API_DEADLINE_SECONDS - 1, contracts[1].deadline_seconds)
        self.assertEqual(GITHUB_API_DEADLINE_SECONDS - 2, contracts[2].deadline_seconds)
        self.assertEqual(
            f"https://api.github.com/repos/nQuake/distfiles/git/commits/{revision}",
            contracts[1].url,
        )
        self.assertEqual(
            f"https://api.github.com/repos/nQuake/distfiles/git/trees/{tree_sha}?recursive=1",
            contracts[2].url,
        )
        self.assertTrue(all(contract.maximum_size == DISCOVERY_MAX_BYTES for contract in contracts))

    def test_github_recursive_tree_rejects_an_invalid_commit_tree(self) -> None:
        revision = "a" * 40
        valid = commit_document(revision)
        invalid = (
            {},
            {**valid, "sha": "c" * 40},
            {**valid, "tree": {**valid["tree"], "sha": "invalid"}},
            {**valid, "tree": {**valid["tree"], "url": "https://example.invalid/tree"}},
        )
        for commit in invalid:
            with self.subTest(commit=commit), mock.patch(
                "public_upstreams.download",
                side_effect=[response(reference_document(revision)), response(commit)],
            ), self.assertRaisesRegex(ValueError, "commit invalido"):
                github_recursive_tree("nQuake/distfiles", "master")

    def test_github_recursive_tree_rejects_truncated_or_mismatched_trees(self) -> None:
        for document in (
            tree_document([], truncated=True),
            tree_document([], revision="b" * 40),
            {"sha": "a" * 40, "truncated": False, "tree": {}},
        ):
            with self.subTest(document=document), self.assertRaisesRegex(
                ValueError, "arvore incompleta ou invalida",
            ):
                _validated_github_tree(document, expected_revision="a" * 40)

    def test_github_recursive_tree_rejects_invalid_entry_fields(self) -> None:
        base = {
            "path": "qw/file.pk3", "mode": "100644", "type": "blob",
            "sha": "c" * 40, "size": 321,
        }
        invalid_changes = (
            {"path": "../escape"},
            {"path": "/absolute"},
            {"path": "qw\\file.pk3"},
            {"path": "qw/CON"},
            {"mode": "120000"},
            {"type": "special"},
            {"sha": "invalid"},
            {"size": True},
            {"size": -1},
            {"size": public_upstreams.MAX_ARTIFACT_BYTES + 1},
        )
        for changes in invalid_changes:
            entry = {**base, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _validated_github_tree(
                    tree_document([entry]), expected_revision="a" * 40,
                )

    def test_github_recursive_tree_rejects_portable_path_collisions(self) -> None:
        entries = [
            {
                "path": path, "mode": "100644", "type": "blob",
                "sha": character * 40, "size": 1,
            }
            for path, character in (("qw/File.cfg", "a"), ("qw/file.cfg", "b"))
        ]
        with self.assertRaisesRegex(ValueError, "caminhos.*duplicados"):
            _validated_github_tree(
                tree_document(entries), expected_revision="a" * 40,
            )

    def test_github_recursive_tree_rejects_size_on_non_blob(self) -> None:
        with self.assertRaisesRegex(ValueError, "tamanho em entrada sem blob"):
            _validated_github_tree(
                tree_document([{
                    "path": "qw", "mode": "040000", "type": "tree",
                    "sha": "c" * 40, "size": 1,
                }]),
                expected_revision="a" * 40,
            )

    def test_github_recursive_tree_rejects_submodules(self) -> None:
        with self.assertRaisesRegex(ValueError, "entrada de arvore invalida"):
            _validated_github_tree(
                tree_document([{
                    "path": "vendor/module", "mode": "160000", "type": "commit",
                    "sha": "c" * 40,
                }]),
                expected_revision="a" * 40,
            )

    def test_github_api_rejects_invalid_json(self) -> None:
        invalid_response = mock.Mock(data=b"not-json", url="https://api.github.com/", headers={})
        with mock.patch("public_upstreams.download", return_value=invalid_response), self.assertRaisesRegex(
            ValueError, "referencia do GitHub invalido",
        ):
            github_ref_revision("nQuake/distfiles", "master")

    def test_public_upstreams_has_no_native_git_ingress(self) -> None:
        source = (ROOT / "maintenance/tools/public_upstreams.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("ls-remote", source)
        self.assertNotIn("git clone", source)
        self.assertNotIn("run_git", source)

    def test_release_commit_and_size_use_public_web_urls_without_authorization(self) -> None:
        release = mock.Mock(
            url="https://github.com/QW-Group/ktx/releases/tag/1.47",
            data=None,
            headers={"Content-Length": "1"},
        )
        commit = mock.Mock(
            url="https://github.com/QW-Group/ezquake-source/commit/" + "d" * 40,
            data=(
                b'<meta property="og:url" content="https://github.com/'
                b'QW-Group/ezquake-source/commit/' + b"d" * 40 + b'">'
                b'<a href="/QW-Group/ezquake-source/commit/' + b"a" * 40 + b'">parent</a>'
                b'<a href="/QW-Group/ezquake-source/commit/' + b"b" * 40 + b'">child</a>'
            ),
            headers={"Content-Length": "100"},
        )
        artifact = mock.Mock(
            url="https://github.com/example/download.zip",
            data=None,
            headers={"Content-Length": "403006"},
        )
        with mock.patch("public_upstreams.download", side_effect=[release, commit, artifact]) as opened:
            self.assertEqual("1.47", github_latest_release("QW-Group/ktx"))
            self.assertEqual("d" * 40, github_commit_revision("QW-Group/ezquake-source", "d" * 7))
            self.assertEqual(403006, remote_content_length("https://github.com/example/download.zip"))
        for call in opened.call_args_list:
            contract = call.args[0]
            self.assertNotIn("Authorization", contract.headers)


if __name__ == "__main__":
    unittest.main()
