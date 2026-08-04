from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINTS = {
    path.stem: path
    for path in (ROOT / "dist/installer/bin").glob("*.py")
    if path.name in {
        "gameplay.py",
        "manager.py",
        "menu.py",
        "python_runtime.py",
        "services.py",
        "session_control.py",
    }
}
def module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


RUNTIME_MODULES = {
    module_name(path): path for path in (ROOT / "x86qw_runtime").rglob("*.py")
}
PRODUCTION_MODULES = {**ENTRYPOINTS, **RUNTIME_MODULES}


def literal_imports(
    path: Path,
    current_module: str | None = None,
    modules: dict[str, Path] | None = None,
) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module)
                available = modules or PRODUCTION_MODULES
                for alias in node.names:
                    candidate = f"{node.module}.{alias.name}"
                    if candidate in available:
                        names.add(candidate)
            elif node.level and current_module is not None:
                package = (
                    current_module
                    if path.name == "__init__.py"
                    else current_module.rpartition(".")[0]
                )
                parts = package.split(".") if package else []
                ascend = node.level - 1
                if ascend > len(parts):
                    continue
                base_parts = parts[:len(parts) - ascend]
                if node.module:
                    base_parts.extend(node.module.split("."))
                base = ".".join(base_parts)
                if base:
                    names.add(base)
                if node.module is None and base:
                    available = modules or PRODUCTION_MODULES
                    for alias in node.names:
                        candidate = f"{base}.{alias.name}"
                        if candidate in available:
                            names.add(candidate)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "importlib"
            and node.func.attr == "import_module"
            and node.args
        ):
            imported = static_string(node.args[0])
            if imported is not None:
                names.add(imported)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            and node.args
        ):
            imported = static_string(node.args[0])
            if imported is not None:
                names.add(imported)
    return names


def hidden_service_locator_targets(path: Path) -> set[str]:
    """Find entrypoint modules recovered indirectly from ``sys.modules``.

    Looking a sibling up in the interpreter registry is still an import edge;
    omitting it from the graph merely hides a cycle from the architecture gate.
    """

    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        if not (
            isinstance(function, ast.Attribute)
            and function.attr in {"get", "__getitem__"}
            and isinstance(function.value, ast.Attribute)
            and isinstance(function.value.value, ast.Name)
            and function.value.value.id == "sys"
            and function.value.attr == "modules"
        ):
            continue
        target = static_string(node.args[0])
        if target is not None:
            names.add(target)
    return names


