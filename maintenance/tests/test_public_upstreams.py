from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "maintenance/tools"))

import public_upstreams  # noqa: E402
from public_upstreams import (  # noqa: E402
    GIT_TREE_MAX_BYTES,
    _posix_process_group_status,
    _run_bounded_command,
    _windows_job_kernel32,
    _windows_ntdll,
    git_remote_revision,
    git_remote_tree,
    github_commit_revision,
    github_latest_release,
    remote_content_length,
    run_git,
)


class PublicUpstreamTests(unittest.TestCase):
    def test_git_remote_revision_uses_the_public_git_protocol(self) -> None:
        with mock.patch(
            "public_upstreams.run_git",
            return_value="a" * 40 + "\trefs/heads/master\n",
        ) as run:
            self.assertEqual("a" * 40, git_remote_revision("https://example.invalid/repo.git", "refs/heads/master"))
        arguments = run.call_args.args[0]
        self.assertEqual(["ls-remote", "--exit-code"], arguments[:2])
        self.assertNotIn("api.github.com", " ".join(arguments))
        self.assertLessEqual(run.call_args.kwargs["stdout_limit"], 4 * 1024 * 1024)

    def test_git_repository_rejects_non_https_and_ambiguous_urls_before_spawn(self) -> None:
        invalid = (
            "http://example.invalid/repo.git",
            "file:///tmp/repo.git",
            "git@example.invalid:repo.git",
            "https://user@example.invalid/repo.git",
            "https://example.invalid:8443/repo.git",
            "https://example.invalid/repo.git?ref=main",
            "https://example.invalid/repo.git#main",
            "https://example.invalid/repo\\name.git",
            "https://example.invalid/repo git",
        )
        with mock.patch("public_upstreams.run_git") as run:
            for repository in invalid:
                with self.subTest(repository=repository), self.assertRaises(ValueError):
                    git_remote_revision(repository, "refs/heads/master")
        run.assert_not_called()

    def test_git_repository_rejection_does_not_echo_credentials_or_controls(self) -> None:
        sentinel = "X86QW_URL_SECRET_SENTINEL"
        invalid = (
            f"https://operator:{sentinel}@example.invalid/repo.git",
            f"https://example.invalid/repo.git?token={sentinel}\nforged",
        )
        with mock.patch("public_upstreams.run_git") as run:
            for repository in invalid:
                with self.subTest(repository=repository), self.assertRaises(ValueError) as raised:
                    git_remote_revision(repository, "refs/heads/master")
                self.assertNotIn(sentinel, str(raised.exception))
                self.assertNotIn("\n", str(raised.exception))
        run.assert_not_called()

    def test_git_ref_rejects_option_and_ref_syntax_injection_before_spawn(self) -> None:
        invalid = ("--upload-pack=evil", "refs/heads/a..b", "refs/heads/a@{1}", "refs//heads/main")
        with mock.patch("public_upstreams.run_git") as run:
            for ref in invalid:
                with self.subTest(ref=ref), self.assertRaises(ValueError):
                    git_remote_revision("https://example.invalid/repo.git", ref)
        run.assert_not_called()

    def test_git_tree_is_read_without_checking_out_blobs(self) -> None:
        outputs = [
            "",
            "b" * 40 + "\n",
            b"100644 blob " + b"c" * 40 + b"\tqw/file.pk3\0",
        ]
        with mock.patch("public_upstreams.run_git", side_effect=outputs) as run:
            revision, entries = git_remote_tree("https://example.invalid/repo.git", "master")
        self.assertEqual("b" * 40, revision)
        self.assertEqual(
            ("qw/file.pk3", "c" * 40, None),
            (entries[0].path, entries[0].sha1, entries[0].size),
        )
        clone = run.call_args_list[0].args[0]
        self.assertIn("--filter=blob:none", clone)
        self.assertIn("--no-checkout", clone)
        self.assertEqual(GIT_TREE_MAX_BYTES, run.call_args_list[0].kwargs["workspace_limit"])
        self.assertIsNotNone(run.call_args_list[0].kwargs["workspace"])
        tree = run.call_args_list[2].args[0]
        self.assertNotIn("-l", tree)
        self.assertEqual(GIT_TREE_MAX_BYTES, run.call_args_list[2].kwargs["workspace_limit"])
        deadlines = {call.kwargs["deadline"] for call in run.call_args_list}
        self.assertEqual(1, len(deadlines))

    def test_git_tree_rejects_a_record_with_an_unexpected_size_field(self) -> None:
        outputs = [
            "",
            "b" * 40 + "\n",
            b"100644 blob " + b"c" * 40 + b" 123\tqw/file.pk3\0",
        ]
        with mock.patch("public_upstreams.run_git", side_effect=outputs):
            with self.assertRaisesRegex(ValueError, "entrada invalida"):
                git_remote_tree("https://example.invalid/repo.git", "master")

    def test_git_tree_rejects_malformed_blob_identity(self) -> None:
        outputs = [
            "",
            "b" * 40 + "\n",
            b"100644 blob not-a-sha1\tqw/file.pk3\0",
        ]
        with mock.patch("public_upstreams.run_git", side_effect=outputs):
            with self.assertRaisesRegex(ValueError, "entrada invalida"):
                git_remote_tree("https://example.invalid/repo.git", "master")

    def test_bounded_process_does_not_use_a_shell_and_disables_prompts(self) -> None:
        command = [sys.executable, "-c", "print('ok', end='')"]
        spawn_calls: list[dict[str, object]] = []
        real_popen = subprocess.Popen

        def spawn(*arguments, **options):
            spawn_calls.append(options)
            return real_popen(*arguments, **options)

        dangerous_environment = {
            "PATH": os.environ.get("PATH", ""),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "url.file:///tmp/.insteadOf",
            "GIT_CONFIG_VALUE_0": "https://",
            "GIT_CONFIG_GLOBAL": "/tmp/hostile-global-config",
            "GIT_CONFIG_SYSTEM": "/tmp/hostile-system-config",
            "GIT_OBJECT_DIRECTORY": "/tmp/outside-workspace",
            "GIT_SSL_NO_VERIFY": "1",
            "GIT_CURL_VERBOSE": "1",
            "GIT_ASKPASS": "/tmp/hostile-askpass",
            "GIT_PROXY_COMMAND": "/tmp/hostile-proxy",
            "SSH_ASKPASS": "/tmp/hostile-ssh-askpass",
            "CURL_CA_BUNDLE": "/tmp/hostile-ca.pem",
            "SSL_CERT_FILE": "/tmp/hostile-cert.pem",
            "HTTPS_PROXY": "http://insecure-proxy.invalid:8080",
            "HTTP_PROXY": "http://insecure-proxy.invalid:8080",
            "ALL_PROXY": "socks5://insecure-proxy.invalid:1080",
        }
        with (
            mock.patch.dict(os.environ, dangerous_environment, clear=True),
            mock.patch("public_upstreams.subprocess.Popen", side_effect=spawn),
        ):
            result = _run_bounded_command(command, deadline=time.monotonic() + 5)
        self.assertEqual(b"ok", result.stdout)
        self.assertIs(spawn_calls[0]["shell"], False)
        child_environment = spawn_calls[0]["env"]
        self.assertEqual("0", child_environment["GIT_TERMINAL_PROMPT"])
        self.assertEqual("never", child_environment["GCM_INTERACTIVE"])
        self.assertEqual("1", child_environment["GIT_CONFIG_NOSYSTEM"])
        self.assertEqual(os.devnull, child_environment["GIT_CONFIG_GLOBAL"])
        self.assertNotIn("GIT_CONFIG_COUNT", child_environment)
        self.assertNotIn("GIT_CONFIG_KEY_0", child_environment)
        self.assertNotIn("GIT_CONFIG_VALUE_0", child_environment)
        self.assertNotIn("GIT_OBJECT_DIRECTORY", child_environment)
        self.assertNotIn("GIT_CONFIG_SYSTEM", child_environment)
        self.assertNotIn("GIT_SSL_NO_VERIFY", child_environment)
        self.assertNotIn("GIT_CURL_VERBOSE", child_environment)
        self.assertNotIn("GIT_ASKPASS", child_environment)
        self.assertNotIn("GIT_PROXY_COMMAND", child_environment)
        self.assertNotIn("SSH_ASKPASS", child_environment)
        self.assertNotIn("CURL_CA_BUNDLE", child_environment)
        self.assertNotIn("SSL_CERT_FILE", child_environment)
        self.assertNotIn("HTTPS_PROXY", child_environment)
        self.assertNotIn("HTTP_PROXY", child_environment)
        self.assertNotIn("ALL_PROXY", child_environment)

    def test_bounded_process_preserves_only_an_https_proxy_without_credentials(self) -> None:
        spawn_calls: list[dict[str, object]] = []
        real_popen = subprocess.Popen

        def spawn(*arguments, **options):
            spawn_calls.append(options)
            return real_popen(*arguments, **options)

        environment = {
            "PATH": os.environ.get("PATH", ""),
            "HTTPS_PROXY": "https://proxy.example.invalid:8443",
        }
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch("public_upstreams.subprocess.Popen", side_effect=spawn),
        ):
            _run_bounded_command(
                [sys.executable, "-c", "pass"],
                deadline=time.monotonic() + 5,
            )
        child_environment = spawn_calls[0]["env"]
        self.assertEqual(environment["HTTPS_PROXY"], child_environment["HTTPS_PROXY"])
        self.assertEqual(environment["HTTPS_PROXY"], child_environment["https_proxy"])

    def test_run_git_forbids_non_https_protocols_and_redirects(self) -> None:
        completed = subprocess.CompletedProcess([], 0, b"ok", b"")
        with mock.patch("public_upstreams._run_bounded_command", return_value=completed) as run:
            self.assertEqual("ok", run_git(["version"]))
        command = run.call_args.args[0]
        self.assertIn("protocol.allow=never", command)
        self.assertIn("protocol.https.allow=always", command)
        self.assertIn("http.sslVerify=true", command)
        self.assertIn("http.followRedirects=false", command)

    def test_bounded_process_rejects_excessive_stdout_and_stderr(self) -> None:
        for stream in ("stdout", "stderr"):
            script = (
                "import sys,time; "
                f"sys.{stream}.write('x' * 65536); sys.{stream}.flush(); time.sleep(5)"
            )
            with self.subTest(stream=stream), self.assertRaisesRegex(ValueError, "saida do Git"):
                _run_bounded_command(
                    [sys.executable, "-c", script],
                    deadline=time.monotonic() + 5,
                    stdout_limit=1024,
                    stderr_limit=1024,
                )

    def test_bounded_process_enforces_an_absolute_deadline(self) -> None:
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, "prazo total"):
            _run_bounded_command(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                deadline=started + 0.2,
            )
        self.assertLess(time.monotonic() - started, 3)

    def test_bounded_process_terminates_the_child_when_interrupted(self) -> None:
        with (
            mock.patch(
                "public_upstreams._terminate_process",
                wraps=public_upstreams._terminate_process,
            ) as terminate,
            mock.patch("public_upstreams._poll_pause", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaises(KeyboardInterrupt):
                _run_bounded_command(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    deadline=time.monotonic() + 5,
                )
        terminate.assert_called_once()

    def test_bounded_process_stops_when_the_clone_workspace_exceeds_its_quota(self) -> None:
        with tempfile.TemporaryDirectory(prefix="x86qw-git-limit-") as temporary:
            workspace = Path(temporary)
            output = workspace / "pack"
            script = (
                "import pathlib,sys,time; "
                "pathlib.Path(sys.argv[1]).write_bytes(b'x' * 65536); time.sleep(30)"
            )
            with self.assertRaisesRegex(ValueError, "cota temporaria"):
                _run_bounded_command(
                    [sys.executable, "-c", script, str(output)],
                    deadline=time.monotonic() + 5,
                    workspace=workspace,
                    workspace_limit=1024,
                )
            self.assertGreater(os.path.getsize(output), 1024)

    @unittest.skipUnless(os.name == "posix", "grupos POSIX exigem um runner Unix")
    def test_posix_shutdown_kills_a_descendant_that_ignores_sigterm(self) -> None:
        with tempfile.TemporaryDirectory(prefix="x86qw-git-group-") as temporary:
            identity = Path(temporary) / "child.txt"
            child = (
                "import os,pathlib,signal,sys,time;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "pathlib.Path(sys.argv[1]).write_text("
                "f'{os.getpid()} {os.getpgrp()}',encoding='ascii');"
                "time.sleep(30)"
            )
            leader = (
                "import subprocess,sys,time;"
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);"
                "time.sleep(30)"
            )
            with self.assertRaisesRegex(ValueError, "prazo total"):
                _run_bounded_command(
                    [sys.executable, "-c", leader, child, str(identity)],
                    deadline=time.monotonic() + 2,
                )
            self.assertTrue(identity.exists())
            child_pid, process_group = map(
                int,
                identity.read_text(encoding="ascii").split(),
            )
            self.assertEqual("dead", _posix_process_group_status(process_group))
            for _ in range(40):
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("o descendente POSIX não foi coletado após o encerramento")

    @unittest.skipUnless(os.name == "nt", "Job Object exige um runner Windows")
    def test_windows_job_object_kills_the_git_descendant_tree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="x86qw-git-job-") as temporary:
            identity = Path(temporary) / "child.txt"
            child = (
                "import os,pathlib,sys,time;"
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()),encoding='ascii');"
                "time.sleep(30)"
            )
            leader = (
                "import subprocess,sys,time;"
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]);"
                "time.sleep(30)"
            )
            with self.assertRaisesRegex(ValueError, "prazo total"):
                _run_bounded_command(
                    [sys.executable, "-c", leader, child, str(identity)],
                    deadline=time.monotonic() + 2,
                )
            self.assertTrue(identity.exists())
            child_pid = int(identity.read_text(encoding="ascii"))
            with self.assertRaises(OSError):
                os.kill(child_pid, 0)

    @unittest.skipUnless(os.name == "nt", "assinaturas Win32 exigem um runner Windows")
    def test_windows_job_api_signatures_are_explicit(self) -> None:
        kernel32 = _windows_job_kernel32()
        for name in (
            "CreateJobObjectW",
            "SetInformationJobObject",
            "AssignProcessToJobObject",
            "CloseHandle",
        ):
            function = getattr(kernel32, name)
            self.assertIsNotNone(function.argtypes, name)
            self.assertIsNotNone(function.restype, name)
        resume = _windows_ntdll().NtResumeProcess
        self.assertIsNotNone(resume.argtypes)
        self.assertIsNotNone(resume.restype)

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
