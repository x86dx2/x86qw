from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WRITE_ONLY_ZIP_MODULES = frozenset({
    "maintenance/tools/build_component_packages.py",
    "maintenance/tools/build_core_package.py",
    "maintenance/tools/build_installer_bundle.py",
    "maintenance/tools/component_sources.py",
})


def zip_boundary_violations(path: str, source: str) -> list[str]:
    """Reject ZIP readers outside the canonical runtime boundary."""

    tree = ast.parse(source, filename=path)
    violations: list[str] = []
    zipfile_aliases: set[str] = set()
    write_handles: list[tuple[str, int, int]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "zipfile":
                    zipfile_aliases.add(alias.asname or alias.name)
                    if path not in WRITE_ONLY_ZIP_MODULES:
                        violations.append(f"{path}:{node.lineno}:zipfile import")
        elif isinstance(node, ast.ImportFrom) and node.module == "zipfile":
            violations.append(f"{path}:{node.lineno}:zipfile symbol import")

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, ast.Name) and value.id in zipfile_aliases:
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in zipfile_aliases:
                        zipfile_aliases.add(target.id)
                        changed = True
            if (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id in zipfile_aliases
                and value.attr == "ZipFile"
            ):
                violations.append(f"{path}:{node.lineno}:ZipFile alias")

    forbidden_methods = {"extract", "extractall", "is_zipfile", "testzip"}
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                context = item.context_expr
                if not isinstance(context, ast.Call):
                    continue
                function = context.func
                if not (
                    isinstance(function, ast.Attribute)
                    and isinstance(function.value, ast.Name)
                    and function.value.id in zipfile_aliases
                    and function.attr == "ZipFile"
                ):
                    continue
                if isinstance(item.optional_vars, ast.Name):
                    write_handles.append((
                        item.optional_vars.id,
                        node.lineno,
                        node.end_lineno or node.lineno,
                    ))
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        call_name = (
            function.attr if isinstance(function, ast.Attribute)
            else function.id if isinstance(function, ast.Name)
            else ""
        )
        if call_name in forbidden_methods:
            violations.append(f"{path}:{node.lineno}:{call_name}")
        if (
            isinstance(function, ast.Name)
            and function.id == "__import__"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "zipfile"
        ):
            violations.append(f"{path}:{node.lineno}:dynamic zipfile import")
        if (
            call_name == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "zipfile"
        ):
            violations.append(f"{path}:{node.lineno}:dynamic zipfile import")
        if (
            isinstance(function, ast.Name)
            and function.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in zipfile_aliases
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in {"ZipFile", *forbidden_methods}
        ):
            violations.append(f"{path}:{node.lineno}:dynamic zipfile access")
        if (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and any(
                function.value.id == name and start <= node.lineno <= end
                for name, start, end in write_handles
            )
            and function.attr not in {"write", "writestr"}
        ):
            violations.append(f"{path}:{node.lineno}:write-only handle uses {function.attr}")
        if not (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id in zipfile_aliases
            and function.attr == "ZipFile"
        ):
            continue
        mode_node = node.args[1] if len(node.args) > 1 else next(
            (keyword.value for keyword in node.keywords if keyword.arg == "mode"),
            None,
        )
        mode = mode_node.value if isinstance(mode_node, ast.Constant) else None
        if path not in WRITE_ONLY_ZIP_MODULES or mode != "w":
            violations.append(f"{path}:{node.lineno}:ZipFile is not explicit write-only")
    return violations


