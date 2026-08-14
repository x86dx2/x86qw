"""Closed schema for protected native release-smoke handoffs.

The release workflow does not pretend that a portable JSON check is a native
runtime smoke.  A protected external run must provide this exact, bounded
record; the local promotion tools only normalize and compare it.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from pathlib import PurePosixPath


NATIVE_EVIDENCE_FORMAT = 2
KNOWN_NATIVE_PLATFORMS = frozenset({
    "Linux-X64",
    "Windows-X64",
    "macOS-ARM64",
    "macOS-X64",
})
# This is the release gate's native set for the current scope.  Apple Silicon
# M3 is the only platform with mandatory native evidence in this cycle;
# Linux-X64, Windows-X64 and macOS-X64 remain preview/portable targets and
# must not be silently promoted by a schema that the workflow cannot execute.
REQUIRED_NATIVE_PLATFORMS = frozenset({"macOS-ARM64"})
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

CANONICAL_CASES = (
    "install-clean-space-unicode",
    "install-existing-space-unicode",
    "migration-0.7.13-real",
    "client-stable-window-map-exit",
    "client-nightly-window-map-exit",
    "game-ktx",
    "game-ktx-frogbot",
    "game-final-arena",
    "game-pro-x",
    "game-team-fortress",
    "game-td2",
    "mvdsv-mvd",
    "qtv-stream",
    "qwfwd-forward",
    "lifecycle-update",
    "lifecycle-update-apply",
    "lifecycle-upgrade",
    "lifecycle-upgrade-apply",
    "lifecycle-verify",
    "lifecycle-repair",
    "lifecycle-repair-corruption",
    "lifecycle-migrate-apply",
    "lifecycle-cleanup",
    "lifecycle-uninstall",
    "lifecycle-purge",
)

CASE_ASSERTIONS = {
    "install-clean-space-unicode": frozenset({
        "installed", "path-space-unicode", "launcher-help", "launcher-version",
        "launcher-changes", "launcher-migrate",
    }),
    "install-existing-space-unicode": frozenset({"existing-install", "path-space-unicode"}),
    "migration-0.7.13-real": frozenset({
        "legacy-0.7.13", "migration-applied", "personal-preserved",
        "pak-preserved", "process-exited",
    }),
    "client-stable-window-map-exit": frozenset({"window-created", "map-loaded", "process-exited"}),
    "client-nightly-window-map-exit": frozenset({"window-created", "map-loaded", "process-exited"}),
    "game-ktx": frozenset({"gamecode-loaded", "map-loaded", "process-exited"}),
    "game-ktx-frogbot": frozenset({
        "gamecode-loaded", "map-loaded", "frogbot-spawned", "frogbot-skill",
        "frogbot-named", "process-exited",
    }),
    "game-final-arena": frozenset({"gamecode-loaded", "map-loaded", "process-exited"}),
    "game-pro-x": frozenset({"gamecode-loaded", "map-loaded", "process-exited"}),
    "game-team-fortress": frozenset({"gamecode-loaded", "map-loaded", "process-exited"}),
    "game-td2": frozenset({"gamecode-loaded", "map-loaded", "process-exited"}),
    "mvdsv-mvd": frozenset({"server-ready", "mvd-valid", "process-exited"}),
    "qtv-stream": frozenset({"http-ready", "stream-readable", "process-exited"}),
    "qwfwd-forward": frozenset({"udp-forwarded", "process-exited"}),
    "lifecycle-update": frozenset({"state-converged", "no-downgrade", "process-exited"}),
    "lifecycle-update-apply": frozenset({
        "state-converged", "no-downgrade", "mutation-applied", "process-exited",
    }),
    "lifecycle-upgrade": frozenset({"state-converged", "profile-preserved", "process-exited"}),
    "lifecycle-upgrade-apply": frozenset({
        "state-converged", "profile-preserved", "mutation-applied", "process-exited",
    }),
    "lifecycle-verify": frozenset({"integrity-verified", "no-mutation"}),
    "lifecycle-repair": frozenset({"repair-planned", "personal-preserved", "process-exited"}),
    "lifecycle-repair-corruption": frozenset({
        "repair-applied", "corruption-restored", "personal-preserved", "process-exited",
    }),
    "lifecycle-migrate-apply": frozenset({
        "migration-applied", "state-converged", "process-exited",
    }),
    "lifecycle-cleanup": frozenset({"no-residual-processes", "no-residual-ports", "no-residual-temporaries"}),
    "lifecycle-uninstall": frozenset({"installation-removed", "personal-preserved", "process-exited"}),
    "lifecycle-purge": frozenset({"installation-removed", "personal-removed", "process-exited"}),
}

ENVIRONMENT_FIELDS = frozenset({
    "os",
    "architecture",
    "standard_user",
    "elevated",
    "distro",
    "distro_version",
    "glibc_version",
})
HARDWARE_FIELDS = frozenset({"chip", "model"})
CASE_FIELDS = frozenset({
    "name",
    "command",
    "status",
    "exit_code",
    "started_at",
    "duration_ms",
    "assertions",
    "artifacts",
})
ARTIFACT_FIELDS = frozenset({"path", "kind", "size", "sha256"})
FORBIDDEN_COMMAND_MARKERS = ("mock", "fake", "stub", "fixture", "dry-run")
M3_CHIP = re.compile(r"^Apple M3(?:\s.*)?$")


class NativeEvidenceError(ValueError):
    """A native handoff does not satisfy the closed release contract."""


def _string(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise NativeEvidenceError(f"{field} precisa ser texto não vazio")
    if "\x00" in value or len(value) > 512:
        raise NativeEvidenceError(f"{field} contém valor inseguro")
    return value


def validate_environment(value: object, *, platform: str) -> dict[str, object]:
    if platform not in KNOWN_NATIVE_PLATFORMS:
        raise NativeEvidenceError(f"plataforma nativa desconhecida: {platform!r}")
    if not isinstance(value, Mapping) or set(value) != ENVIRONMENT_FIELDS:
        raise NativeEvidenceError("ambiente nativo possui campos desconhecidos ou ausentes")
    environment = dict(value)
    os_name = _string(environment["os"], "environment.os").casefold()
    architecture = _string(environment["architecture"], "environment.architecture").casefold()
    if type(environment["standard_user"]) is not bool or type(environment["elevated"]) is not bool:
        raise NativeEvidenceError("ambiente nativo exige flags booleanas exatas")
    if environment["elevated"] or not environment["standard_user"]:
        raise NativeEvidenceError("smoke nativo deve executar sob usuário padrão não elevado")
    for field in ("distro", "distro_version", "glibc_version"):
        item = environment[field]
        if item is not None and (not isinstance(item, str) or len(item) > 128 or "\x00" in item):
            raise NativeEvidenceError(f"environment.{field} inválido")
    if platform == "Linux-X64":
        if os_name != "linux" or architecture not in {"x86_64", "amd64"}:
            raise NativeEvidenceError("Linux-X64 não corresponde ao ambiente nativo")
        if not all(environment[field] for field in ("distro", "distro_version", "glibc_version")):
            raise NativeEvidenceError("Linux-X64 exige distro, versão e GLIBC")
    elif platform == "Windows-X64":
        if os_name != "windows" or architecture not in {"x64", "amd64"}:
            raise NativeEvidenceError("Windows-X64 não corresponde ao ambiente nativo")
        if any(environment[field] is not None for field in ("distro", "distro_version", "glibc_version")):
            raise NativeEvidenceError("Windows não deve declarar distro, versão ou GLIBC")
    elif platform == "macOS-ARM64":
        if os_name != "macos" or architecture != "arm64":
            raise NativeEvidenceError("macOS-ARM64 não corresponde ao ambiente nativo")
        if any(environment[field] is not None for field in ("distro", "distro_version", "glibc_version")):
            raise NativeEvidenceError("macOS não deve declarar distro, versão ou GLIBC")
    elif platform == "macOS-X64":
        if os_name != "macos" or architecture not in {"x86_64", "x64"}:
            raise NativeEvidenceError("macOS-X64 não corresponde ao ambiente nativo")
        if any(environment[field] is not None for field in ("distro", "distro_version", "glibc_version")):
            raise NativeEvidenceError("macOS não deve declarar distro, versão ou GLIBC")
    return environment


def validate_hardware(value: object, *, platform: str) -> dict[str, str] | None:
    """Validate the hardware attestation required by the native M3 gate."""

    if platform != "macOS-ARM64":
        if value is not None:
            raise NativeEvidenceError(
                f"hardware só pode ser declarado para macOS-ARM64, não {platform}"
            )
        return None
    if not isinstance(value, Mapping) or set(value) != HARDWARE_FIELDS:
        raise NativeEvidenceError(
            "macOS-ARM64 exige atestação hardware com chip e model"
        )
    chip = _string(value["chip"], "hardware.chip")
    model = _string(value["model"], "hardware.model")
    if M3_CHIP.fullmatch(chip.strip()) is None:
        raise NativeEvidenceError("hardware.chip não confirma Apple M3")
    return {"chip": chip.strip(), "model": model.strip()}


def validate_command(value: object, *, case_name: str) -> list[str]:
    """Validate a native command without permitting shell interpretation."""

    if (
        not isinstance(value, list)
        or not value
        or not all(
            isinstance(part, str)
            and part
            and not any(
                control in part
                for control in ("\x00", "\r", "\n", ";", "&", "|", "`", "$")
            )
            for part in value
        )
    ):
        raise NativeEvidenceError(f"caso nativo {case_name} possui comando inválido")
    command = [str(part) for part in value]
    rendered = shlex.join(command).casefold()
    if any(marker in rendered for marker in FORBIDDEN_COMMAND_MARKERS):
        raise NativeEvidenceError(f"caso nativo {case_name} usa marcador de mock/dry-run")
    return command


def _validate_artifacts(value: object, *, case_name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise NativeEvidenceError(f"caso nativo {case_name} precisa de artefato de evidência")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for artifact in value:
        if not isinstance(artifact, Mapping) or set(artifact) != ARTIFACT_FIELDS:
            raise NativeEvidenceError(f"artefato do caso nativo {case_name} inválido")
        path = artifact["path"]
        if (
            not isinstance(path, str)
            or not path
            or "\\" in path
            or any(ord(character) < 0x20 for character in path)
        ):
            raise NativeEvidenceError(f"caminho de artefato inválido no caso nativo {case_name}")
        portable_path = PurePosixPath(path)
        if portable_path.is_absolute() or any(part in {"", ".", ".."} for part in portable_path.parts):
            raise NativeEvidenceError(f"caminho de artefato inseguro no caso nativo {case_name}")
        kind = _string(artifact["kind"], f"artifact.{case_name}.kind")
        if kind in seen:
            raise NativeEvidenceError(f"artefato duplicado no caso nativo {case_name}")
        seen.add(kind)
        if type(artifact["size"]) is not int or artifact["size"] < 0:
            raise NativeEvidenceError(f"tamanho inválido no caso nativo {case_name}")
        digest = artifact["sha256"]
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise NativeEvidenceError(f"SHA-256 inválido no caso nativo {case_name}")
        result.append({
            "path": portable_path.as_posix(),
            "kind": kind,
            "size": artifact["size"],
            "sha256": digest,
        })
    return result


def validate_cases(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or len(value) != len(CANONICAL_CASES):
        raise NativeEvidenceError("handoff nativo não registra exatamente os casos obrigatórios")
    result: list[dict[str, object]] = []
    for expected_name, raw_case in zip(CANONICAL_CASES, value, strict=True):
        if not isinstance(raw_case, Mapping) or set(raw_case) != CASE_FIELDS:
            raise NativeEvidenceError(f"caso nativo {expected_name} possui campos inválidos")
        if raw_case["name"] != expected_name or raw_case["status"] != "passed":
            raise NativeEvidenceError(f"caso nativo fora da ordem ou não aprovado: {expected_name}")
        if (
            type(raw_case["exit_code"]) is not int
            or raw_case["exit_code"] != 0
            or not isinstance(raw_case["started_at"], str)
            or UTC_TIMESTAMP.fullmatch(raw_case["started_at"]) is None
            or type(raw_case["duration_ms"]) is not int
            or raw_case["duration_ms"] < 0
        ):
            raise NativeEvidenceError(f"caso nativo {expected_name} possui resultado inválido")
        assertions = raw_case["assertions"]
        if (
            not isinstance(assertions, list)
            or any(not isinstance(item, str) or not item for item in assertions)
            or len(assertions) != len(set(assertions))
            or set(assertions) != CASE_ASSERTIONS[expected_name]
        ):
            raise NativeEvidenceError(f"assertions incompletas no caso nativo {expected_name}")
        command = validate_command(raw_case["command"], case_name=expected_name)
        result.append({
            "name": expected_name,
            "command": list(command),
            "status": "passed",
            "exit_code": 0,
            "started_at": raw_case["started_at"],
            "duration_ms": raw_case["duration_ms"],
            "assertions": sorted(assertions),
            "artifacts": _validate_artifacts(raw_case["artifacts"], case_name=expected_name),
        })
    return tuple(result)


def commands_from_cases(cases: tuple[dict[str, object], ...]) -> list[str]:
    return [" ".join(str(part) for part in case["command"]) for case in cases]


__all__ = [
    "ARTIFACT_FIELDS",
    "CASE_ASSERTIONS",
    "CASE_FIELDS",
    "CANONICAL_CASES",
    "ENVIRONMENT_FIELDS",
    "HARDWARE_FIELDS",
    "NATIVE_EVIDENCE_FORMAT",
    "KNOWN_NATIVE_PLATFORMS",
    "REQUIRED_NATIVE_PLATFORMS",
    "NativeEvidenceError",
    "commands_from_cases",
    "validate_command",
    "validate_cases",
    "validate_environment",
    "validate_hardware",
]
