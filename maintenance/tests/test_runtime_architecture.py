from __future__ import annotations

import ast
import unittest
from pathlib import Path


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
RUNTIME_MODULES = {
    ".".join(path.relative_to(ROOT).with_suffix("").parts): path
    for path in (ROOT / "x86qw_runtime").rglob("*.py")
}
PRODUCTION_MODULES = {**ENTRYPOINTS, **RUNTIME_MODULES}


def literal_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
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


def production_target(imported: str) -> str | None:
    candidates = sorted(PRODUCTION_MODULES, key=len, reverse=True)
    for candidate in candidates:
        if imported == candidate or imported.startswith(candidate + "."):
            return candidate
    return None


def import_graph() -> dict[str, set[str]]:
    graph = {name: set() for name in PRODUCTION_MODULES}
    for name, path in PRODUCTION_MODULES.items():
        for imported in literal_imports(path):
            target = production_target(imported)
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


class RuntimeArchitectureTests(unittest.TestCase):
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
            for imported in sorted(literal_imports(path)):
                if imported.split(".", 1)[0] in forbidden:
                    violations.append(f"{name} -> {imported}")
        self.assertEqual([], violations)

    def test_installed_production_import_graph_has_no_cycle(self) -> None:
        """Late imports must not conceal circular ownership between entrypoints."""

        cycle = find_cycle(import_graph())
        self.assertIsNone(cycle, " -> ".join(cycle or ()))


if __name__ == "__main__":
    unittest.main()
