#!/usr/bin/env python3
"""Install the exact public installer candidate in an isolated directory.

This helper is intentionally a release gate, not a fallback installer.  It
resolves the candidate from the public catalog, downloads the pinned bytes
through the canonical bounded downloader, validates the bundle before
execution, and runs that exact zipapp with an explicit non-interactive install
contract.  Trust metadata is mandatory and must be supplied by the protected
release environment; local fixtures are never accepted as a substitute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from x86qw_runtime.io.archive import (  # noqa: E402
    ArchiveError,
    extract_archive,
    read_archive_member,
    validate_installer_bundle,
)
from x86qw_runtime.io.downloader import (  # noqa: E402
    BoundedMetadata,
    DownloadError,
    PinnedArtifact,
    RetryPolicy,
    download,
    download_mirrors,
    validate_https_url,
)


CATALOG_URL = "https://x86qw.x86.com.br/api/v1/catalog.json"
CATALOG_MAX_BYTES = 2 * 1024 * 1024
CATALOG_DEADLINE_SECONDS = 60.0
BUNDLE_MAX_BYTES = 512 * 1024 * 1024
BUNDLE_DEADLINE_SECONDS = 20 * 60.0
PROCESS_TIMEOUT_SECONDS = 45 * 60.0
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9A-Za-z-][0-9A-Za-z-]*(?:\.(?:0|[1-9A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
STABLE_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
NIGHTLY_VERSION = re.compile(r"^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}$")


class PublicInstallSmokeError(RuntimeError):
    """The public endpoint did not satisfy the release smoke contract."""


def _is_candidate_version(version: str) -> bool:
    return isinstance(version, str) and SEMVER.fullmatch(version) is not None


def _require_https(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PublicInstallSmokeError(f"{label} ausente ou inválido.")
    try:
        parsed = validate_https_url(value, label)
    except Exception as error:  # downloader policy errors are implementation details
        raise PublicInstallSmokeError(f"{label} precisa usar HTTPS.") from error
    if parsed.username or parsed.password:
        raise PublicInstallSmokeError(f"{label} não pode conter credenciais.")
    return value


def _catalog_package(catalog: object, version: str) -> dict[str, Any]:
    if not isinstance(catalog, dict) or catalog.get("project") != "x86qw":
        raise PublicInstallSmokeError("catálogo público inválido.")
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise PublicInstallSmokeError("catálogo público não contém packages.")
    matches = [
        item for item in packages
        if isinstance(item, dict)
        and item.get("package") == "x86qw-installer"
        and item.get("version") == version
        and item.get("current") is True
    ]
    if len(matches) != 1:
        raise PublicInstallSmokeError(
            "o catálogo público não contém exatamente um instalador corrente para o candidato."
        )
    package = matches[0]
    size = package.get("size")
    digest = package.get("sha256")
    if type(size) is not int or size <= 0 or size > BUNDLE_MAX_BYTES:
        raise PublicInstallSmokeError("tamanho do instalador público inválido.")
    if not isinstance(digest, str) or not HEX64.fullmatch(digest):
        raise PublicInstallSmokeError("SHA-256 do instalador público inválido.")
    urls = package.get("urls")
    if not isinstance(urls, list) or not urls or not all(isinstance(url, str) for url in urls):
        raise PublicInstallSmokeError("mirrors do instalador público inválidos.")
    normalized_urls = tuple(dict.fromkeys(_require_https(url, "mirror do instalador") for url in urls))
    filename = package.get("filename")
    if not isinstance(filename, str) or not filename.startswith("x86qw-installer-") or not filename.endswith(".zip"):
        raise PublicInstallSmokeError("nome do instalador público inválido.")
    return {
        "size": size,
        "sha256": digest,
        "urls": normalized_urls,
        "filename": filename,
    }


def _download_catalog_payload(url: str) -> tuple[dict[str, Any], bytes]:
    result = download(BoundedMetadata(
        url=_require_https(url, "catálogo público"),
        maximum_size=CATALOG_MAX_BYTES,
        deadline_seconds=CATALOG_DEADLINE_SECONDS,
        retry=RetryPolicy(attempts=3),
        label="catálogo público",
    ))
    payload = result.data or b""
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicInstallSmokeError("catálogo público não é JSON válido.") from error
    if not isinstance(value, dict):
        raise PublicInstallSmokeError("catálogo público não é um objeto JSON.")
    return value, payload


def _download_catalog(url: str) -> dict[str, Any]:
    """Compatibility wrapper for callers that only need parsed HTTP JSON."""

    return _download_catalog_payload(url)[0]


def _authenticate_public_catalog(
    catalog_payload: bytes,
    trust_metadata_url: str,
) -> dict[str, Any]:
    """Authenticate the exact public catalog bytes through the TUF target.

    The HTTP catalog is only a transport representation.  Candidate selection
    is allowed to continue only after the bounded TUF verifier confirms that
    the authenticated ``catalog/catalog.json`` target is byte-for-byte equal
    to those public bytes.
    """

    if not isinstance(catalog_payload, bytes) or not catalog_payload:
        raise PublicInstallSmokeError("catálogo público não retornou bytes.")
    if len(catalog_payload) > CATALOG_MAX_BYTES:
        raise PublicInstallSmokeError("catálogo público excede o limite.")
    trust_url = _require_https(trust_metadata_url, "metadados de confiança")
    base_url = trust_url if trust_url.endswith("/") else f"{trust_url}/"
    try:
        from maintenance.tools.verify_public_tuf import (  # noqa: PLC0415
            PublicTufVerificationError,
            verify_public_catalog,
        )

        with tempfile.TemporaryDirectory(prefix="x86qw-public-catalog-") as temporary:
            catalog_path = Path(temporary) / "catalog.json"
            catalog_path.write_bytes(catalog_payload)
            verify_public_catalog(
                base_url=base_url,
                root=ROOT / "maintenance/trust/root.json",
                catalog=catalog_path,
            )
    except (OSError, PublicTufVerificationError) as error:
        raise PublicInstallSmokeError(
            "catálogo público não foi autenticado pela cadeia TUF."
        ) from error
    try:
        authenticated = json.loads(catalog_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicInstallSmokeError("catálogo TUF autenticado não é JSON válido.") from error
    if not isinstance(authenticated, dict):
        raise PublicInstallSmokeError("catálogo TUF autenticado não é um objeto JSON.")
    return authenticated


def _run_json(
    application: Path,
    action: str,
    *,
    target: Path | None = None,
    env: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    # Keep the argv literal at this boundary.  A release smoke must never
    # turn an arbitrary string into a shell command, and the remote/process
    # policy scanner can verify each executable and argument independently.
    if target is None:
        command = [sys.executable, str(application), "--json", action]
        result = subprocess.run(
            [sys.executable, str(application), "--json", action],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    else:
        command = [sys.executable, str(application), "--json", action, str(target)]
        result = subprocess.run(
            [sys.executable, str(application), "--json", action, str(target)],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    print(result.stdout, end="")
    if result.returncode != 0:
        raise PublicInstallSmokeError(
            f"comando público terminou com código {result.returncode}: {command[1:]!r}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PublicInstallSmokeError("comando público não retornou JSON válido.") from error
    if not isinstance(value, dict):
        raise PublicInstallSmokeError("comando público retornou JSON inesperado.")
    return value


def _require_action_success(value: dict[str, Any], action: str) -> dict[str, Any]:
    if value.get("ok") is not True:
        raise PublicInstallSmokeError(
            f"launcher instalado não confirmou {action}: {value!r}"
        )
    return value


def _launcher_path(target: Path) -> Path:
    return Path(target) / ("x86qw.cmd" if os.name == "nt" else "x86qw.sh")


def _run_launcher(
    target: Path,
    arguments: tuple[str, ...],
    *,
    env: dict[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    launcher = _launcher_path(target)
    if launcher.is_symlink() or not launcher.is_file():
        raise PublicInstallSmokeError(f"launcher instalado ausente ou inseguro: {launcher}")
    if os.name != "nt" and not os.access(launcher, os.X_OK):
        raise PublicInstallSmokeError(f"launcher Unix instalado não é executável: {launcher}")
    if os.name == "nt":
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher), *arguments]
    else:
        command = [str(launcher), *arguments]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        raise PublicInstallSmokeError(
            f"launcher instalado terminou com código {result.returncode}: {arguments!r}"
        )
    return result


def _run_launcher_json(
    target: Path,
    action: str,
    *arguments: str,
    env: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    result = _run_launcher(
        target,
        (action, *arguments, "--json"),
        env=env,
        timeout=timeout,
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PublicInstallSmokeError(
            f"launcher instalado não retornou JSON para {action}."
        ) from error
    if not isinstance(value, dict):
        raise PublicInstallSmokeError(f"resposta JSON inesperada para {action}.")
    return _require_action_success(value, action)


def _install_application(
    application: Path,
    target: Path,
    *,
    env: dict[str, str],
    platform: str,
    channel: str,
    release: str,
    profile: str,
    version: str,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(application),
        "--online-only",
        "--non-interactive",
        "--platform", platform,
        "--channel", channel,
        "--release", release,
        "--profile", profile,
        "install", str(target),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=PROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        raise PublicInstallSmokeError(
            f"instalação pública terminou com código {result.returncode}."
        )
    receipt_path = target / ".x86qw" / "cli" / "receipt"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PublicInstallSmokeError(
            "a instalação pública não deixou um receipt de CLI válido."
        ) from error
    if not isinstance(receipt, dict) or receipt.get("version") != version:
        raise PublicInstallSmokeError(
            "o receipt instalado não corresponde ao candidato."
        )
    return receipt


def _write_acceptance_sentinels(target: Path) -> tuple[Path, Path]:
    config = target / "qw" / "configs" / "x86qw-public-acceptance.cfg"
    demo = target / "qw" / "demos" / "x86qw-public-acceptance.mvd"
    config.parent.mkdir(parents=True, exist_ok=True)
    demo.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("// public acceptance personal fixture\n", encoding="utf-8")
    demo.write_bytes(b"x86qw public acceptance personal fixture\n")
    return config, demo


def _require_preserved_sentinels(sentinels: tuple[Path, Path]) -> None:
    config, demo = sentinels
    if not config.is_file() or config.read_text(encoding="utf-8") != "// public acceptance personal fixture\n":
        raise PublicInstallSmokeError("uninstall conservador removeu a configuração pessoal")
    if not demo.is_file() or demo.read_bytes() != b"x86qw public acceptance personal fixture\n":
        raise PublicInstallSmokeError("uninstall conservador removeu a demo pessoal")


def _run_full_lifecycle(
    application: Path,
    target: Path,
    *,
    workspace: Path,
    env: dict[str, str],
    platform: str,
    channel: str,
    release: str,
    profile: str,
    version: str,
) -> dict[str, object]:
    """Exercise the installed launcher against disposable public targets."""

    version_result = _run_launcher(target, ("version",), env=env, timeout=60)
    if f"x86QW {version}" not in version_result.stdout:
        raise PublicInstallSmokeError("version do launcher instalado divergiu do candidato")

    sentinels = _write_acceptance_sentinels(target)
    changes = _run_launcher_json(target, "changes", env=env, timeout=PROCESS_TIMEOUT_SECONDS)
    migrate = _run_launcher_json(
        target, "migrate", "--dry-run", env=env, timeout=PROCESS_TIMEOUT_SECONDS,
    )
    update_plan = _run_launcher_json(
        target, "update", "--dry-run", env=env, timeout=PROCESS_TIMEOUT_SECONDS,
    )
    update_first = _run_launcher_json(
        target, "update", "--yes", env=env, timeout=PROCESS_TIMEOUT_SECONDS,
    )
    update_second = _run_launcher_json(
        target, "update", "--yes", env=env, timeout=PROCESS_TIMEOUT_SECONDS,
    )
    verify = _run_launcher_json(
        target, "verify", env=env, timeout=PROCESS_TIMEOUT_SECONDS,
    )
    uninstall = _run_launcher_json(
        target, "uninstall", env=env, timeout=PROCESS_TIMEOUT_SECONDS,
    )
    _require_preserved_sentinels(sentinels)

    purge_target = workspace / "instalação pública purge ✓"
    _install_application(
        application,
        purge_target,
        env=env,
        platform=platform,
        channel=channel,
        release=release,
        profile=profile,
        version=version,
    )
    _write_acceptance_sentinels(purge_target)
    purge = _run_launcher_json(
        purge_target, "uninstall", "--purge",
        env=env, timeout=PROCESS_TIMEOUT_SECONDS,
    )
    if purge_target.exists() or purge_target.is_symlink():
        raise PublicInstallSmokeError("uninstall --purge não removeu o destino descartável")
    return {
        "launcher": _launcher_path(target).name,
        "operations": {
            "version": True,
            "changes": changes.get("ok") is True,
            "migrate_dry_run": migrate.get("ok") is True and migrate.get("dry_run") is True,
            "update_dry_run": update_plan.get("ok") is True and update_plan.get("dry_run") is True,
            "update_apply": update_first.get("ok") is True,
            "update_idempotent": update_second.get("ok") is True,
            "verify": verify.get("ok") is True,
            "uninstall": uninstall.get("ok") is True,
            "uninstall_purge": purge.get("ok") is True,
        },
        "personal_data_preserved_by_uninstall": True,
        "purge_removed_personal_data": True,
    }


def run_smoke(
    *,
    version: str,
    platform: str,
    channel: str,
    release: str,
    profile: str,
    catalog_url: str,
    trust_metadata_url: str,
    full_lifecycle: bool = False,
    output: Path | None = None,
) -> dict[str, Any]:
    if not _is_candidate_version(version):
        raise PublicInstallSmokeError("a versão do candidato precisa ser SemVer válida.")
    if platform not in {"linux", "macos", "windows"}:
        raise PublicInstallSmokeError(f"plataforma não suportada pelo smoke: {platform}")
    if channel not in {"stable", "nightly"}:
        raise PublicInstallSmokeError("canal inválido.")
    if channel == "stable" and release != "latest" and not STABLE_VERSION.fullmatch(release):
        raise PublicInstallSmokeError("release stable precisa ser latest ou x.y.z.")
    if channel == "nightly" and release != "latest" and not NIGHTLY_VERSION.fullmatch(release):
        raise PublicInstallSmokeError("release nightly precisa ser latest ou YYYYMMDD-HHMMSS_sha.")
    if profile not in {"essential", "recommended", "complete"}:
        raise PublicInstallSmokeError("perfil inválido.")
    trust_url = _require_https(trust_metadata_url, "metadados de confiança")
    _http_catalog, catalog_payload = _download_catalog_payload(catalog_url)
    authenticated_catalog = _authenticate_public_catalog(catalog_payload, trust_url)
    package = _catalog_package(authenticated_catalog, version)

    with tempfile.TemporaryDirectory(prefix="x86qw-public-install-") as temporary:
        workspace = Path(temporary)
        bundle_path = workspace / str(package["filename"])
        try:
            download_mirrors(tuple(
                PinnedArtifact(
                    url=url,
                    destination=bundle_path,
                    expected_size=package["size"],
                    expected_sha256=package["sha256"],
                    maximum_size=BUNDLE_MAX_BYTES,
                    deadline_seconds=BUNDLE_DEADLINE_SECONDS,
                    retry=RetryPolicy(attempts=3),
                    label="bundle público do candidato",
                )
                for url in package["urls"]
            ))
            plan = validate_installer_bundle(bundle_path, version)
            extracted = workspace / "bundle"
            extracted.mkdir()
            extract_archive(plan, extracted)
        except (ArchiveError, DownloadError, OSError) as error:
            raise PublicInstallSmokeError(f"bundle público rejeitado: {error}") from error

        prefix = extracted / f"x86qw-installer-{version}"
        application = prefix / "x86qw.pyz"
        if not application.is_file() or application.is_symlink():
            raise PublicInstallSmokeError("bundle público não contém x86qw.pyz seguro.")
        target = workspace / "instalação pública ✓"
        env = dict(os.environ)
        env["X86_QW_TRUST_METADATA_URL"] = trust_url.rstrip("/")
        env["X86_QW_TRUST_METADATA_REQUIRED"] = "1"
        _install_application(
            application,
            target,
            env=env,
            platform=platform,
            channel=channel,
            release=release,
            profile=profile,
            version=version,
        )

        version_json = _run_json(
            application,
            "version",
            env=env,
            timeout=60,
        )
        version_data = version_json.get("data")
        if (
            version_json.get("ok") is not True
            or not isinstance(version_data, dict)
            or version_data.get("version") != version
        ):
            raise PublicInstallSmokeError("version --json divergiu do candidato.")
        verify_json = _run_json(
            application,
            "verify",
            target=target,
            env=env,
            timeout=PROCESS_TIMEOUT_SECONDS,
        )
        if verify_json.get("ok") is not True:
            raise PublicInstallSmokeError("verify --json não confirmou a instalação pública.")
        result = {
            "format": 1,
            "project": "x86qw",
            "candidate_version": version,
            "platform": platform,
            "channel": channel,
            "release": release,
            "profile": profile,
            "catalog_sha256": hashlib.sha256(catalog_payload).hexdigest(),
            "bundle_sha256": package["sha256"],
            "verified": True,
        }
        if full_lifecycle:
            result["full_lifecycle"] = _run_full_lifecycle(
                application,
                target,
                workspace=workspace,
                env=env,
                platform=platform,
                channel=channel,
                release=release,
                profile=profile,
                version=version,
            )
        if output is not None:
            destination = Path(output)
            if destination.exists() or destination.is_symlink():
                raise PublicInstallSmokeError(f"relatório público já existe: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="versão estável do candidato")
    parser.add_argument("--platform", required=True, choices=("linux", "macos", "windows"))
    parser.add_argument("--channel", required=True, choices=("stable", "nightly"))
    parser.add_argument("--release", required=True, help="latest ou versão exata do cliente")
    parser.add_argument("--profile", required=True, choices=("essential", "recommended", "complete"))
    parser.add_argument("--catalog-url", default=CATALOG_URL)
    parser.add_argument("--trust-metadata-url", required=True)
    parser.add_argument(
        "--full-lifecycle", action="store_true",
        help="também executa a aceitação completa pelo launcher instalado em destinos descartáveis",
    )
    parser.add_argument("--output", type=Path, help="grava o recibo JSON da aceitação sem overwrite")
    return parser


def main(argv: list[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    try:
        result = run_smoke(
            version=options.version,
            platform=options.platform,
            channel=options.channel,
            release=options.release,
            profile=options.profile,
            catalog_url=options.catalog_url,
            trust_metadata_url=options.trust_metadata_url,
            full_lifecycle=options.full_lifecycle,
            output=options.output,
        )
    except (PublicInstallSmokeError, DownloadError, ArchiveError, OSError, subprocess.SubprocessError) as error:
        print(f"[ERRO] Smoke de instalação pública falhou: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