def static_string(node: ast.AST) -> str | None:
    """Resolve simple constant concatenation used to conceal an import edge."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = static_string(node.left)
        right = static_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def production_target(
    imported: str, modules: dict[str, Path] | None = None,
) -> str | None:
    available = modules or PRODUCTION_MODULES
    candidates = sorted(available, key=len, reverse=True)
    for candidate in candidates:
        if imported == candidate or imported.startswith(candidate + "."):
            return candidate
    return None


def import_graph(
    modules: dict[str, Path] | None = None,
) -> dict[str, set[str]]:
    available = modules or PRODUCTION_MODULES
    graph = {name: set() for name in available}
    for name, path in available.items():
        for imported in literal_imports(path, name, available):
            target = production_target(imported, available)
            if target is not None and target != name:
                graph[name].add(target)
    return graph


def find_cycle(graph: dict[str, set[str]]) -> tuple[str, ...] | None:
    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()

    def visit(node: str) -> tuple[str, ...] | None:
        if node in active_set:
            start = active.index(node)
            return (*active[start:], node)
        if node in visited:
            return None
        visited.add(node)
        active.append(node)
        active_set.add(node)
        for dependency in sorted(graph[node]):
            cycle = visit(dependency)
            if cycle is not None:
                return cycle
        active.pop()
        active_set.remove(node)
        return None

    for node in sorted(graph):
        cycle = visit(node)
        if cycle is not None:
            return cycle
    return None


def qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = qualified_name(node.value)
        if owner is not None:
            return f"{owner}.{node.attr}"
    return None


def entrypoint_boundary_violations(path: Path) -> tuple[str, ...]:
    """Report transport, archive and native policy owned by one CLI entrypoint."""

    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    violations: set[str] = set()
    for imported in literal_imports(path):
        root = imported.split(".", 1)[0]
        if root in {"http", "urllib"}:
            violations.add(f"http:{imported}")
        elif root == "zipfile":
            violations.add(f"zip:{imported}")
        elif root in {"ctypes", "platform"}:
            violations.add(f"native:{imported}")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        name = qualified_name(node)
        if name == "zipfile.ZipFile":
            violations.add("zip:zipfile.ZipFile")
        elif name in {
            "os.access", "os.fchmod", "os.name",
            "subprocess.Popen", "subprocess.run", "sys.platform",
        } or name is not None and name.endswith(".chmod"):
            violations.add(f"native:{name}")
    return tuple(sorted(violations))


class RuntimeArchitectureTests(unittest.TestCase):
    def test_dunder_import_is_visible_to_the_runtime_import_gate(self) -> None:
        """A runtime cannot hide a repository dependency behind __import__."""

        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "runtime_probe.py"
            module.write_text(
                '__import__("maintenance.tools")\n', encoding="utf-8",
            )

            self.assertIn("maintenance.tools", literal_imports(module))

    def test_dunder_import_participates_in_cycle_detection(self) -> None:
        """A dynamic literal import cannot conceal a production cycle."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.py"
            second = root / "second.py"
            first.write_text('__import__("probe.second")\n', encoding="utf-8")
            second.write_text("import probe.first\n", encoding="utf-8")
            modules = {"probe.first": first, "probe.second": second}

            self.assertEqual(
                ("probe.first", "probe.second", "probe.first"),
                find_cycle(import_graph(modules)),
            )

    def test_entrypoint_boundary_detector_reports_http_zip_and_native_policy(self) -> None:
        """The CLI boundary gate must report each forbidden ownership category."""

        with tempfile.TemporaryDirectory() as temporary:
            entrypoint = Path(temporary) / "main.py"
            entrypoint.write_text(
                "import ctypes\n"
                "import os\n"
                "import subprocess\n"
                "import sys\n"
                "import urllib.request\n"
                "import zipfile\n"
                "urllib.request.urlopen('https://example.invalid')\n"
                "zipfile.ZipFile('payload.zip')\n"
                "print(os.name, sys.platform)\n"
                "subprocess.run(['runtime'])\n"
                "path.chmod(0o755)\n",
                encoding="utf-8",
            )
            detector = globals().get(
                "entrypoint_boundary_violations", lambda _path: (),
            )

            self.assertEqual(
                {
                    "http:urllib.request",
                    "native:ctypes",
                    "native:os.name",
                    "native:path.chmod",
                    "native:subprocess.run",
                    "native:sys.platform",
                    "zip:zipfile",
                    "zip:zipfile.ZipFile",
                },
                set(detector(entrypoint)),
            )

    def test_service_entrypoint_uses_only_public_managed_file_operations(self) -> None:
        """Platform materialization must not leak private runtime helpers."""

        services = ENTRYPOINTS["services"]
        tree = ast.parse(services.read_text("utf-8"), filename=str(services))
        private_imports = sorted(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "x86qw_runtime.io.managed_files"
            for alias in node.names
            if alias.name.startswith("_")
        )
        self.assertEqual([], private_imports)

    def test_installed_entrypoints_import_only_public_runtime_symbols(self) -> None:
        """A CLI facade must not couple itself to private runtime implementation."""

        violations: list[str] = []
        for entrypoint, path in sorted(ENTRYPOINTS.items()):
            tree = ast.parse(path.read_text("utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if not node.module or not node.module.startswith("x86qw_runtime"):
                    continue
                for alias in node.names:
                    if alias.name.startswith("_"):
                        violations.append(
                            f"{entrypoint}: {node.module}.{alias.name}"
                        )

        self.assertEqual([], violations)

    def test_manager_does_not_publish_personal_files_with_a_local_helper(self) -> None:
        """Personal-file replacement belongs to the reversible runtime boundary."""

        manager = ast.parse(
            ENTRYPOINTS["manager"].read_text("utf-8"),
            filename=str(ENTRYPOINTS["manager"]),
        )
        local_publishers = {
            node.name
            for node in ast.walk(manager)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "write_personal_config"
        }

        self.assertEqual(set(), local_publishers)

    def test_relative_import_cycle_is_detected(self) -> None:
        """Package-relative imports must not be invisible to the cycle gate."""

        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "probe"
            package.mkdir()
            initializer = package / "__init__.py"
            child = package / "child.py"
            initializer.write_text("from . import child\n", encoding="utf-8")
            child.write_text("import probe\n", encoding="utf-8")
            modules = {"probe": initializer, "probe.child": child}
            with mock.patch.dict(PRODUCTION_MODULES, modules, clear=True):
                cycle = find_cycle(import_graph())

        self.assertEqual(("probe", "probe.child", "probe"), cycle)

    def test_absolute_from_import_cycle_is_detected(self) -> None:
        """An absolute ``from package import module`` must add the module edge."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.py"
            second = root / "second.py"
            first.write_text("from probe import second\n", encoding="utf-8")
            second.write_text("import probe.first\n", encoding="utf-8")
            modules = {"probe.first": first, "probe.second": second}

            self.assertEqual(
                ("probe.first", "probe.second", "probe.first"),
                find_cycle(import_graph(modules)),
            )

    def test_runtime_never_imports_repository_or_entrypoint_layers(self) -> None:
        """Runtime modules must remain usable without maintenance or CLI facades."""

        forbidden = {
            "maintenance",
            "dist",
            "manager",
            "gameplay",
            "services",
            "session_control",
            "menu",
            "python_runtime",
        }
        violations: list[str] = []
        for name, path in sorted(RUNTIME_MODULES.items()):
            for imported in sorted(literal_imports(path, name, PRODUCTION_MODULES)):
                if imported.split(".", 1)[0] in forbidden:
                    violations.append(f"{name} -> {imported}")
        self.assertEqual([], violations)

    def test_installed_production_import_graph_has_no_cycle(self) -> None:
        """Late imports must not conceal circular ownership between entrypoints."""

        cycle = find_cycle(import_graph())
        self.assertIsNone(cycle, " -> ".join(cycle or ()))

    def test_entrypoints_do_not_recover_siblings_from_sys_modules(self) -> None:
        """Composition is explicit instead of depending on import order."""

        violations = {
            path.name: sorted(hidden_service_locator_targets(path))
            for path in ENTRYPOINTS.values()
            if hidden_service_locator_targets(path)
        }
        self.assertEqual({}, violations)

    def test_installed_entrypoints_do_not_import_maintenance(self) -> None:
        """Repository tooling is supplied by a development composition root."""

        violations = {
            path.name: sorted(
                imported for imported in literal_imports(path)
                if imported == "maintenance" or imported.startswith("maintenance.")
            )
            for path in ENTRYPOINTS.values()
        }
        self.assertEqual({}, {name: values for name, values in violations.items() if values})

    def test_maintenance_recovery_does_not_load_service_entrypoint(self) -> None:
        """Mutating manager actions use the runtime journal boundary directly."""

        manager = ast.parse(
            ENTRYPOINTS["manager"].read_text("utf-8"),
            filename=str(ENTRYPOINTS["manager"]),
        )
        execute = next(
            node for node in manager.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "execute_manager_action"
        )
        calls = {
            node.func.id
            for node in ast.walk(execute)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("load_services_module", calls)

    def test_manager_does_not_own_http_transport_or_policy(self) -> None:
        """The CLI composition root must consume the canonical remote client."""

        manager_path = ENTRYPOINTS["manager"]
        tree = ast.parse(
            manager_path.read_text("utf-8"), filename=str(manager_path),
        )
        imports = literal_imports(
            manager_path, "manager", PRODUCTION_MODULES,
        )
        self.assertFalse(
            any(name == "urllib" or name.startswith("urllib.") for name in imports),
            sorted(imports),
        )
        definitions = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue({"http_get", "http_get_mirrors"}.isdisjoint(definitions))
        forbidden_constructors = {
            "BoundedMetadata", "PinnedArtifact", "RetryPolicy",
            "bounded_download", "bounded_download_mirrors",
        }
        called = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(forbidden_constructors.isdisjoint(called), sorted(called))

    def test_manager_owns_no_http_zip_or_native_platform_policy(self) -> None:
        """The installed CLI entrypoint is composition, not an adapter implementation."""

        self.assertEqual(
            (), entrypoint_boundary_violations(ENTRYPOINTS["manager"]),
        )

    def test_all_installed_entrypoints_own_no_native_process_policy(self) -> None:
        """Process implementation belongs to runtime supervisors, never CLI facades."""

        violations = {
            name: entrypoint_boundary_violations(path)
            for name, path in ENTRYPOINTS.items()
            if entrypoint_boundary_violations(path)
        }
        self.assertEqual({}, violations)

    def test_manager_does_not_import_the_stdlib_platform_module(self) -> None:
        """Native host facts must come from the canonical runtime adapter."""

        imports = literal_imports(ENTRYPOINTS["manager"])
        self.assertNotIn("platform", {name.split(".", 1)[0] for name in imports})


if __name__ == "__main__":
    unittest.main()
