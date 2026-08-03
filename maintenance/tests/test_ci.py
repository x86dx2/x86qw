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
    """Reject ZIP readers outside the canonical runtime boundary.

    The small allowlist contains deterministic writers only.  Imports through
    aliases and dynamic import/getattr indirections are inspected explicitly so
    a future reader cannot bypass this gate by merely renaming ``ZipFile``.
    """
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

    # Follow harmless module aliases (``archive_zip = zipfile``), but reject
    # aliases of ZipFile itself because they obscure the write-only contract.
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
            source = path.read_text(encoding="utf-8")
            violations.extend(zip_boundary_violations(path.relative_to(ROOT).as_posix(), source))
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

    def test_pull_request_workflow_is_read_only_and_multiplatform(self):
        workflow = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("macos-latest", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn('python: ["3.10", "3.13"]', workflow)
        self.assertIn("git lfs pull", workflow)
        self.assertIn("git lfs fsck", workflow)
        self.assertIn("./maintenance/manage.py verify", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("maintenance/tools/check_committed_diff.py", workflow)
        self.assertIn("wrangler@4.114.0 deploy --dry-run", workflow)
        self.assertNotIn("secrets.", workflow)

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
            event.write_text(json.dumps({
                "pull_request": {"base": {"sha": base}, "head": {"sha": head}},
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script), "--event-name", "pull_request", "--event-file", str(event)],
                cwd=repository, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("trailing whitespace", result.stdout + result.stderr)

    def test_publication_is_manual_protected_and_depends_on_validation(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("needs: validate", workflow)
        self.assertIn("environment: release", workflow)
        self.assertIn("git diff --exit-code", workflow)
        self.assertIn("./maintenance/manage.py publish --dry-run", workflow)
        self.assertIn("./maintenance/manage.py publish", workflow)
        self.assertIn("GLAB_TOKEN: ${{ secrets.GITLAB_TOKEN }}", workflow)
        self.assertIn("GLAB_TOKEN=\"${GLAB_TOKEN//$'\\r'/}\"", workflow)
        self.assertIn("GLAB_TOKEN=\"${GLAB_TOKEN//$'\\n'/}\"", workflow)
        self.assertIn('export GITLAB_TOKEN="${GLAB_TOKEN}"', workflow)
        self.assertNotIn("pull_request:", workflow)

    def test_large_runtime_and_demo_payloads_are_lfs_managed(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("dist/**/*.mvd filter=lfs", attributes)
        self.assertIn("dist/servers/**/x86qw/runtime/** filter=lfs", attributes)
        self.assertIn("dist/services/**/x86qw/runtime/** filter=lfs", attributes)


if __name__ == "__main__":
    unittest.main()
