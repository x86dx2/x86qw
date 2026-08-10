from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import maintenance.manage as manage_module
from maintenance.manage import (
    GITHUB_API_DEADLINE_SECONDS,
    GITHUB_API_MAX_BYTES,
    ManagerError,
    PROJECT_ROOT,
    Asset,
    command_add,
    command_check,
    command_update,
    distribution_delta,
    ezquake_source_revision,
    fetch_definition_file,
    github_api_object,
    github_latest_release_tag,
    github_release,
    github_release_coordinates,
    parser,
    publish_github,
    preserve_profile_fingerprints,
    reference_content_changed,
    safe_relative,
    summarize_delta,
    update_inventory_lines,
    update_ezquake_catalog,
    update_reference_releases,
    validate_definition_file,
    validate_distribution_change,
)
from maintenance.tools.components import (
    PORTABLE_RELATIVE_PATH_MAX_UTF16_UNITS,
    validate_catalog as validate_component_catalog,
    validate_portable_relative_path,
)
from maintenance.tools.downloader import DownloadHTTPError, DownloadResult


class DistributionManagerTests(unittest.TestCase):
    def test_portable_path_contract_accepts_posix_separators_and_limit(self) -> None:
        canonical = "dist/mods/td2/2.22/source/payload.zip"
        self.assertEqual(canonical, safe_relative(canonical, "dist"))
        self.assertEqual(canonical, validate_portable_relative_path(canonical, "test path"))

        at_limit = "dist/" + "a" * (PORTABLE_RELATIVE_PATH_MAX_UTF16_UNITS - 5)
        self.assertEqual(at_limit, safe_relative(at_limit, "dist"))

    def test_portable_path_contract_rejects_win32_aliases_and_characters(self) -> None:
        invalid_components = (
            "bad<name", "bad>name", 'bad"name', "bad:name", "bad\\name",
            "bad|name", "bad?name", "bad*name", "CON.cfg", "conin$.cfg",
            "CONOUT$.txt", "COM¹.cfg", "com²", "LPT³.zip", "trailing.",
            "trailing ",
        )
        for component in invalid_components:
            path = f"dist/mods/{component}/payload.zip"
            with self.subTest(component=component):
                with self.assertRaises(ManagerError):
                    safe_relative(path, "dist")
                with self.assertRaises(ValueError):
                    validate_portable_relative_path(path, "test path")

        for path in ("dist//payload.zip", "dist/./payload.zip", "dist/../payload.zip"):
            with self.subTest(path=path), self.assertRaises(ManagerError):
                safe_relative(path, "dist")

    def test_portable_path_contract_counts_utf16_units(self) -> None:
        over_ascii_limit = "dist/" + "a" * (PORTABLE_RELATIVE_PATH_MAX_UTF16_UNITS - 4)
        over_astral_limit = "dist/" + chr(0x1F600) * 118
        for path in (over_ascii_limit, over_astral_limit):
            with self.subTest(path_length=len(path)), self.assertRaises(ManagerError):
                safe_relative(path, "dist")

    def test_component_catalog_uses_the_common_portable_path_contract(self) -> None:
        catalog = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/components.json").read_text(encoding="utf-8")
        )
        for invalid in ("ezquake/CONIN$.cfg", "ezquake/COM¹.cfg", "ezquake/bad?.cfg"):
            proposed = copy.deepcopy(catalog)
            proposed["components"][0]["sources"][0]["destination"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_component_catalog(proposed)

    def test_github_release_uses_bounded_https_metadata_download(self) -> None:
        payload = json.dumps({"tag_name": "x86qw-installer-0.7.1"}).encode()
        response = DownloadResult(
            "https://api.github.com/repos/x86dx2/x86qw/releases/tags/x86qw-installer-0.7.1",
            len(payload), "a" * 64, 1, {}, data=payload,
        )
        with mock.patch.dict("os.environ", {"GH_TOKEN": "secret-token"}, clear=True):
            with mock.patch("maintenance.manage.download", return_value=response) as bounded:
                release = github_release("x86dx2/x86qw", "x86qw-installer-0.7.1")

        self.assertEqual("x86qw-installer-0.7.1", release["tag_name"])
        contract = bounded.call_args.args[0]
        self.assertEqual(GITHUB_API_MAX_BYTES, contract.maximum_size)
        self.assertEqual(GITHUB_API_DEADLINE_SECONDS, contract.deadline_seconds)
        self.assertEqual("https", urllib.parse.urlsplit(contract.url).scheme)
        self.assertEqual("Bearer secret-token", contract.headers["Authorization"])

    def test_github_latest_release_uses_bounded_json_response(self) -> None:
        payload = b'{"tag_name":"v1.2.3"}'
        response = DownloadResult(
            "https://api.github.com/repos/example/project/releases/latest",
            len(payload), "b" * 64, 1, {}, data=payload,
        )
        with mock.patch.dict("os.environ", {"GH_TOKEN": "secret-token"}, clear=True):
            with mock.patch("maintenance.manage.download", return_value=response):
                self.assertEqual("v1.2.3", github_latest_release_tag("example/project"))

    def test_github_api_treats_bounded_404_as_missing(self) -> None:
        error = DownloadHTTPError(404, "missing", {})
        with mock.patch.dict("os.environ", {"GH_TOKEN": "secret-token"}, clear=True):
            with mock.patch("maintenance.manage.download", side_effect=error):
                self.assertIsNone(github_api_object("example/project", "releases/latest", "latest"))

    def test_github_api_rejects_invalid_json_without_echoing_token(self) -> None:
        response = DownloadResult(
            "https://api.github.com/repos/example/project/releases/latest",
            4, "c" * 64, 1, {}, data=b"nope",
        )
        with mock.patch.dict("os.environ", {"GH_TOKEN": "never-print-this"}, clear=True):
            with mock.patch("maintenance.manage.download", return_value=response):
                with self.assertRaisesRegex(ManagerError, "resposta JSON inválida") as raised:
                    github_api_object("example/project", "releases/latest", "latest")
        self.assertNotIn("never-print-this", str(raised.exception))

    def test_github_release_quotes_tag_as_one_path_segment(self) -> None:
        payload = b'{"tag_name":"release/../latest"}'
        response = DownloadResult(
            "https://api.github.com/repos/example/project/releases/tags/release%2F..%2Flatest",
            len(payload), "d" * 64, 1, {}, data=payload,
        )
        with mock.patch.dict("os.environ", {"GH_TOKEN": "secret-token"}, clear=True):
            with mock.patch("maintenance.manage.download", return_value=response) as bounded:
                github_release("example/project", "release/../latest")
        self.assertTrue(bounded.call_args.args[0].url.endswith("/release%2F..%2Flatest"))

    def test_github_api_rejects_repository_path_injection_before_download(self) -> None:
        for repository in ("example/project/releases", "../project", "example/.."):
            with self.subTest(repository=repository):
                with mock.patch("maintenance.manage.download") as bounded:
                    with self.assertRaisesRegex(ManagerError, "repositório GitHub inválido"):
                        github_api_object(repository, "latest", "latest")
                bounded.assert_not_called()

    def test_registered_nightly_revision_does_not_require_github_lookup(self) -> None:
        asset = Asset(
            "ezquake",
            "https://example.invalid/ezquake.zip",
            "clients/ezquake/nightly/20260616-101233_a86996a/macos-universal/ezQuake.zip",
            1,
        )

        with mock.patch("maintenance.manage.github_commit_revision") as lookup:
            revision = ezquake_source_revision(asset)

        self.assertEqual("a86996a3d33dc1bc3fb15bfe7bcadd662b822557", revision)
        lookup.assert_not_called()

    def test_profile_history_preserves_old_and_new_distribution_shapes(self) -> None:
        catalog = {
            "profiles": {"essential": ["base"], "recommended": ["base"], "complete": ["base"]},
            "profile_history": {"essential": [], "recommended": [], "complete": []},
        }
        preserve_profile_fingerprints(catalog)
        old = catalog["profile_history"]["recommended"][0]
        catalog["profiles"]["recommended"].append("feature")
        preserve_profile_fingerprints(catalog)
        self.assertEqual(2, len(catalog["profile_history"]["recommended"]))
        self.assertEqual(old, catalog["profile_history"]["recommended"][0])

    def test_distribution_delta_reports_new_and_obsolete_managed_files(self) -> None:
        manifest = {
            "files": {
                "clients/ezquake/old.zip": {"url": "https://example.invalid/old.zip"},
                "mods/kept.zip": {"url": "https://example.invalid/kept.zip", "size": 10, "sha256": "a" * 64},
            }
        }
        assets = [
            Asset("td2", "https://example.invalid/kept.zip", "mods/kept.zip", 10, expected_sha256="a" * 64),
            Asset("td2", "https://example.invalid/new.zip", "mods/new.zip", 20),
        ]

        delta = distribution_delta(assets, manifest)

        self.assertEqual(
            [item["path"] for item in delta],
            ["clients/ezquake/old.zip", "mods/new.zip"],
        )
        self.assertEqual({item["status"] for item in delta}, {"obsolete", "update-available"})

    def test_add_dry_run_rejects_unmanaged_remote_file_without_download_or_mutation(self) -> None:
        secret = "valid-dry-run-secret"
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": f"https://example.invalid/{secret}/private.zip",
                "destination": "dist/mods/td2/2.22/source/private.zip",
                "size": 12,
                "sha256": "a" * 64,
            }],
        }
        options = mock.Mock(definition=Path("reviewed-change.json"), dry_run=True, yes=False)
        output = io.StringIO()

        def load(path: Path) -> dict[str, object]:
            if Path(path) == options.definition.resolve():
                return definition
            return json.loads(Path(path).read_text(encoding="utf-8"))

        with mock.patch(
            "maintenance.manage.load_json", side_effect=load,
        ), mock.patch(
            "maintenance.manage.download",
        ) as download, mock.patch(
            "maintenance.manage.require_clean_worktree",
        ) as clean, mock.patch(
            "maintenance.manage.confirm",
        ) as confirm, mock.patch(
            "maintenance.manage.prepare_workspace",
        ) as prepare, mock.patch(
            "maintenance.manage.apply_workspace",
        ) as apply, redirect_stdout(output), redirect_stderr(output):
            with self.assertRaisesRegex(
                ManagerError, "todo arquivo remoto persistente precisa declarar managed: true",
            ):
                command_add(options)

        download.assert_not_called()
        clean.assert_not_called()
        confirm.assert_not_called()
        prepare.assert_not_called()
        apply.assert_not_called()
        self.assertNotIn(secret, output.getvalue())

    def test_add_dry_run_rejects_query_on_persistent_managed_url_without_disclosure(self) -> None:
        secret = "never-persist-this-token"
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": f"https://example.invalid/private.zip?token={secret}",
                "destination": "dist/mods/td2/2.22/source/private.zip",
                "size": 12,
                "sha256": "a" * 64,
                "managed": True,
                "distribution_component": "td2",
                "consumer": "install:total-destruction-2",
                "package": "total-destruction-2",
            }],
        }
        options = mock.Mock(definition=Path("reviewed-change.json"), dry_run=True, yes=False)

        with mock.patch(
            "maintenance.manage.load_json", return_value=definition,
        ), mock.patch(
            "maintenance.manage.download",
        ) as download, mock.patch(
            "maintenance.manage.prepare_workspace",
        ) as prepare, self.assertRaises(ManagerError) as raised:
            command_add(options)

        download.assert_not_called()
        prepare.assert_not_called()
        self.assertNotIn(secret, str(raised.exception))

    def test_add_dry_run_accepts_canonical_managed_metadata_without_network(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "dist/manifest.json").read_text(encoding="utf-8")
        )["files"]
        paths = (
            "mods/td2/2.22/source/quakeworld-TD2.22QW-server_PTBR.tar.gz",
            "clients/ezquake/stable/3.6.9/windows-x64/ezQuake-windows-x64.zip",
            "clients/ezquake/stable/3.6.9/source/ezquake-source-3.6.9.tar.gz",
            "installer/packages/0.7.0/x86qw-installer-0.7.0.zip",
        )
        entries = []
        for path in paths:
            metadata = manifest[path]
            entry = {
                "url": metadata["url"],
                "destination": f"dist/{path}",
                "size": metadata["size"],
                "sha256": metadata["sha256"],
                "managed": True,
                "distribution_component": metadata["component"],
                "consumer": metadata["consumer"],
            }
            if "package" in metadata:
                entry["package"] = metadata["package"]
            entries.append(entry)
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": entries,
        }
        options = mock.Mock(
            definition=Path("canonical-managed.json"), dry_run=True, yes=False,
        )

        def load(path: Path) -> dict[str, object]:
            if Path(path) == options.definition.resolve():
                return definition
            return json.loads(Path(path).read_text(encoding="utf-8"))

        with mock.patch(
            "maintenance.manage.load_json", side_effect=load,
        ), mock.patch(
            "maintenance.manage.download",
        ) as download, mock.patch(
            "maintenance.manage.prepare_workspace",
        ) as prepare:
            self.assertEqual(0, command_add(options))

        download.assert_not_called()
        prepare.assert_not_called()

    def test_add_rejects_unsafe_release_url_before_network_or_staging(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "dist/manifest.json").read_text(encoding="utf-8")
        )["files"]
        components = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/components.json").read_text(encoding="utf-8")
        )
        releases = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/component-releases.json").read_text(encoding="utf-8")
        )
        relative = "mods/ktx/1.47/source/ktx-1.47.tar.gz"
        metadata = manifest[relative]
        component = copy.deepcopy(
            next(item for item in components["components"] if item["id"] == "ktx")
        )
        release = copy.deepcopy(releases["components"]["ktx"])
        secret = "never-print-release-token"
        release["upstream"]["source_url"] = (
            f"https://example.invalid/source.zip?token={secret}"
        )
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": metadata["url"],
                "destination": f"dist/{relative}",
                "size": metadata["size"],
                "sha256": metadata["sha256"],
                "managed": True,
                "distribution_component": metadata["component"],
                "consumer": metadata["consumer"],
                "package": metadata["package"],
            }],
            "component": component,
            "replace": True,
            "release": release,
        }
        options = mock.Mock(
            definition=Path("unsafe-release-url.json"), dry_run=True, yes=False,
        )

        def load(path: Path) -> dict[str, object]:
            if Path(path) == options.definition.resolve():
                return definition
            return json.loads(Path(path).read_text(encoding="utf-8"))

        with mock.patch(
            "maintenance.manage.load_json", side_effect=load,
        ), mock.patch(
            "maintenance.manage.download",
        ) as download, mock.patch(
            "maintenance.manage.prepare_workspace",
        ) as prepare, mock.patch(
            "maintenance.manage.apply_workspace",
        ) as apply, self.assertRaises(ManagerError) as raised:
            command_add(options)

        download.assert_not_called()
        prepare.assert_not_called()
        apply.assert_not_called()
        self.assertNotIn(secret, str(raised.exception))

    def test_add_binds_nquake_snapshot_path_to_the_staged_reference(self) -> None:
        releases = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/component-releases.json").read_text(encoding="utf-8")
        )
        revision = releases["reference"]["revision"]
        manifest = json.loads(
            (PROJECT_ROOT / "dist/manifest.json").read_text(encoding="utf-8")
        )["files"]
        relative, metadata = next(
            (path, record)
            for path, record in manifest.items()
            if path.startswith(f"distributions/nquake/{revision}/")
            and record.get("package") is not None
        )
        canonical = {
            "url": metadata["url"],
            "destination": f"dist/{relative}",
            "size": metadata["size"],
            "sha256": metadata["sha256"],
            "managed": True,
            "distribution_component": metadata["component"],
            "consumer": metadata["consumer"],
            "package": metadata["package"],
        }
        forged_revision = "a" * 40 if revision != "a" * 40 else "b" * 40
        forged = {
            **canonical,
            "url": str(canonical["url"]).replace(revision, forged_revision),
            "destination": str(canonical["destination"]).replace(
                revision, forged_revision,
            ),
        }

        for label, entry, succeeds in (
            ("canonical", canonical, True),
            ("forged", forged, False),
        ):
            definition = {
                "format": 1,
                "project": "x86qw",
                "kind": "distribution-change",
                "files": [entry],
            }
            options = mock.Mock(
                definition=Path(f"nquake-{label}.json"), dry_run=True, yes=False,
            )

            def load(path: Path) -> dict[str, object]:
                if Path(path) == options.definition.resolve():
                    return definition
                return json.loads(Path(path).read_text(encoding="utf-8"))

            with self.subTest(label=label), mock.patch(
                "maintenance.manage.load_json", side_effect=load,
            ), mock.patch(
                "maintenance.manage.download",
            ) as download, mock.patch(
                "maintenance.manage.prepare_workspace",
            ) as prepare, mock.patch(
                "maintenance.manage.apply_workspace",
            ) as apply:
                if succeeds:
                    self.assertEqual(0, command_add(options))
                else:
                    with self.assertRaisesRegex(
                        ManagerError,
                        "revisao do snapshot nQuake diverge da referencia fixada",
                    ):
                        command_add(options)

            download.assert_not_called()
            prepare.assert_not_called()
            apply.assert_not_called()

    def test_add_accepts_only_an_explicit_stale_safe_nquake_transition(self) -> None:
        releases = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/component-releases.json").read_text(
                encoding="utf-8",
            )
        )
        reference = releases["reference"]
        previous = reference["revision"]
        revision = "a" * 40 if previous != "a" * 40 else "b" * 40
        manifest = json.loads(
            (PROJECT_ROOT / "dist/manifest.json").read_text(encoding="utf-8")
        )["files"]
        relative, metadata = next(
            (path, record)
            for path, record in manifest.items()
            if path.startswith(f"distributions/nquake/{previous}/")
            and record.get("package") is not None
        )
        entry = {
            "url": str(metadata["url"]).replace(previous, revision),
            "destination": f"dist/{relative.replace(previous, revision)}",
            "size": metadata["size"],
            "sha256": metadata["sha256"],
            "managed": True,
            "distribution_component": metadata["component"],
            "consumer": metadata["consumer"],
            "package": metadata["package"],
        }
        unrelated_relative = (
            "mods/td2/2.22/source/quakeworld-TD2.22QW-server_PTBR.tar.gz"
        )
        unrelated_metadata = manifest[unrelated_relative]
        unrelated_entry = {
            "url": unrelated_metadata["url"],
            "destination": f"dist/{unrelated_relative}",
            "size": unrelated_metadata["size"],
            "sha256": unrelated_metadata["sha256"],
            "managed": True,
            "distribution_component": unrelated_metadata["component"],
            "consumer": unrelated_metadata["consumer"],
            "package": unrelated_metadata["package"],
        }
        base = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "reference": {
                "repository": reference["repository"],
                "previous_revision": previous,
                "revision": revision,
            },
            "files": [entry],
        }
        self.assertEqual(
            1, len(validate_distribution_change(base, Path("reference-change.json"))),
        )

        secret = "reference-secret-should-not-leak"
        cases = {
            "stale": (
                {
                    **base,
                    "reference": {
                        **base["reference"],
                        "previous_revision": revision,
                    },
                },
                "previous_revision",
            ),
            "repository": (
                {
                    **base,
                    "reference": {
                        **base["reference"],
                        "repository": f"https://reviewer:{secret}@example.invalid/repo",
                    },
                },
                "repository da referencia nQuake invalido",
            ),
            "origin": (
                {
                    **base,
                    "files": [{
                        **entry,
                        "url": "https://example.invalid/reviewed.bin",
                    }],
                },
                "URL do snapshot nQuake diverge",
            ),
            "missing-snapshot": (
                {
                    **base,
                    "files": [unrelated_entry],
                },
                "transicao da referencia nQuake precisa incorporar",
            ),
        }
        for label, (definition, message) in cases.items():
            with self.subTest(label=label), self.assertRaisesRegex(
                ManagerError, message,
            ) as raised:
                validate_distribution_change(definition, Path(f"{label}.json"))
            self.assertNotIn(secret, str(raised.exception))

    def test_add_replaces_the_staged_nquake_snapshot_only_after_validation(self) -> None:
        releases = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/component-releases.json").read_text(
                encoding="utf-8",
            )
        )
        reference = releases["reference"]
        previous = reference["revision"]
        revision = "a" * 40 if previous != "a" * 40 else "b" * 40
        live_manifest = json.loads(
            (PROJECT_ROOT / "dist/manifest.json").read_text(encoding="utf-8")
        )["files"]
        relative, metadata = next(
            (path, record)
            for path, record in live_manifest.items()
            if path.startswith(f"distributions/nquake/{previous}/")
            and record.get("package") is not None
        )
        new_relative = relative.replace(previous, revision)
        entry = {
            "url": str(metadata["url"]).replace(previous, revision),
            "destination": f"dist/{new_relative}",
            "size": metadata["size"],
            "sha256": metadata["sha256"],
            "managed": True,
            "distribution_component": metadata["component"],
            "consumer": metadata["consumer"],
            "package": metadata["package"],
        }
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "reference": {
                "repository": reference["repository"],
                "previous_revision": previous,
                "revision": revision,
            },
            "files": [entry],
        }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            definition_path = root / "change.json"
            definition_path.write_text(json.dumps(definition), encoding="utf-8")
            original_prepare = manage_module.prepare_workspace

            def prepare(_parent: Path) -> Path:
                return original_prepare(root)

            def fetch(
                raw: dict[str, object],
                _base: Path,
                target: Path,
                *,
                validated_plan: dict[str, object] | None = None,
            ) -> tuple[int, str]:
                self.assertIsNotNone(validated_plan)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"reviewed fixture")
                return int(raw["size"]), str(raw["sha256"])

            def inspect(work: Path) -> None:
                staged_releases = json.loads(
                    (work / "component-releases.json").read_text(encoding="utf-8")
                )
                staged_manifest = json.loads(
                    (work / "dist/manifest.json").read_text(encoding="utf-8")
                )["files"]
                self.assertEqual(revision, staged_releases["reference"]["revision"])
                self.assertFalse(
                    (work / "dist/distributions/nquake" / previous).exists(),
                )
                self.assertTrue((work / "dist" / new_relative).is_file())
                self.assertIn(new_relative, staged_manifest)
                self.assertFalse(any(
                    path.startswith(f"distributions/nquake/{previous}/")
                    for path in staged_manifest
                ))

            options = mock.Mock(
                definition=definition_path, dry_run=False, yes=True,
            )
            with mock.patch(
                "maintenance.manage.require_clean_worktree",
            ), mock.patch(
                "maintenance.manage.confirm",
            ), mock.patch(
                "maintenance.manage.prepare_workspace", side_effect=prepare,
            ), mock.patch(
                "maintenance.manage.fetch_definition_file", side_effect=fetch,
            ), mock.patch(
                "maintenance.manage.validate_staged",
            ), mock.patch(
                "maintenance.manage.build_packages", return_value={"packages": []},
            ), mock.patch(
                "maintenance.manage.register_packages",
            ), mock.patch(
                "maintenance.manage.apply_workspace", side_effect=inspect,
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(0, command_add(options))

        self.assertTrue(
            (PROJECT_ROOT / "dist/distributions/nquake" / previous).is_dir(),
        )

    def test_add_rejects_changed_identity_for_immutable_managed_paths(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "dist/manifest.json").read_text(encoding="utf-8")
        )["files"]
        paths = (
            "mods/td2/2.22/source/quakeworld-TD2.22QW-server_PTBR.tar.gz",
            "clients/ezquake/stable/3.6.9/windows-x64/ezQuake-windows-x64.zip",
            "clients/ezquake/stable/3.6.9/source/ezquake-source-3.6.9.tar.gz",
            "installer/packages/0.7.0/x86qw-installer-0.7.0.zip",
        )
        for path in paths:
            metadata = manifest[path]
            entry = {
                "url": metadata["url"],
                "destination": f"dist/{path}",
                "size": metadata["size"],
                "sha256": "0" * 64 if metadata["sha256"] != "0" * 64 else "1" * 64,
                "managed": True,
                "distribution_component": metadata["component"],
                "consumer": metadata["consumer"],
            }
            if "package" in metadata:
                entry["package"] = metadata["package"]
            definition = {
                "format": 1,
                "project": "x86qw",
                "kind": "distribution-change",
                "files": [entry],
            }
            with self.subTest(path=path), self.assertRaisesRegex(
                ManagerError, "diverge do manifesto imutavel atual",
            ):
                validate_distribution_change(definition, Path("immutable-change.json"))

    def test_add_rejects_semantic_change_to_existing_manifest_entry(self) -> None:
        path = "installer/packages/0.7.0/x86qw-installer-0.7.0.zip"
        metadata = json.loads(
            (PROJECT_ROOT / "dist/manifest.json").read_text(encoding="utf-8")
        )["files"][path]
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": metadata["url"],
                "destination": f"dist/{path}",
                "size": metadata["size"],
                "sha256": metadata["sha256"],
                "managed": True,
                "distribution_component": "installer",
                "consumer": "bootstrap:installer",
            }],
        }
        with self.assertRaisesRegex(ManagerError, "metadado consumer diverge"):
            validate_distribution_change(definition, Path("semantic-change.json"))

    def test_add_rejects_new_installer_outside_release_workflow(self) -> None:
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": "https://example.invalid/x86qw-installer-9.9.8.zip",
                "destination": "dist/installer/packages/9.9.8/x86qw-installer-9.9.8.zip",
                "size": 12,
                "sha256": "e" * 64,
                "managed": True,
                "distribution_component": "installer",
                "consumer": "bootstrap:installer",
            }],
        }
        with self.assertRaisesRegex(ManagerError, "fluxo de release imutavel"):
            validate_distribution_change(definition, Path("new-installer.json"))

    def test_add_rejects_managed_remote_without_canonical_authority(self) -> None:
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": "https://example.invalid/private.zip",
                "destination": "dist/mods/td2/2.22/source/private.zip",
                "size": 12,
                "sha256": "d" * 64,
                "managed": True,
                "distribution_component": "td2",
                "consumer": "install:total-destruction-2",
                "package": "total-destruction-2",
            }],
        }
        with self.assertRaisesRegex(
            ManagerError, "consumidor operacional|nao esta vinculado",
        ):
            validate_distribution_change(definition, Path("unregistered-remote.json"))

    def test_add_accepts_only_local_sources_declared_by_the_proposed_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "client.cfg").write_text("echo reviewed\n", encoding="utf-8")
            base = {
                "format": 1,
                "project": "x86qw",
                "kind": "distribution-change",
                "files": [{
                    "source": "client.cfg",
                    "destination": "dist/mods/ktx/1.47/x86qw/config/client.cfg",
                }],
            }
            self.assertEqual(1, len(validate_distribution_change(base, root / "change.json")))

            invalid = copy.deepcopy(base)
            invalid["files"][0]["destination"] = (
                "dist/mods/ktx/1.47/x86qw/config/not-declared.cfg"
            )
            with self.assertRaisesRegex(ManagerError, "consumidor exato no BOM"):
                validate_distribution_change(invalid, root / "change.json")

    def test_add_dry_run_rejects_missing_wrong_or_invented_manifest_package(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "dist/manifest.json").read_text(encoding="utf-8")
        )["files"]
        td2_path = "mods/td2/2.22/source/quakeworld-TD2.22QW-server_PTBR.tar.gz"
        td2 = manifest[td2_path]
        td2_entry = {
            "url": td2["url"],
            "destination": f"dist/{td2_path}",
            "size": td2["size"],
            "sha256": td2["sha256"],
            "managed": True,
            "distribution_component": td2["component"],
            "consumer": td2["consumer"],
            "package": td2["package"],
        }
        ez_path = "clients/ezquake/stable/3.6.9/windows-x64/ezQuake-windows-x64.zip"
        ezquake = manifest[ez_path]
        ez_entry = {
            "url": ezquake["url"],
            "destination": f"dist/{ez_path}",
            "size": ezquake["size"],
            "sha256": ezquake["sha256"],
            "managed": True,
            "distribution_component": ezquake["component"],
            "consumer": ezquake["consumer"],
        }
        invalid_entries = {
            "missing-td2-package": {
                key: value for key, value in td2_entry.items() if key != "package"
            },
            "wrong-td2-package": {**td2_entry, "package": "ktx"},
            "invented-ezquake-package": {**ez_entry, "package": "ezquake-stable"},
        }

        for label, entry in invalid_entries.items():
            definition = {
                "format": 1,
                "project": "x86qw",
                "kind": "distribution-change",
                "files": [entry],
            }
            options = mock.Mock(
                definition=Path(f"{label}.json"), dry_run=True, yes=False,
            )

            def load(path: Path) -> dict[str, object]:
                if Path(path) == options.definition.resolve():
                    return definition
                return json.loads(Path(path).read_text(encoding="utf-8"))

            with self.subTest(label=label), mock.patch(
                "maintenance.manage.load_json", side_effect=load,
            ), mock.patch(
                "maintenance.manage.download",
            ) as download, mock.patch(
                "maintenance.manage.prepare_workspace",
            ) as prepare, self.assertRaises(ManagerError):
                command_add(options)

            download.assert_not_called()
            prepare.assert_not_called()

    def test_add_dry_run_keeps_public_package_identity_separate_from_manifest_package(self) -> None:
        catalog = json.loads(
            (PROJECT_ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
        )
        source = next(
            package for package in catalog["packages"]
            if package["component"] == "ezquake"
            and package["channel"] == "stable"
            and package["platform"] == "linux"
        )
        package = copy.deepcopy(source)
        package["version"] = "9.9.9"
        package["distribution_path"] = (
            "clients/ezquake/stable/9.9.9/linux-x86_64/"
            f"{package['filename']}"
        )
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": package["origin_url"],
                "destination": f"dist/{package['distribution_path']}",
                "size": package["size"],
                "sha256": package["sha256"],
                "managed": True,
                "distribution_component": "ezquake",
                "consumer": "install:ezquake",
            }],
            "package": package,
        }
        options = mock.Mock(
            definition=Path("new-ezquake-package.json"), dry_run=True, yes=False,
        )

        def load(path: Path) -> dict[str, object]:
            if Path(path) == options.definition.resolve():
                return definition
            return json.loads(Path(path).read_text(encoding="utf-8"))

        with mock.patch(
            "maintenance.manage.load_json", side_effect=load,
        ), mock.patch(
            "maintenance.manage.download",
        ) as download, mock.patch(
            "maintenance.manage.prepare_workspace",
        ) as prepare:
            self.assertEqual(0, command_add(options))

        download.assert_not_called()
        prepare.assert_not_called()

    def test_add_rejects_public_package_with_foreign_logical_identity(self) -> None:
        catalog = json.loads(
            (PROJECT_ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
        )
        source = next(
            package for package in catalog["packages"]
            if package["component"] == "ezquake"
            and package["channel"] == "stable"
            and package["platform"] == "linux"
        )
        package = copy.deepcopy(source)
        package["version"] = "9.9.8"
        package["distribution_path"] = (
            "clients/ezquake/stable/9.9.8/linux-x86_64/"
            f"{package['filename']}"
        )
        package["package"] = "total-destruction-2"
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": package["origin_url"],
                "destination": f"dist/{package['distribution_path']}",
                "size": package["size"],
                "sha256": package["sha256"],
                "managed": True,
                "distribution_component": "ezquake",
                "consumer": "install:ezquake",
            }],
            "package": package,
        }

        with self.assertRaisesRegex(
            ManagerError,
            "identidade logica do pacote publico diverge do arquivo gerenciado",
        ):
            validate_distribution_change(definition, Path("foreign-public-package.json"))

    def test_add_rejects_ezquake_package_with_path_metadata_mismatch_before_network(self) -> None:
        catalog = json.loads(
            (PROJECT_ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
        )
        source = next(
            package for package in catalog["packages"]
            if package["component"] == "ezquake"
            and package["channel"] == "stable"
            and package["platform"] == "linux"
        )
        package = copy.deepcopy(source)
        package["channel"] = "nightly"
        package["version"] = "20990101-010203_abcdef0"
        package["distribution_path"] = (
            "clients/ezquake/stable/9.9.9/linux-x86_64/"
            f"{package['filename']}"
        )
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": package["origin_url"],
                "destination": f"dist/{package['distribution_path']}",
                "size": package["size"],
                "sha256": package["sha256"],
                "managed": True,
                "distribution_component": "ezquake",
                "consumer": "install:ezquake",
            }],
            "package": package,
        }
        options = mock.Mock(
            definition=Path("mismatched-ezquake-coordinates.json"),
            dry_run=True,
            yes=False,
        )

        def load(path: Path) -> dict[str, object]:
            if Path(path) == options.definition.resolve():
                return definition
            return json.loads(Path(path).read_text(encoding="utf-8"))

        with mock.patch(
            "maintenance.manage.load_json", side_effect=load,
        ), mock.patch(
            "maintenance.manage.download",
        ) as download, mock.patch(
            "maintenance.manage.prepare_workspace",
        ) as prepare, self.assertRaisesRegex(
            ManagerError,
            "distribution_path does not match ezquake coordinates",
        ):
            command_add(options)

        download.assert_not_called()
        prepare.assert_not_called()

    def test_add_dry_run_validates_staged_component_release_before_network(self) -> None:
        components = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/components.json").read_text(encoding="utf-8")
        )
        releases = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/component-releases.json").read_text(encoding="utf-8")
        )
        component = next(item for item in components["components"] if item["id"] == "ktx")
        component = copy.deepcopy(component)
        complete_release = copy.deepcopy(releases["components"]["ktx"])
        complete_release["version"] = "1.47+x86qw.19-review"
        complete_release["distribution_tag"] = "ktx-1.47-x86qw.19-review"
        artifact = {
            "filename": "reviewed-note.bin",
            "url": "https://example.invalid/reviewed-note.bin",
            "distribution_path": "mods/ktx/1.47/upstream/reviewed-note.bin",
            "size": 12,
            "sha256": "f" * 64,
        }
        complete_release["artifacts"].append(artifact)
        base = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": artifact["url"],
                "destination": f"dist/{artifact['distribution_path']}",
                "size": 12,
                "sha256": "f" * 64,
                "managed": True,
                "distribution_component": "ktx",
                "consumer": "development:ktx",
                "package": "ktx",
            }],
            "component": component,
            "replace": True,
        }

        for label, release, succeeds in (
            ("complete", complete_release, True),
            ("partial", {
                "version": "1.47",
                "distribution_component": "ktx",
            }, False),
        ):
            definition = {**base, "release": release}
            options = mock.Mock(
                definition=Path(f"staged-{label}.json"), dry_run=True, yes=False,
            )

            def load(path: Path) -> dict[str, object]:
                if Path(path) == options.definition.resolve():
                    return definition
                return json.loads(Path(path).read_text(encoding="utf-8"))

            with self.subTest(label=label), mock.patch(
                "maintenance.manage.load_json", side_effect=load,
            ), mock.patch(
                "maintenance.manage.download",
            ) as download, mock.patch(
                "maintenance.manage.prepare_workspace",
            ) as prepare:
                if succeeds:
                    self.assertEqual(0, command_add(options))
                else:
                    with self.assertRaisesRegex(ManagerError, "inventarios propostos invalidos"):
                        command_add(options)

            download.assert_not_called()
            prepare.assert_not_called()

    def test_add_rejects_replacement_release_crossing_distribution_namespace(self) -> None:
        components = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/components.json").read_text(encoding="utf-8")
        )
        releases = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/component-releases.json").read_text(encoding="utf-8")
        )
        component = copy.deepcopy(
            next(item for item in components["components"] if item["id"] == "ktx")
        )
        release = copy.deepcopy(releases["components"]["ktx"])
        release["version"] = "1.47+x86qw.99-review"
        release["distribution_tag"] = "ktx-1.47-x86qw.99-review"
        release["distribution_component"] = "td2"
        artifact = {
            "filename": "forged-td2.bin",
            "url": "https://example.invalid/forged-td2.bin",
            "distribution_path": "mods/td2/2.22/source/forged-td2.bin",
            "size": 12,
            "sha256": "f" * 64,
        }
        release["artifacts"] = [artifact]
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": artifact["url"],
                "destination": f"dist/{artifact['distribution_path']}",
                "size": artifact["size"],
                "sha256": artifact["sha256"],
                "managed": True,
                "distribution_component": "td2",
                "consumer": "development:td2",
                "package": "ktx",
            }],
            "component": component,
            "replace": True,
            "release": release,
        }

        with self.assertRaisesRegex(
            ManagerError,
            "release substituta precisa preservar distribution_component ktx",
        ):
            validate_distribution_change(definition, Path("cross-namespace.json"))

    def test_add_rejects_replacement_release_omitting_current_namespace(self) -> None:
        components = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/components.json").read_text(encoding="utf-8")
        )
        releases = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/component-releases.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (PROJECT_ROOT / "dist/manifest.json").read_text(encoding="utf-8")
        )["files"]
        relative, metadata = next(
            (path, record)
            for path, record in manifest.items()
            if record.get("component") == "nquake"
        )
        entry = {
            "url": metadata["url"],
            "destination": f"dist/{relative}",
            "size": metadata["size"],
            "sha256": metadata["sha256"],
            "managed": True,
            "distribution_component": metadata["component"],
            "consumer": metadata["consumer"],
            "package": metadata["package"],
        }
        component = copy.deepcopy(
            next(item for item in components["components"] if item["id"] == "ktx")
        )
        replacement = {
            "version": releases["reference"]["revision"][:12],
            "strategy": "reference-snapshot",
            "freshness": "reference-payload-current",
            "artifacts": [],
        }
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [entry],
            "component": component,
            "replace": True,
            "release": replacement,
        }

        with self.assertRaisesRegex(
            ManagerError,
            "release substituta precisa preservar distribution_component ktx",
        ):
            validate_distribution_change(definition, Path("omitted-namespace.json"))

    def test_add_rejects_staged_artifact_with_package_from_another_release(self) -> None:
        components = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/components.json").read_text(encoding="utf-8")
        )
        releases = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/component-releases.json").read_text(encoding="utf-8")
        )
        component = copy.deepcopy(
            next(item for item in components["components"] if item["id"] == "ktx")
        )
        release = copy.deepcopy(releases["components"]["ktx"])
        release["version"] = "1.47+x86qw.99-review"
        release["distribution_tag"] = "ktx-1.47-x86qw.99-review"
        artifact = {
            "filename": "reviewed-note.bin",
            "url": "https://example.invalid/reviewed-note.bin",
            "distribution_path": "mods/ktx/1.47/upstream/reviewed-note.bin",
            "size": 12,
            "sha256": "f" * 64,
        }
        release["artifacts"].append(artifact)
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": artifact["url"],
                "destination": f"dist/{artifact['distribution_path']}",
                "size": artifact["size"],
                "sha256": artifact["sha256"],
                "managed": True,
                "distribution_component": "ktx",
                "consumer": "development:ktx",
                "package": "total-destruction-2",
            }],
            "component": component,
            "replace": True,
            "release": release,
        }

        with self.assertRaisesRegex(
            ManagerError,
            "package do arquivo gerenciado deve ser ktx",
        ):
            validate_distribution_change(definition, Path("cross-package.json"))

    def test_add_dry_run_rejects_credentials_without_disclosing_them(self) -> None:
        secret = "never-print-this-password"
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": f"https://reviewer:{secret}@example.invalid/private.zip",
                "destination": "dist/mods/td2/2.22/source/private.zip",
                "size": 12,
                "sha256": "b" * 64,
            }],
        }
        options = mock.Mock(definition=Path("credential-change.json"), dry_run=True, yes=False)
        output = io.StringIO()

        with mock.patch(
            "maintenance.manage.load_json", return_value=definition,
        ), mock.patch(
            "maintenance.manage.download",
        ) as download, mock.patch(
            "maintenance.manage.require_clean_worktree",
        ) as clean, mock.patch(
            "maintenance.manage.confirm",
        ) as confirm, mock.patch(
            "maintenance.manage.prepare_workspace",
        ) as prepare, mock.patch(
            "maintenance.manage.apply_workspace",
        ) as apply, redirect_stdout(output), redirect_stderr(output):
            with self.assertRaises(ManagerError) as raised:
                command_add(options)

        download.assert_not_called()
        clean.assert_not_called()
        confirm.assert_not_called()
        prepare.assert_not_called()
        apply.assert_not_called()
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn(secret, str(raised.exception))

    def test_add_dry_run_rejects_unpinned_or_unsafe_remote_files(self) -> None:
        base = {
            "url": "https://example.invalid/private.zip",
            "destination": "dist/mods/td2/2.22/source/private.zip",
            "size": 12,
            "sha256": "c" * 64,
        }
        invalid_entries = {
            "missing-size": {key: value for key, value in base.items() if key != "size"},
            "zero-size": {**base, "size": 0},
            "missing-sha256": {key: value for key, value in base.items() if key != "sha256"},
            "invalid-sha256": {**base, "sha256": "not-a-digest"},
            "plain-http": {**base, "url": "http://example.invalid/private.zip"},
            "control-destination": {**base, "destination": "dist/mods/ok\n[OK] forged.zip"},
            "drive-destination": {**base, "destination": "dist/C:private.zip"},
            "distribution-root": {**base, "destination": "dist"},
            "distribution-root-slash": {**base, "destination": "dist/"},
            "empty-component": {**base, "destination": "dist/mods//private.zip"},
            "windows-reserved": {**base, "destination": "dist/mods/td2/CON.zip"},
            "trailing-dot": {**base, "destination": "dist/mods/td2/private.zip."},
            "trailing-space": {**base, "destination": "dist/mods/td2/private.zip "},
            "non-nfc": {**base, "destination": "dist/mods/td2/cafe\u0301.zip"},
        }

        for label, entry in invalid_entries.items():
            definition = {
                "format": 1,
                "project": "x86qw",
                "kind": "distribution-change",
                "files": [entry],
            }
            options = mock.Mock(definition=Path(f"{label}.json"), dry_run=True, yes=False)
            output = io.StringIO()
            with self.subTest(label=label), mock.patch(
                "maintenance.manage.load_json", return_value=definition,
            ), mock.patch(
                "maintenance.manage.download",
            ) as download, mock.patch(
                "maintenance.manage.require_clean_worktree",
            ) as clean, mock.patch(
                "maintenance.manage.confirm",
            ) as confirm, mock.patch(
                "maintenance.manage.prepare_workspace",
            ) as prepare, mock.patch(
                "maintenance.manage.apply_workspace",
            ) as apply, redirect_stdout(output), redirect_stderr(output), self.assertRaises(ManagerError):
                command_add(options)

            download.assert_not_called()
            clean.assert_not_called()
            confirm.assert_not_called()
            prepare.assert_not_called()
            apply.assert_not_called()

    def test_add_dry_run_rejects_managed_owner_and_consumer_mismatches(self) -> None:
        base = {
            "url": "https://example.invalid/private.zip",
            "destination": "dist/mods/td2/2.22/source/private.zip",
            "size": 12,
            "sha256": "c" * 64,
            "managed": True,
            "distribution_component": "td2",
            "consumer": "install:total-destruction-2",
            "package": "total-destruction-2",
        }
        invalid_entries = {
            "wrong-owner": ({
                **base,
                "destination": "dist/clients/ezquake/stable/private.zip",
            }, "outside the declared consumer scope"),
            "unconsumed-owner-path": ({
                **base,
                "destination": "dist/clients/ezquake/stable/private.zip",
                "distribution_component": "ezquake",
                "consumer": "install:ezquake",
                "package": None,
            }, "nao possui consumidor operacional"),
            "wrong-consumer": ({**base, "consumer": "runtime:host"}, "consumer nao declarado"),
            "legacy-consumer-on-new-path": ({
                "url": "https://example.invalid/x86qw-installer-9.9.8.zip",
                "destination": "dist/installer/packages/9.9.8/x86qw-installer-9.9.8.zip",
                "size": 12,
                "sha256": "e" * 64,
                "managed": True,
                "distribution_component": "installer",
                "consumer": "archive:installer-history",
            }, "consumer nao declarado"),
            "unsafe-component": ({
                **base, "distribution_component": "td2\n[OK] forged",
            }, "distribution_component e consumer seguros"),
            "unsafe-consumer": ({
                **base, "consumer": "install:td2\n[OK] forged",
            }, "distribution_component e consumer seguros"),
            "unsafe-package": ({**base, "package": "../td2"}, "identificador seguro"),
        }

        for label, (entry, expected_error) in invalid_entries.items():
            definition = {
                "format": 1,
                "project": "x86qw",
                "kind": "distribution-change",
                "files": [entry],
            }
            options = mock.Mock(definition=Path(f"{label}.json"), dry_run=True, yes=False)

            def load(path: Path) -> dict[str, object]:
                if Path(path) == options.definition.resolve():
                    return definition
                return json.loads(Path(path).read_text(encoding="utf-8"))

            with self.subTest(label=label), mock.patch(
                "maintenance.manage.load_json", side_effect=load,
            ), mock.patch(
                "maintenance.manage.download",
            ) as download, mock.patch(
                "maintenance.manage.prepare_workspace",
            ) as prepare, self.assertRaisesRegex(ManagerError, expected_error):
                command_add(options)

            download.assert_not_called()
            prepare.assert_not_called()

    def test_add_dry_run_rejects_intermediate_source_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real"
            real.mkdir()
            payload = real / "payload.bin"
            payload.write_bytes(b"reviewed payload")
            link = root / "link"
            try:
                link.symlink_to(real, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink indisponivel: {error}")
            definition = {
                "format": 1,
                "project": "x86qw",
                "kind": "distribution-change",
                "files": [{
                    "source": "link/payload.bin",
                    "destination": "dist/mods/td2/2.22/source/payload.bin",
                }],
            }
            options = mock.Mock(
                definition=root / "change.json", dry_run=True, yes=False,
            )

            with mock.patch(
                "maintenance.manage.load_json", return_value=definition,
            ), mock.patch(
                "maintenance.manage.download",
            ) as download, mock.patch(
                "maintenance.manage.prepare_workspace",
            ) as prepare, self.assertRaisesRegex(ManagerError, "sem symlink"):
                command_add(options)

            download.assert_not_called()
            prepare.assert_not_called()

    def test_add_dry_run_rejects_existing_casefold_collision(self) -> None:
        definition = {
            "format": 1,
            "project": "x86qw",
            "kind": "distribution-change",
            "files": [{
                "url": "https://example.invalid/private.zip",
                "destination": "dist/mods/td2/existing/private.zip",
                "size": 12,
                "sha256": "d" * 64,
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary) / "dist"
            existing = dist / "mods/td2/Existing"
            existing.mkdir(parents=True)
            options = mock.Mock(
                definition=Path("case-collision.json"), dry_run=True, yes=False,
            )

            with mock.patch(
                "maintenance.manage.DIST", dist,
            ), mock.patch(
                "maintenance.manage.load_json", return_value=definition,
            ), mock.patch(
                "maintenance.manage.download",
            ) as download, mock.patch(
                "maintenance.manage.prepare_workspace",
            ) as prepare, self.assertRaisesRegex(ManagerError, "colide"):
                command_add(options)

            download.assert_not_called()
            prepare.assert_not_called()

    def test_local_add_replaces_staged_hardlink_without_mutating_live_dist(self) -> None:
        replacement = b"reviewed executable payload"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "payload.bin"
            source.write_bytes(replacement)
            source.chmod(0o755)
            live = root / "live/dist/mods/example/payload.bin"
            staged = root / "work/dist/mods/example/payload.bin"
            live.parent.mkdir(parents=True)
            staged.parent.mkdir(parents=True)
            live.write_bytes(b"published bytes must stay untouched")
            try:
                os.link(live, staged)
            except OSError as error:
                self.skipTest(f"hardlink indisponivel: {error}")
            self.assertTrue(os.path.samefile(live, staged))
            entry = {
                "source": source.name,
                "destination": "dist/mods/example/payload.bin",
                "size": len(replacement),
                "sha256": hashlib.sha256(replacement).hexdigest(),
            }

            size, digest = fetch_definition_file(entry, root, staged)

            self.assertEqual(len(replacement), size)
            self.assertEqual(hashlib.sha256(replacement).hexdigest(), digest)
            self.assertEqual(b"published bytes must stay untouched", live.read_bytes())
            self.assertEqual(replacement, staged.read_bytes())
            self.assertFalse(os.path.samefile(live, staged))
            if os.name != "nt":
                self.assertEqual(0o755, stat.S_IMODE(staged.stat().st_mode))
            self.assertEqual([], list(staged.parent.glob(f".{staged.name}-*.tmp")))

    def test_local_add_detects_source_change_against_plan_before_replace(self) -> None:
        trusted = b"trusted bytes"
        changed = b"hostile bytes"
        self.assertEqual(len(trusted), len(changed))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "payload.bin"
            source.write_bytes(trusted)
            live = root / "live/dist/mods/example/payload.bin"
            staged = root / "work/dist/mods/example/payload.bin"
            live.parent.mkdir(parents=True)
            staged.parent.mkdir(parents=True)
            live.write_bytes(b"published bytes")
            try:
                os.link(live, staged)
            except OSError as error:
                self.skipTest(f"hardlink indisponivel: {error}")
            entry = {
                "source": source.name,
                "destination": "dist/mods/example/payload.bin",
                "size": len(trusted),
                "sha256": hashlib.sha256(trusted).hexdigest(),
            }
            plan = validate_definition_file(entry, root)
            source.write_bytes(changed)

            with self.assertRaisesRegex(ManagerError, "mudou depois da validacao"):
                fetch_definition_file(entry, root, staged, validated_plan=plan)

            self.assertEqual(b"published bytes", live.read_bytes())
            self.assertEqual(b"published bytes", staged.read_bytes())
            self.assertTrue(os.path.samefile(live, staged))
            self.assertEqual([], list(staged.parent.glob(f".{staged.name}-*.tmp")))

    def test_local_add_cleans_private_temporary_when_fsync_fails(self) -> None:
        payload = b"reviewed payload"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "payload.bin"
            source.write_bytes(payload)
            live = root / "live/dist/mods/example/payload.bin"
            staged = root / "work/dist/mods/example/payload.bin"
            live.parent.mkdir(parents=True)
            staged.parent.mkdir(parents=True)
            live.write_bytes(b"published bytes")
            try:
                os.link(live, staged)
            except OSError as error:
                self.skipTest(f"hardlink indisponivel: {error}")
            entry = {
                "source": source.name,
                "destination": "dist/mods/example/payload.bin",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }

            with mock.patch(
                "maintenance.manage.os.fsync", side_effect=OSError("simulated fsync failure"),
            ), self.assertRaisesRegex(ManagerError, "nao foi possivel copiar"):
                fetch_definition_file(entry, root, staged)

            self.assertEqual(b"published bytes", live.read_bytes())
            self.assertEqual(b"published bytes", staged.read_bytes())
            self.assertTrue(os.path.samefile(live, staged))
            self.assertEqual([], list(staged.parent.glob(f".{staged.name}-*.tmp")))

    def test_check_reports_review_required_without_downloading_a_candidate(self) -> None:
        revision = "a" * 40
        path = f"distributions/nquake/{revision}/non-gpl/qw/autoexec.cfg"
        assets = [Asset(
            "nquake",
            f"https://raw.githubusercontent.com/nQuake/distfiles/{revision}/non-gpl/qw/autoexec.cfg",
            path,
            12,
            "x86qw-client-bootstrap",
            None,
            "b" * 40,
        )]
        releases = {"reference": {"revision": revision}, "components": {}}
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch(
            "maintenance.manage.load_releases", return_value=releases,
        ), mock.patch(
            "maintenance.manage.check_updates", return_value=[],
        ), mock.patch(
            "maintenance.manage.discover_assets", return_value=assets,
        ), mock.patch(
            "maintenance.manage.load_manifest", return_value={"files": {}},
        ), mock.patch(
            "maintenance.manage.reference_content_changed", return_value=True,
        ), mock.patch(
            "maintenance.manage.download",
        ) as download, mock.patch(
            "maintenance.manage.download_asset",
        ) as download_asset, mock.patch(
            "maintenance.manage.prepare_workspace",
        ) as prepare, mock.patch(
            "maintenance.manage.sync_candidate",
        ) as synchronize, redirect_stdout(stdout), redirect_stderr(stderr):
            result = command_check(mock.Mock(offline=False, json=True))

        self.assertEqual(2, result)
        document = json.loads(stdout.getvalue())
        self.assertEqual([path], document["review_required"])
        download.assert_not_called()
        download_asset.assert_not_called()
        prepare.assert_not_called()
        synchronize.assert_not_called()

    def test_update_blocks_a_new_unpinned_nquake_revision(self) -> None:
        old_revision = "a" * 40
        new_revision = "b" * 40
        path = f"distributions/nquake/{new_revision}/non-gpl/qw/autoexec.cfg"
        assets = [Asset(
            "nquake",
            f"https://raw.githubusercontent.com/nQuake/distfiles/{new_revision}/non-gpl/qw/autoexec.cfg",
            path,
            12,
            "x86qw-client-bootstrap",
            None,
            "c" * 40,
        )]
        releases = {"reference": {"revision": old_revision}, "components": {}}
        options = mock.Mock(
            workers=1, yes=True, commit=False, push=False, message=None, dry_run=False,
        )

        with mock.patch(
            "maintenance.manage.load_releases", return_value=releases,
        ), mock.patch(
            "maintenance.manage.check_updates", return_value=[],
        ), mock.patch(
            "maintenance.manage.discover_assets", return_value=assets,
        ), mock.patch(
            "maintenance.manage.update_inventory_lines", return_value=[],
        ), mock.patch(
            "maintenance.manage.load_json", return_value={"packages": []},
        ), mock.patch(
            "maintenance.manage.load_manifest", return_value={"files": {}},
        ), mock.patch(
            "maintenance.manage.reference_content_changed", return_value=True,
        ), mock.patch(
            "maintenance.manage.download",
        ) as download, mock.patch(
            "maintenance.manage.require_clean_worktree",
        ) as clean, mock.patch(
            "maintenance.manage.confirm",
        ) as confirm, mock.patch(
            "maintenance.manage.prepare_workspace",
        ) as prepare, mock.patch(
            "maintenance.manage.sync_candidate",
        ) as synchronize, mock.patch(
            "maintenance.manage.apply_workspace",
        ) as apply, self.assertRaisesRegex(
            ManagerError, "Nenhum payload candidato foi baixado",
        ):
            command_update(options)

        download.assert_not_called()
        clean.assert_not_called()
        confirm.assert_not_called()
        prepare.assert_not_called()
        synchronize.assert_not_called()
        apply.assert_not_called()

    def test_update_refuses_unpinned_candidates_before_any_mutation(self) -> None:
        revision = "a" * 40
        assets = [
            Asset(
                "nquake",
                "https://example.invalid/current.cfg",
                f"distributions/nquake/{revision}/qw/current.cfg",
                1,
                "x86qw-client-bootstrap",
                "a" * 64,
            ),
            Asset(
                "ezquake",
                "https://example.invalid/new.zip",
                "clients/ezquake/nightly/new/windows-x64/new.exe",
                10,
            ),
        ]
        releases = {"reference": {"revision": revision}, "components": {}}
        options = mock.Mock(
            workers=1, yes=True, commit=False, push=False, message=None,
        )

        for dry_run in (False, True):
            options.dry_run = dry_run
            with self.subTest(dry_run=dry_run), mock.patch(
                "maintenance.manage.load_releases", return_value=releases,
            ), mock.patch(
                "maintenance.manage.check_updates", return_value=[],
            ), mock.patch(
                "maintenance.manage.discover_assets", return_value=assets,
            ), mock.patch(
                "maintenance.manage.update_inventory_lines", return_value=[],
            ), mock.patch(
                "maintenance.manage.load_json", return_value={"packages": []},
            ), mock.patch(
                "maintenance.manage.load_manifest", return_value={"files": {}},
            ), mock.patch(
                "maintenance.manage.reference_content_changed", return_value=True,
            ), mock.patch(
                "maintenance.manage.require_clean_worktree",
            ) as clean, mock.patch(
                "maintenance.manage.confirm",
            ) as confirm, mock.patch(
                "maintenance.manage.prepare_workspace",
            ) as prepare, mock.patch(
                "maintenance.manage.sync_candidate",
            ) as synchronize, mock.patch(
                "maintenance.manage.apply_workspace",
            ) as apply, self.assertRaisesRegex(
                ManagerError, "Nenhum payload candidato foi baixado",
            ):
                command_update(options)

            clean.assert_not_called()
            confirm.assert_not_called()
            prepare.assert_not_called()
            synchronize.assert_not_called()
            apply.assert_not_called()

    def test_nquake_delta_is_summarized_as_one_snapshot_change(self) -> None:
        delta = [
            {"path": f"distributions/nquake/{'a' * 40}/one", "status": "obsolete", "reason": "old"},
            {"path": f"distributions/nquake/{'b' * 40}/one", "status": "update-available", "reason": "new"},
            {"path": f"distributions/nquake/{'b' * 40}/two", "status": "update-available", "reason": "new"},
        ]

        summary = summarize_delta(delta)

        self.assertEqual(len(summary), 1)
        self.assertIn("aaaaaaaaaaaa (1 removidos)", summary[0])
        self.assertIn("bbbbbbbbbbbb (2 novos)", summary[0])

    def test_reference_update_preserves_x86qw_suffixes_and_overlay_version(self) -> None:
        releases = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/component-releases.json").read_text(encoding="utf-8")
        )
        new = "b" * 40

        changed = update_reference_releases(releases, new)

        self.assertTrue(changed)
        self.assertEqual(releases["reference"]["revision"], new)
        self.assertEqual(
            releases["components"]["final-arena"]["version"],
            "1.20+nquake.bbbbbbbbbbbb+x86qw.4",
        )
        self.assertEqual(releases["components"]["pro-x"]["version"], "1.1+x86qw.5")
        self.assertIn("nquake.bbbbbbbbbbbb", releases["components"]["team-fortress"]["version"])
        self.assertEqual("1.47+x86qw.19", releases["components"]["ktx"]["version"])
        self.assertEqual("ktx-1.47-x86qw.19", releases["components"]["ktx"]["distribution_tag"])

    def test_reference_advance_without_consumed_byte_changes_is_ignored(self) -> None:
        payload = b"same product bytes"
        digest = hashlib.sha1(f"blob {len(payload)}\0".encode() + payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = f"distributions/nquake/{'a' * 40}/non-gpl/qw/autoexec.cfg"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(payload)
            manifest = {"files": {relative: {
                "url": "https://example.invalid/old",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "package": "x86qw-client-bootstrap",
            }}}
            assets = [Asset(
                "nquake", "https://example.invalid/new", f"distributions/nquake/{'b' * 40}/non-gpl/qw/autoexec.cfg",
                None, "x86qw-client-bootstrap", None, digest,
            )]

            self.assertFalse(reference_content_changed(assets, manifest, root=root))

    def test_ezquake_catalog_and_recipes_are_rebuilt_from_the_same_assets(self) -> None:
        catalog = json.loads((PROJECT_ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8"))
        manifest = json.loads((PROJECT_ROOT / "dist/manifest.json").read_text(encoding="utf-8"))
        assets = []
        for package in catalog["packages"]:
            if package["component"] == "ezquake":
                assets.append(Asset(
                    "ezquake", package["origin_url"], package["distribution_path"], package["size"],
                ))
        assets.append(Asset(
            "ezquake",
            "https://example.invalid/ezquake-source.tar.gz",
            "clients/ezquake/stable/3.6.9/source/ezquake-source-3.6.9.tar.gz",
            1,
            "ezquake-stable",
        ))
        with tempfile.TemporaryDirectory() as temporary, mock.patch(
            "maintenance.manage.ezquake_source_revision", return_value="c" * 40,
        ):
            recipes = Path(temporary) / "recipes"

            stable, nightly = update_ezquake_catalog(copy.deepcopy(catalog), assets, manifest, recipes)

            self.assertEqual(stable, "3.6.9")
            self.assertEqual(nightly, "20260616-101233_a86996a")
            self.assertEqual(len(list(recipes.rglob("*.json"))), 3)
            generated = json.loads(next(recipes.rglob("macos-universal.json")).read_text())
            self.assertIn("x86dx2/x86qw/releases", generated["package"]["urls"][0])

    def test_github_release_coordinates_support_current_and_legacy_repositories(self) -> None:
        filename = "x86qw-installer-0.1.0.zip"
        self.assertEqual(
            ("x86dx2/x86qw", "x86qw-installer-0.1.0"),
            github_release_coordinates(
                f"https://github.com/x86dx2/x86qw/releases/download/x86qw-installer-0.1.0/{filename}",
                filename,
            ),
        )

    def test_component_release_is_created_without_taking_latest(self) -> None:
        package = {
            "component": "core",
            "package": "x86qw-core-id1",
            "version": "0.1.0",
            "filename": "x86qw-core-id1-0.1.0.zip",
            "size": 1,
            "sha256": "0" * 64,
            "urls": [
                "https://github.com/x86dx2/x86qw/releases/download/"
                "x86qw-content-core-0.1.0/x86qw-core-id1-0.1.0.zip"
            ],
            "mirror_title": "x86QW Content · Dados base 0.1.0",
            "mirror_notes": "Dados base.",
            "mirror_latest": False,
        }
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / package["filename"]
            artifact.write_bytes(b"x")
            with mock.patch("maintenance.manage.local_artifact", return_value=artifact):
                with mock.patch("maintenance.manage.github_release", return_value=None):
                    with mock.patch("maintenance.manage.github_latest_release_tag", return_value=None):
                        with mock.patch("maintenance.manage.run") as run:
                            publish_github({"packages": [package]}, dry_run=False)
        create = run.call_args_list[0].args[0]
        self.assertIn("--latest=false", create)
        self.assertIn("x86QW Content · Dados base 0.1.0", create)
        self.assertEqual(
            ("x86dx2/x86qw", "x86qw-content-test-0.1.0"),
            github_release_coordinates(
                "https://github.com/x86dx2/x86qw/releases/download/x86qw-content-test-0.1.0/"
                "x86qw-test-0.1.0.zip",
                "x86qw-test-0.1.0.zip",
            ),
        )

    def test_public_parser_exposes_the_complete_lifecycle(self) -> None:
        choices = parser()._subparsers._group_actions[0].choices
        self.assertEqual(set(choices), {"check", "update", "add", "verify", "build", "publish", "commit"})

    def test_update_summary_exposes_clients_ktx_td2_and_their_sources(self) -> None:
        catalog = json.loads((PROJECT_ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8"))
        releases = json.loads(
            (PROJECT_ROOT / "maintenance/inventory/component-releases.json").read_text(encoding="utf-8")
        )
        assets = [
            Asset("ezquake", package["origin_url"], package["distribution_path"], package["size"])
            for package in catalog["packages"]
            if package["component"] == "ezquake"
        ]
        assets.append(Asset(
            "ezquake",
            "https://example.invalid/ezquake-source.tar.gz",
            "clients/ezquake/stable/3.6.9/source/ezquake-source-3.6.9.tar.gz",
            1,
            "ezquake-stable",
        ))
        results = [
            {
                "component": identifier,
                "installed": str(release["version"]),
                "latest_source": str(release.get("upstream", {}).get("release", release["version"])),
                "status": "current",
                "strategy": str(release["strategy"]),
            }
            for identifier, release in releases["components"].items()
        ]

        output = "\n".join(update_inventory_lines(results, assets, releases, catalog))
        self.assertIn("ezQuake stable: 3.6.9 (3 plataformas)", output)
        self.assertIn("ezQuake nightly: 20260616-101233_a86996a (3 plataformas)", output)
        self.assertIn("Interface e recursos visuais nQuake: e4cb23d40aa2", output)
        self.assertIn("QRP alta resolução: e4cb23d40aa2+x86qw.1", output)
        self.assertIn("Final Arena: upstream 1.20; pacote x86QW 1.20+nquake.e4cb23d40aa2+x86qw.4", output)
        self.assertIn("Pro-X: upstream 1.1; pacote x86QW 1.1+x86qw.5", output)
        self.assertIn("Team Fortress: upstream 2.9; pacote x86QW 2.9+nquake.e4cb23d40aa2+x86qw.6", output)
        self.assertIn("KTX x86QW", output)
        self.assertIn("dist/mods/ktx/1.47/upstream/qwprogs-qvm.zip", output)
        self.assertIn("Total Destruction 2", output)
        self.assertIn("dist/mods/td2/2.22/source/", output)

    def test_contextual_layout_has_no_legacy_root_directories(self) -> None:
        for name in ("distribution", "installer", "inventory", "recipes", "tools", "tests"):
            self.assertFalse((PROJECT_ROOT / name).exists(), name)
        self.assertFalse((PROJECT_ROOT / "maintenance/inventory/upstream-current.json").exists())
        self.assertTrue((PROJECT_ROOT / "site/wrangler.jsonc").is_file())


if __name__ == "__main__":
    unittest.main()