class ContinuousIntegrationTests(unittest.TestCase):
    def test_zip_and_pk3_reads_have_one_canonical_boundary(self):
        production = [
            *sorted((ROOT / "dist/installer/bin").glob("*.py")),
            *sorted((ROOT / "maintenance/tools").glob("*.py")),
        ]
        violations: list[str] = []
        for path in production:
            violations.extend(zip_boundary_violations(path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")))
        self.assertEqual([], violations)
        for path in (
            ROOT / "dist/installer/bin/install.sh",
            ROOT / "site/public/install.sh",
            ROOT / "dist/installer/bin/install.ps1",
            ROOT / "site/public/install.ps1",
        ):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("unzip", source.casefold(), path)
            self.assertNotIn("Expand-Archive", source, path)

    def test_zip_boundary_gate_rejects_aliases_and_dynamic_access(self):
        fixtures = (
            "import zipfile as z\nwith z.ZipFile('x', 'r') as value:\n    pass\n",
            "from zipfile import ZipFile\nwith ZipFile('x', 'w') as value:\n    pass\n",
            "import zipfile\nfactory = zipfile.ZipFile\n",
            "import zipfile\nfactory = getattr(zipfile, 'ZipFile')\n",
            "module = __import__('zipfile')\n",
        )
        for source in fixtures:
            with self.subTest(source=source):
                self.assertTrue(zip_boundary_violations("fixture.py", source))

    def test_local_validation_documentation_replaces_external_ci(self):
        runbook = (ROOT / "docs/runbooks/release.md").read_text(encoding="utf-8")
        contributing = (ROOT / ".github/CONTRIBUTING.md").read_text(encoding="utf-8")
        pull_request = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
        stabilization = (ROOT / "docs/implementation/stabilization-1.0.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs/ROADMAP-QUAKE-ECOSYSTEM.md").read_text(encoding="utf-8")
        draft = (ROOT / "docs/releases/1.0.0-draft.md").read_text(encoding="utf-8")
        self.assertIn("./maintenance/manage.py verify", runbook)
        self.assertIn("release_candidate.py prepare", runbook)
        self.assertIn("git show -s --format=%cI", runbook)
        self.assertIn("--generated-at", runbook)
        self.assertIn("release_candidate.py verify", runbook)
        self.assertIn("release_candidate.py promote", runbook)
        self.assertIn("mktemp", runbook)
        self.assertIn("Mac", contributing)
        self.assertIn("Mac", pull_request)
        self.assertIn("gates externos", pull_request.casefold())
        self.assertIn("fail-closed", pull_request.casefold())
        self.assertNotIn("Smoke nativo executado", pull_request)
        self.assertNotIn("sete checks", stabilization)
        self.assertNotIn("os workflows de candidato rejeitam prereleases", stabilization)
        self.assertNotIn("publicação da `1.0.0` sem os gates nativos", roadmap)
        self.assertIn("sem gates nativos", roadmap)
        self.assertIn("baseline-fonte no Git", draft)
        self.assertIn("smoke nativo M3", draft)
        self.assertNotIn("versão pública continua sendo `0.7.3`", draft)
        self.assertNotIn("sem smokes nativos ou runners externos", draft)

    def test_committed_diff_gate_uses_event_shas_and_rejects_committed_whitespace(self):
        script = ROOT / "maintenance/tools/check_committed_diff.py"
        source = script.read_text(encoding="utf-8")
        self.assertIn('event_name == "pull_request"', source)
        self.assertIn('event_name == "push"', source)
        self.assertIn('"git", "diff", "--check"', source)
        self.assertIn('"git", "show", "--check"', source)
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            subprocess.run(["git", "init", "-q", repository], check=True)
            subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.name", "CI fixture"], cwd=repository, check=True)
            fixture = repository / "fixture.txt"
            fixture.write_text("válido\n", encoding="utf-8")
            subprocess.run(["git", "add", "fixture.txt"], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
            fixture.write_text("espaço inválido   \n", encoding="utf-8")
            subprocess.run(["git", "add", "fixture.txt"], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-qm", "bad whitespace"], cwd=repository, check=True)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
            event = repository / "event.json"
            event.write_text(json.dumps({"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script), "--event-name", "pull_request", "--event-file", str(event)],
                cwd=repository, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("trailing whitespace", result.stdout + result.stderr)

    def test_release_candidate_path_has_real_transport_and_publish_gates(self):
        candidate = (ROOT / "maintenance/tools/release_candidate.py").read_text(encoding="utf-8")
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertNotIn("verify_external_handoff", candidate)
        self.assertIn("build-once", release)
        self.assertIn("portable-verify", release)
        self.assertIn("native-m3", release)
        self.assertIn("approval-preview", release)
        self.assertIn("environment: release", release)
        self.assertIn("publish-assets", release)
        self.assertIn("publish-gitlab", release)
        self.assertIn("verify-mirrors", release)
        self.assertIn("metadata-last", release)
        self.assertIn("verify_public_tuf.py", release)
        self.assertIn("verify_public_bootstraps.py", release)
        self.assertIn("--site-source candidate/site/public", release)
        self.assertNotIn("--bootstrap-source candidate/site/public", release)
        self.assertIn("release-blockers", release)
        self.assertIn("check_release_blockers.py", release)
        self.assertIn("release_evidence_binding.py", release)
        self.assertIn(".github/workflows/native-m3.yml", release)
        self.assertIn("issues: read", release)
        self.assertNotIn("needs: [release-blockers, build-once, native-m3]", release)
        self.assertIn("retention-days: 90", release)
        self.assertNotIn("Reserved mirror gate", release)
        self.assertNotIn("No metadata publisher", release)

    def test_required_portable_contract_covers_macos_and_linux(self):
        workflow = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        self.assertIn('- os: macos-latest\n            python: "3.10"', workflow)
        self.assertIn('- os: macos-latest\n            python: "3.13"', workflow)
        self.assertIn('- os: ubuntu-latest\n            python: "3.10"', workflow)
        self.assertIn('- os: ubuntu-latest\n            python: "3.13"', workflow)
        self.assertIn("preview-other-os:", workflow)
        self.assertIn("run_preview:", workflow)
        self.assertIn("if: inputs.run_preview == true", workflow)
        self.assertIn("Windows preview contract", workflow)
        self.assertIn("windows_preview_excluded", workflow)
        self.assertNotIn("continue-on-error", workflow)
        portable_block = workflow.split("preview-other-os:")[0]
        self.assertIn("- os: ubuntu-latest", portable_block)
        self.assertNotIn("- os: windows-latest", portable_block)

    def test_portable_contract_seeds_one_shared_lfs_cache_before_other_matrix_jobs(self):
        workflow = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        self.assertIn("lfs-seed:", workflow)
        self.assertIn("needs: lfs-seed", workflow)
        self.assertNotIn("seed_lfs:", workflow)
        self.assertIn("matrix:\n        include:", workflow)
        self.assertIn(
            "actions/cache@cdf6c1fa76f9f475f3d7449005a359c84ca0f306",
            workflow,
        )
        self.assertIn("path: .git/lfs/objects", workflow)
        self.assertIn(
            "key: x86qw-lfs-v3-${{ github.event.pull_request.head.sha || github.sha }}",
            workflow,
        )
        self.assertIn("enableCrossOsArchive: true", workflow)
        self.assertIn("cache-hit", workflow)
        self.assertIn("git lfs checkout", workflow)
        self.assertIn("materialize_lfs.py", workflow)
        self.assertIn("git lfs fsck --pointers", workflow)
        self.assertEqual(0, workflow.count("git lfs pull"))

    def test_workflows_use_node24_actions_and_codeql(self):
        workflow_dir = ROOT / ".github/workflows"
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(workflow_dir.glob("*.yml"))
        )
        for legacy in (
            "actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830",
            "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        ):
            self.assertNotIn(legacy, workflows)
        for current in (
            "actions/cache@cdf6c1fa76f9f475f3d7449005a359c84ca0f306",
            "actions/download-artifact@37930b1c2abaa49bbe596cd826c3c89aef350131",
            "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f",
        ):
            self.assertIn(current, workflows)

        codeql = workflow_dir / "codeql.yml"
        self.assertTrue(codeql.is_file())
        source = codeql.read_text(encoding="utf-8")
        self.assertIn("security-events: write", source)
        self.assertIn("javascript-typescript", source)
        self.assertIn("python", source)
        self.assertEqual(
            2,
            source.count(
                "github/codeql-action/"
            ),
        )
        self.assertEqual(
            2,
            source.count("@bb16b9baa2ec4010b29f5c606d57d01190139edd"),
        )
        self.assertNotIn("pull_request_target", source)

    def test_production_site_deploys_only_explicit_assembled_assets(self):
        workflow_names = (
            "release.yml",
            "site-projection-repair.yml",
            "tuf-snapshot-publish.yml",
            "tuf-timestamp-publish.yml",
        )
        for name in workflow_names:
            source = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            deploy_lines = [
                line for line in source.splitlines()
                if "wrangler deploy" in line
            ]
            self.assertEqual(2, len(deploy_lines), name)
            self.assertEqual(2, source.count("--assets"), name)
            if name in {"release.yml", "site-projection-repair.yml"}:
                self.assertIn("--assets ../release-work/site/public", source)
                self.assertIn("--assets release-work/site/public", source)
            else:
                self.assertIn("--assets ../release-work/public", source)
                self.assertIn("--assets release-work/public", source)
            self.assertEqual(1, source.count("--strict"), name)

    def test_lfs_materializer_is_a_bounded_content_addressed_ci_boundary(self):
        materializer = ROOT / "maintenance/tools/materialize_lfs.py"
        self.assertTrue(materializer.is_file())
        source = materializer.read_text(encoding="utf-8")
        self.assertIn("media.githubusercontent.com/media", source)
        self.assertIn("PinnedArtifact", source)
        self.assertIn("expected_sha256", source)
        self.assertIn("expected_size", source)

    def test_release_catalog_timestamp_is_bound_to_the_candidate_commit(self):
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn('git show -s --format=%cI "$CANDIDATE_COMMIT"', release)
        self.assertIn("astimezone(timezone.utc)", release)
        self.assertIn('replace("+00:00", "Z")', release)
        self.assertEqual(
            2,
            release.count("--generated-at \"$CANDIDATE_GENERATED_AT\""),
            "catalog and candidate manifest must share the commit-bound timestamp",
        )

    def test_approval_preview_passes_each_evidence_root_once(self):
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        preview = release.split("  approval-preview:\n", 1)[1].split("\n  approval:\n", 1)[0]
        self.assertEqual(
            1,
            preview.count("--artifact-root m3-evidence"),
            "approval verification must not duplicate the evidence root argument",
        )

    def test_external_workflows_are_explicit_and_pinned(self):
        workflow_dir = ROOT / ".github/workflows"
        workflow_files = sorted(
            path for path in workflow_dir.glob("*")
            if path.suffix in {".yml", ".yaml"}
        )
        self.assertEqual(
            {
                "codeql.yml", "native-m3.yml", "patch-mirror.yml", "public-acceptance.yml", "release.yml", "sign-native-evidence.yml",
                "rc-soak.yml", "site-projection-repair.yml", "tuf-metadata-handoff.yml", "tuf-monitor.yml", "tuf-operation-drill.yml", "tuf-snapshot-publish.yml", "tuf-snapshot-renewal.yml", "tuf-timestamp-publish.yml", "tuf-timestamp-renewal.yml", "validate.yml",
            },
            {path.name for path in workflow_files},
        )
        for path in workflow_files:
            source = path.read_text(encoding="utf-8")
            self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", source)
            if path.name == "native-m3.yml":
                self.assertIn("Prepare isolated Python on self-hosted M3", source)
                self.assertIn('python3_bin="$(command -v python3)"', source)
            elif path.name == "public-acceptance.yml":
                self.assertIn("Prepare isolated Python on self-hosted M3", source)
                self.assertIn('python3 -m venv "$RUNNER_TEMP/x86qw-public-acceptance-python"', source)
            elif path.name == "codeql.yml":
                self.assertIn("github/codeql-action/init@", source)
                self.assertIn("github/codeql-action/analyze@", source)
            else:
                self.assertIn("actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1", source)
        monitor = (workflow_dir / "tuf-monitor.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "17 * * * *"', monitor)
        self.assertIn("monitor_public_tuf.py", monitor)
        self.assertIn("--warning-hours 72", monitor)
        self.assertNotIn("--warning-hours 6", monitor)

    def test_tuf_monitor_persists_one_actionable_alert_on_failure(self):
        monitor = (ROOT / ".github/workflows/tuf-monitor.yml").read_text(encoding="utf-8")
        self.assertIn("issues: write", monitor)
        self.assertIn("actions: read", monitor)
        self.assertIn("if: failure()", monitor)
        self.assertIn("actions/github-script@ed597411d8f924073f98dfc5c65a23a2325f34cd", monitor)
        self.assertIn("x86qw-tuf-lease-alert", monitor)
        self.assertIn("issues.listForRepo", monitor)
        self.assertIn("issues.create", monitor)
        self.assertIn("issues.update", monitor)
        self.assertIn("const labels = ['P0', 'owner-only']", monitor)
        self.assertIn("labels: 'P0'", monitor)
        self.assertIn("listWorkflowRuns", monitor)
        self.assertIn("monitor gap", monitor)
        self.assertIn(
            "if: success() && github.event_name == 'schedule'",
            monitor,
        )

    def test_public_projection_and_timestamp_recovery_verify_all_mirrors_first(self):
        projection = (ROOT / ".github/workflows/site-projection-repair.yml").read_text(
            encoding="utf-8"
        )
        timestamp = (ROOT / ".github/workflows/tuf-timestamp-publish.yml").read_text(
            encoding="utf-8"
        )
        snapshot = (ROOT / ".github/workflows/tuf-snapshot-publish.yml").read_text(
            encoding="utf-8"
        )
        for name, source in (
            ("projection", projection),
            ("timestamp", timestamp),
            ("snapshot", snapshot),
        ):
            with self.subTest(workflow=name):
                self.assertIn("maintenance/tools/verify_release_mirrors.py", source)
                self.assertIn("--expected-release", source)
        self.assertIn("maintenance/tools/project_release_truth.py", projection)
        self.assertIn('"product_version": candidate_version', Path(
            ROOT / "maintenance/tools/project_release_truth.py"
        ).read_text(encoding="utf-8"))

    def test_site_projection_uses_current_root_for_audience_and_domain(self):
        projection = (ROOT / ".github/workflows/site-projection-repair.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("maintenance/tools/render_release_site.py", projection)
        self.assertIn("--source site/public", projection)
        self.assertIn("--catalog candidate/catalog.json", projection)
        self.assertIn("--product candidate/product.json", projection)
        self.assertIn("--bootstrap-source dist/installer/bin", projection)
        self.assertIn(
            'shutil.copytree(Path("release-work/current-site"), source_projection)',
            projection,
        )
        self.assertNotIn(
            'shutil.copytree(Path("candidate/site/public"), source_projection)',
            projection,
        )
        self.assertNotIn("source_projection / \"assets/site.css\"", projection)
        self.assertIn("maintenance/tools/build_deploy_provenance.py", projection)
        self.assertIn("--directory release-work/site-source", projection)
        self.assertIn("--bootstrap-dir release-work/current-site", projection)
        self.assertIn("verify_site_root_probe.py", projection)

    def test_migration_plan_names_the_complete_public_0_7_fixture_range(self):
        roadmap = (ROOT / "docs/ROADMAP.md").read_text(encoding="utf-8")
        plan = (ROOT / "docs/implementation/stabilization-1.0-plan.md").read_text(encoding="utf-8")
        self.assertIn("0.7.0–0.7.13", roadmap)
        self.assertIn("0.7.0–0.7.13", plan)
        self.assertNotIn("0.7.0–0.7.3` e `0.7.13", roadmap)
        self.assertNotIn("0.7.0–0.7.3` e `0.7.13", plan)

    def test_native_platform_contracts_have_an_explicit_m3_executor(self):
        contract = (ROOT / "x86qw_runtime/contracts/native_evidence.py").read_text(encoding="utf-8")
        for platform in ("Linux-X64", "Windows-X64", "macOS-ARM64", "macOS-X64"):
            self.assertIn(platform, contract)
        self.assertIn("REQUIRED_NATIVE_PLATFORMS", contract)
        self.assertTrue((ROOT / "maintenance/tools/native_release_smoke.py").is_file())
        self.assertTrue((ROOT / "maintenance/tools/native_release_evidence.py").is_file())
        self.assertTrue((ROOT / "maintenance/tools/release_evidence_binding.py").is_file())
        self.assertTrue((ROOT / "maintenance/tools/assemble_release_evidence.py").is_file())
        self.assertTrue((ROOT / "maintenance/tools/native_m3_harness.py").is_file())
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        native_workflow = (ROOT / ".github/workflows/native-m3.yml").read_text(encoding="utf-8")
        self.assertIn("self-hosted, macOS, arm64, M3", native_workflow)
        self.assertIn("native-m3.yml", release)
        self.assertIn('REQUIRED_NATIVE_PLATFORMS = frozenset({"macOS-ARM64"})', contract)

    def test_native_m3_workflow_does_not_require_runner_inventory_api_access(self):
        native_workflow = (ROOT / ".github/workflows/native-m3.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: [self-hosted, macOS, arm64, M3]", native_workflow)
        self.assertNotIn("/actions/runners?per_page=100", native_workflow)
        self.assertNotIn("GITHUB_API_URL", native_workflow)

    def test_site_uses_lockfile(self):
        self.assertTrue((ROOT / "site/package.json").is_file())
        self.assertTrue((ROOT / "site/package-lock.json").is_file())

    def test_local_governance_contract_is_complete(self):
        codeowners = (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8")
        explicit_rules = {
            "/x86qw_runtime/",
            "/maintenance/inventory/",
            "/maintenance/tools/",
            "/site/",
        }
        rules: dict[str, list[str]] = {}
        for raw_line in codeowners.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            fields = line.split()
            rules[fields[0]] = fields[1:]
        for pattern in explicit_rules:
            with self.subTest(pattern=pattern):
                self.assertTrue(rules.get(pattern), f"missing CODEOWNERS rule: {pattern}")
                self.assertTrue(any(owner.startswith("@") for owner in rules[pattern]))

        dependabot = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
        self.assertRegex(dependabot, r"(?m)^version:\s*2\s*$")
        self.assertNotIn("github-actions", dependabot)
        self.assertIn("package-ecosystem: npm", dependabot)
        self.assertIn('directory: "/site"', dependabot)
        self.assertRegex(dependabot, r"(?m)^\s+interval:\s*weekly\s*$")

        evidence_runbook = (ROOT / "docs/runbooks/native-evidence.md").read_text(encoding="utf-8")
        release_runbook = (ROOT / "docs/runbooks/release.md").read_text(encoding="utf-8")
        self.assertIn("Mac", evidence_runbook)
        self.assertIn("Mac", release_runbook)
        self.assertNotIn("release-metadata", release_runbook)
        self.assertNotIn("release-approval", release_runbook)

    def test_large_runtime_and_demo_payloads_are_lfs_managed(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("dist/**/*.mvd filter=lfs", attributes)
        self.assertIn("dist/servers/**/x86qw/runtime/** filter=lfs", attributes)
        self.assertIn("dist/services/**/x86qw/runtime/** filter=lfs", attributes)


if __name__ == "__main__":
    unittest.main()
