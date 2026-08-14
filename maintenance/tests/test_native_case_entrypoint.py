from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

from maintenance.native_case_entrypoint import (
    CANONICAL_CASES,
    _CLIENT_CASES,
    Candidate,
    CandidateArtifact,
    CandidateCaseError,
    _WINDOW_PROBE_SOURCE,
    _window_titles,
    _qwfwd_challenge_token,
    _qwfwd_remote_ready_packet,
    _materialize_qwfwd_config,
    _cleanup_case_scratch,
    _native_frogbot_config_payload,
    _frogbot_log_evidence,
    _prepare_native_frogbot_config,
    _remove_native_frogbot_config,
    _prepare_legacy_state,
    _run_installed_launcher_contract,
    _start_tcp_service,
    build_case_command,
    load_candidate,
    validate_case_name,
)


class NativeCaseEntrypointTests(unittest.TestCase):
    def _candidate(self, root: Path) -> Path:
        candidate = root / "candidate"
        candidate.mkdir()
        payload = candidate / "runtime/clients/stable.zip"
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"candidate bytes")
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        manifest = {
            "format": 1,
            "project": "x86qw",
            "version": "1.0.0-rc.1",
            "commit": "c" * 40,
            "artifacts": {
                "runtime/clients/stable.zip": {
                    "size": payload.stat().st_size,
                    "sha256": digest,
                },
            },
        }
        (candidate / "candidate.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8",
        )
        return candidate

    def test_only_the_closed_functional_case_protocol_is_accepted(self) -> None:
        self.assertEqual(25, len(CANONICAL_CASES))
        self.assertIn("game-ktx-frogbot", CANONICAL_CASES)
        self.assertIn("migration-0.7.13-real", CANONICAL_CASES)
        self.assertIn("lifecycle-update-apply", CANONICAL_CASES)
        self.assertIn("lifecycle-upgrade-apply", CANONICAL_CASES)
        self.assertIn("lifecycle-repair-corruption", CANONICAL_CASES)
        self.assertIn("lifecycle-migrate-apply", CANONICAL_CASES)
        self.assertIn("lifecycle-purge", CANONICAL_CASES)
        for name in CANONICAL_CASES:
            self.assertEqual(name, validate_case_name(name))
        with self.assertRaises(CandidateCaseError):
            validate_case_name("unknown-case")

    def test_frogbot_observation_accepts_ktx_runtime_command_and_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "qw" / "qconsole.log"
            log.parent.mkdir()
            log.write_text(
                "cmd botcmd skill 5\ncmd botcmd addbot 5\nspawned /x86QW\n",
                encoding="utf-8",
            )
            evidence = _frogbot_log_evidence(Path(temporary))
            self.assertTrue(evidence["frogbot_spawned"])
            self.assertTrue(evidence["frogbot_skill"])
            self.assertTrue(evidence["frogbot_named"])

    def test_frogbot_case_uses_an_exec_config_for_post_map_commands(self) -> None:
        _channel, arguments, _map_name = _CLIENT_CASES["game-ktx-frogbot"]
        self.assertNotIn("+tempalias", arguments)
        self.assertEqual(
            'tempalias x86qw_native_frogbot "wait;wait;cmd botcmd skill 5;cmd botcmd addbot 5"\n'
            'tempalias on_enter "x86qw_native_frogbot"\n',
            _native_frogbot_config_payload().decode("ascii"),
        )

    def test_frogbot_fixture_restores_the_personal_ktx_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            user_config = target / "qw/x86qw-ktx-user.cfg"
            user_config.parent.mkdir(parents=True)
            original = b"seta name x86QW-native-test\n"
            user_config.write_bytes(original)
            state = _prepare_native_frogbot_config(target)
            self.assertEqual(
                original + b"\nexec x86qw-native-smoke-frogbot.cfg\n",
                user_config.read_bytes(),
            )
            self.assertEqual(_native_frogbot_config_payload(), state[0].read_bytes())
            _remove_native_frogbot_config(state)
            self.assertEqual(original, user_config.read_bytes())
            self.assertFalse(state[0].exists())

    def test_candidate_artifacts_are_hash_checked_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            loaded = load_candidate(candidate)
            self.assertEqual("1.0.0-rc.1", loaded.version)
            self.assertIn("runtime/clients/stable.zip", loaded.artifacts)
            (candidate / "runtime/clients/stable.zip").write_bytes(b"tampered")
            with self.assertRaisesRegex(CandidateCaseError, "diverge"):
                load_candidate(candidate)

    def test_symlinked_candidate_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            payload = candidate / "runtime/clients/stable.zip"
            payload.unlink()
            payload.symlink_to(candidate / "candidate.json")
            with self.assertRaisesRegex(CandidateCaseError, "symlink"):
                load_candidate(candidate)

    def test_case_dispatch_rejects_a_missing_candidate_owned_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            with self.assertRaisesRegex(CandidateCaseError, "artefato nativo ausente"):
                build_case_command(
                    candidate=load_candidate(candidate),
                    case="mvdsv-mvd",
                    scratch=Path(temporary) / "scratch",
                )

    def test_legacy_migration_seed_replaces_current_receipts_with_fixture_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_root = root / "candidate"
            fixture_root = candidate_root / "runtime/native-smoke/macos-arm64/fixtures/migrations/0.7.13"
            fixture_root.joinpath(".x86qw").mkdir(parents=True)
            fixture_files = {
                "VERSION": b"0.7.13\n",
                ".x86qw/state.json": b'{"format":1,"project":"x86qw"}\n',
                ".x86qw/cli.receipt": b'{"format":1,"project":"x86qw","version":"0.7.13"}\n',
                ".x86qw/ktx.receipt": b"format\t1\ncomponent\tktx\nselection\t0.7.13\n",
                ".x86qw/ktx.inventory": b"qw/ktx.pk3\t" + b"d" * 64 + b"\n",
            }
            artifacts: dict[str, CandidateArtifact] = {}
            for relative, payload in fixture_files.items():
                path = fixture_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                artifacts[
                    "runtime/native-smoke/macos-arm64/fixtures/migrations/0.7.13/" + relative
                ] = CandidateArtifact(
                    name=relative,
                    path=path,
                    size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
            target = root / "target"
            metadata = target / ".x86qw"
            (metadata / "cli").mkdir(parents=True)
            (metadata / "components/ktx").mkdir(parents=True)
            (metadata / "state.json").write_text(
                '{"format":2,"project":"x86qw","installation_version":"1.0.0-rc.2"}\n',
                encoding="utf-8",
            )
            (metadata / "cli/receipt").write_bytes(b"current cli")
            (metadata / "components/ktx/receipt").write_bytes(b"current ktx receipt")
            (metadata / "components/ktx/inventory").write_bytes(b"current ktx inventory")
            (target / "id1").mkdir(parents=True)
            (target / "id1/pak0.pak").write_bytes(b"pak0")
            (target / "id1/pak1.pak").write_bytes(b"pak1")

            candidate = Candidate(
                root=candidate_root,
                version="1.0.0-rc.2",
                commit="c" * 40,
                artifacts=artifacts,
            )
            _prepare_legacy_state(candidate, target, source_version="0.7.13")

            self.assertFalse((metadata / "cli/receipt").exists())
            self.assertFalse((metadata / "components/ktx/receipt").exists())
            self.assertFalse((metadata / "components/ktx/inventory").exists())
            self.assertEqual(fixture_files[".x86qw/cli.receipt"], (metadata / "cli.receipt").read_bytes())
            self.assertEqual(fixture_files[".x86qw/ktx.receipt"], (metadata / "ktx.receipt").read_bytes())
            self.assertEqual(fixture_files[".x86qw/ktx.inventory"], (metadata / "ktx.inventory").read_bytes())
            state = json.loads((metadata / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("0.7.13", state["installation_version"])

    def test_case_dispatch_is_literal_and_uses_a_candidate_owned_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            service = candidate / "runtime/servers/mvdsv/1.11/x86qw/runtime/macos-arm64/mvdsv"
            service.parent.mkdir(parents=True)
            service.write_bytes(b"native mvdsv")
            manifest_path = candidate / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"][
                "runtime/servers/mvdsv/1.11/x86qw/runtime/macos-arm64/mvdsv"
            ] = {
                "size": service.stat().st_size,
                "sha256": hashlib.sha256(service.read_bytes()).hexdigest(),
            }
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8",
            )
            prepared = build_case_command(
                candidate=load_candidate(candidate),
                case="mvdsv-mvd",
                scratch=root / "scratch",
                state_root=root / "state",
            )
            self.assertEqual(service, prepared.executable)
            self.assertEqual("mvdsv", Path(prepared.argv[0]).name)
            self.assertIn("-basedir", prepared.argv)
            self.assertIn(str(root / "state/instalação espaço"), prepared.argv)
            self.assertIn("+sv_progtype", prepared.argv)
            self.assertIn("2", prepared.argv)
            self.assertIn("+map", prepared.argv)
            self.assertIn("dm6", prepared.argv)
            self.assertNotIn("-version", prepared.argv)
            self.assertEqual(root / "state/instalação espaço", prepared.cwd)
            self.assertNotIn(candidate.as_posix(), prepared.argv[0])
            self.assertIs(prepared.shell, False)

    def test_service_dispatches_use_candidate_binaries_and_real_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            manifest_path = candidate / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            services = {
                "mvdsv-mvd": "runtime/servers/mvdsv/1.11/x86qw/runtime/macos-arm64/mvdsv",
                "qtv-stream": "runtime/services/qtv/025ca949aca0/x86qw/runtime/macos-arm64/qtv",
                "qwfwd-forward": "runtime/services/qwfwd/1.30/x86qw/runtime/macos-arm64/qwfwd",
            }
            for case, relative in services.items():
                artifact = candidate / relative
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(f"native {case}".encode())
                manifest["artifacts"][relative] = {
                    "size": artifact.stat().st_size,
                    "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                }
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8",
            )
            loaded = load_candidate(candidate)

            for case, relative in services.items():
                with self.subTest(case=case):
                    prepared = build_case_command(
                        candidate=loaded,
                        case=case,
                        scratch=root / "scratch" / case,
                        state_root=root / "state",
                    )
                    self.assertEqual(candidate / relative, prepared.executable)
                    self.assertNotIn(candidate.as_posix(), prepared.argv[0])
                    self.assertIs(prepared.shell, False)
                    self.assertNotIn("-version", prepared.argv)
                    if case == "mvdsv-mvd":
                        self.assertEqual(root / "state/instalação espaço", prepared.cwd)
                    else:
                        self.assertNotEqual(root / "state/instalação espaço", prepared.cwd)

    def test_install_case_dispatches_stable_release_from_candidate_without_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            archive = candidate / "installer/x86qw-installer-1.0.0.zip"
            archive.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("x86qw-installer-1.0.0/x86qw.pyz", b"installer")
                bundle.writestr(
                    "x86qw-installer-1.0.0/x86qw.sh",
                    "#!/bin/sh\n",
                )
                bundle.writestr(
                    "x86qw-installer-1.0.0/x86qw.cmd",
                    "@echo off\r\n",
                )
            stable = candidate / (
                "runtime/clients/ezquake/stable/3.6.9/"
                "macos-universal/ezQuake-macOS-universal.zip"
            )
            stable.parent.mkdir(parents=True, exist_ok=True)
            stable.write_bytes(b"stable runtime")
            manifest_path = candidate / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for path in (archive, stable):
                relative = path.relative_to(candidate).as_posix()
                manifest["artifacts"][relative] = {
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8",
            )

            prepared = build_case_command(
                candidate=load_candidate(candidate),
                case="install-clean-space-unicode",
                scratch=root / "scratch",
            )

            self.assertEqual(
                (
                    sys.executable,
                    str(root / "scratch/extracted/installer/x86qw.pyz"),
                    "--online-only",
                    "--platform", "macos",
                    "--channel", "stable",
                    "--release", "3.6.9",
                    "--native-profile", "complete",
                    "install",
                    str(root / "scratch/instalação espaço"),
                ),
                prepared.argv,
            )
            self.assertTrue(
                (root / "scratch/extracted/installer/x86qw.sh").is_file()
            )
            self.assertTrue(
                (root / "scratch/extracted/installer/x86qw.cmd").is_file()
            )

    def test_installed_launcher_contract_executes_all_required_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = load_candidate(self._candidate(root))
            target = root / "instalação espaço"
            target.mkdir()
            launcher = target / "x86qw.sh"
            launcher.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  help) printf '%s\\n' 'changes migrate' ;;\n"
                "  version) printf '%s\\n' 'x86QW 1.0.0-rc.1' ;;\n"
                "  changes) printf '%s\\n' 'Mudanças locais' ;;\n"
                "  migrate) test \"$2\" = '--dry-run' && printf '%s\\n' 'Migração' ;;\n"
                "  *) exit 2 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            launcher.chmod(0o700)

            observation = _run_installed_launcher_contract(
                candidate=candidate,
                target=target,
                environment={"PATH": "/usr/bin:/bin"},
            )

            self.assertEqual("x86qw.sh", observation["launcher"])
            self.assertEqual(
                ["help", "version", "changes", "migrate"],
                [item["name"] for item in observation["commands"]],
            )
            self.assertTrue(all(item["exit_code"] == 0 for item in observation["commands"]))
            self.assertTrue(observation["help_lists_changes"])
            self.assertTrue(observation["help_lists_migrate"])
            self.assertTrue(observation["version_matches"])
            self.assertTrue(observation["changes_executed"])
            self.assertTrue(observation["migrate_dry_run_executed"])

    def test_client_bundle_with_directory_entries_extracts_app_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            stable = candidate / (
                "runtime/clients/ezquake/stable/3.6.9/"
                "macos-universal/ezQuake-macOS-universal.zip"
            )
            stable.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(stable, "w") as bundle:
                bundle.writestr("ezQuake.app/", b"")
                bundle.writestr("ezQuake.app/Contents/", b"")
                bundle.writestr("ezQuake.app/Contents/MacOS/", b"")
                bundle.writestr(
                    "ezQuake.app/Contents/MacOS/ezQuake", b"native binary",
                )
                bundle.writestr("ezQuake.app/Contents/Info.plist", b"plist")
                bundle.writestr(
                    "ezQuake.app/Contents/Resources/ezquake.icns", b"icon",
                )
            manifest_path = candidate / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            relative = stable.relative_to(candidate).as_posix()
            manifest["artifacts"][relative] = {
                "size": stable.stat().st_size,
                "sha256": hashlib.sha256(stable.read_bytes()).hexdigest(),
            }
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8",
            )

            prepared = build_case_command(
                candidate=load_candidate(candidate),
                case="client-stable-window-map-exit",
                scratch=root / "scratch",
                state_root=root / "state",
            )

            self.assertEqual("ezQuake", prepared.executable.name)
            self.assertTrue(prepared.executable.is_file())
            self.assertEqual(root / "state/instalação espaço", prepared.cwd)
            self.assertIn("-nohome", prepared.argv)
            self.assertIn("-basedir", prepared.argv)
            self.assertIn("-condebug", prepared.argv)
            self.assertIn("+cfg_save_onquit", prepared.argv)
            self.assertEqual("dm6", prepared.argv[-1])
            app = prepared.executable.parents[2]
            self.assertEqual("ezQuake.app", app.name)
            self.assertTrue((app / "Contents/Info.plist").is_file())
            self.assertTrue((app / "Contents/Resources/ezquake.icns").is_file())

    def test_window_titles_falls_back_to_window_server_when_system_events_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            probe = root / ".x86qw-window-probe"
            probe.write_bytes(b"probe")
            probe.chmod(0o700)
            completed = [
                type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
                type("Completed", (), {"returncode": 0, "stdout": "ezQuake\tstandby - 1/8 - dm6\n", "stderr": ""})(),
            ]
            with patch("maintenance.native_case_entrypoint.sys.platform", "darwin"):
                with patch(
                    "maintenance.native_case_entrypoint.subprocess.run",
                    side_effect=completed,
                ) as run:
                    self.assertEqual("standby - 1/8 - dm6", _window_titles(1234, root))
            self.assertEqual(2, run.call_count)

    def test_window_probe_includes_window_server_windows_outside_on_screen_filter(self) -> None:
        self.assertIn("[.optionAll, .excludeDesktopElements]", _WINDOW_PROBE_SOURCE)
        self.assertNotIn("optionOnScreenOnly", _WINDOW_PROBE_SOURCE)

    def test_qwfwd_challenge_token_strips_native_line_ending(self) -> None:
        self.assertEqual(
            "-777",
            _qwfwd_challenge_token(b"\xff\xff\xff\xffc-777\n\0"),
        )

    def test_tcp_service_retries_when_ephemeral_port_is_lost_before_bind(self) -> None:
        first = type("Process", (), {"poll": lambda self: 1})()
        second = type("Process", (), {"poll": lambda self: None})()
        commands = iter([
            (("mvdsv", "--port", "41001"), 41001),
            (("mvdsv", "--port", "41002"), 41002),
        ])
        with patch(
            "maintenance.native_case_entrypoint.subprocess.Popen",
            side_effect=[first, second],
        ) as popen, patch(
            "maintenance.native_case_entrypoint._wait_tcp_listener",
            side_effect=[
                CandidateCaseError("MVDSV terminou antes de abrir a porta nativa"),
                None,
            ],
        ), patch(
            "maintenance.native_case_entrypoint._terminate_service_process",
            return_value=(b"bind failed", 1),
        ) as terminate:
            process, argv, port = _start_tcp_service(
                lambda: next(commands),
                cwd=Path("/tmp/native-service"),
                environment={"PATH": "/usr/bin:/bin"},
                label="MVDSV",
            )

        self.assertIs(second, process)
        self.assertEqual(("mvdsv", "--port", "41002"), argv)
        self.assertEqual(41002, port)
        self.assertEqual(2, popen.call_count)
        terminate.assert_called_once_with(first)

    def test_qwfwd_remote_ready_requires_server_packet_after_connection(self) -> None:
        self.assertTrue(
            _qwfwd_remote_ready_packet(b"\xff\xff\xff\xffn\nNATIVE-READY\n"),
        )
        self.assertFalse(_qwfwd_remote_ready_packet(b"\xff\xff\xff\xffj"))
        self.assertFalse(_qwfwd_remote_ready_packet(b"NATIVE-FORWARD"))

    def test_qwfwd_config_port_override_is_derived_only_in_scratch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "candidate-qwfwd.cfg"
            source.write_bytes(
                b'set hostname "candidate"\n'
                b"set net_port 30000\n"
                b"set net_ip 127.0.0.1\n",
            )
            artifact = CandidateArtifact(
                name="runtime/services/qwfwd/qwfwd.cfg",
                path=source,
                size=source.stat().st_size,
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            )
            destination = root / "scratch/qwfwd.cfg"
            _materialize_qwfwd_config(artifact, destination, 43123)
            self.assertIn(b"set net_port 43123\n", destination.read_bytes())
            self.assertIn(b'set hostname "candidate"\n', destination.read_bytes())
            self.assertIn(b"set net_port 30000\n", source.read_bytes())

    def test_case_scratch_cleanup_removes_only_the_case_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case_scratch = root / "cases" / "client-stable"
            (case_scratch / "extracted").mkdir(parents=True)
            (case_scratch / "extracted" / "payload").write_bytes(b"temporary")
            shared = root / "instalação espaço"
            shared.mkdir()
            _cleanup_case_scratch(case_scratch)
            self.assertFalse(case_scratch.exists())
            self.assertTrue(shared.is_dir())


if __name__ == "__main__":
    unittest.main()
