"""Host filesystem locations resolved outside installer entrypoints."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from typing import Iterator

from ..errors import InstallerError
from ..io import managed_files, private_fs
from ..io.managed_files import persistent_path_identity


class HostPlatformError(InstallerError):
    """A native host location could not be resolved safely."""


def system() -> str:
    return platform.system()


def machine() -> str:
    return platform.machine()


def python_version() -> str:
    return platform.python_version()


def supports_posix_permissions(*, os_name: str | None = None) -> bool:
    """Return whether native chmod/access bits are meaningful on this host."""

    return (os.name if os_name is None else os_name) != "nt"


def apply_mode(
    path: Path,
    mode: int,
    *,
    os_name: str | None = None,
) -> None:
    """Apply one POSIX mode, or preserve the path unchanged on Windows."""

    if supports_posix_permissions(os_name=os_name):
        Path(path).chmod(mode)


def apply_descriptor_mode(
    descriptor: int,
    mode: int,
    *,
    os_name: str | None = None,
) -> None:
    """Apply one mode to an open descriptor when the platform supports it."""

    if supports_posix_permissions(os_name=os_name):
        os.fchmod(descriptor, mode)


def path_mode(path: Path) -> int:
    """Read only the portable permission bits from a filesystem entry."""

    return stat.S_IMODE(Path(path).lstat().st_mode)


def executable_permission_missing(
    path: Path,
    *,
    os_name: str | None = None,
) -> bool:
    """Return whether a POSIX executable lacks an effective execute bit."""

    return supports_posix_permissions(os_name=os_name) and not os.access(path, os.X_OK)


def add_owner_execute(
    path: Path,
    *,
    os_name: str | None = None,
) -> int:
    """Add owner execute permission and return the previous portable mode."""

    previous = path_mode(path)
    apply_mode(path, previous | stat.S_IXUSR, os_name=os_name)
    return previous


def unlink_identity_bound_file(
    path: Path,
    expected_identity: tuple[int, int],
    *,
    os_name: str | None = None,
) -> None:
    """Remove only the expected regular file through the native safe path."""

    path = Path(path)
    if not supports_posix_permissions(os_name=os_name):
        private_fs.unlink_private_file(path, expected_identity=expected_identity)
        return
    if not managed_files.unlink_identity_bound_regular(path, expected_identity):
        raise HostPlatformError(f"Arquivo gerenciado mudou de identidade: {path}")


def cli_launcher_names_for_removal(
    *,
    os_name: str | None = None,
) -> tuple[str, ...]:
    """List launchers removable by the current process without self-deletion."""

    names = ["x86qw.sh"]
    if supports_posix_permissions(os_name=os_name):
        names.append("x86qw.cmd")
    return tuple(names)


@dataclass(frozen=True)
class LaunchPathIdentity:
    path: Path
    directory: bool
    identity: tuple[int, int]
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class LaunchTarget:
    executable: Path
    paths: tuple[LaunchPathIdentity, ...]
    expected_sha256: str | None = None


@dataclass(frozen=True)
class BoundLaunchTarget:
    executable: str
    pass_fds: tuple[int, ...] = ()
    retain_until_exit: bool = False


def _launch_path_identity(path: Path, *, directory: bool) -> LaunchPathIdentity:
    path = Path(path)
    try:
        identity = persistent_path_identity(path, directory=directory)
        metadata = path.lstat()
        current = persistent_path_identity(path, directory=directory)
    except OSError as error:
        raise HostPlatformError(f"Alvo de execução ausente ou inseguro: {path}") from error
    if identity != current:
        raise HostPlatformError(f"Alvo de execução mudou durante a validação: {path}")
    return LaunchPathIdentity(
        path,
        directory,
        (int(identity[0]), int(identity[1])),
        int(metadata.st_size) if not directory else 0,
        int(getattr(metadata, "st_mtime_ns", metadata.st_mtime * 1_000_000_000)),
    )


def executable_launch_target(
    executable: Path,
    *,
    expected_sha256: str | None = None,
    ancestors: tuple[Path, ...] = (),
) -> LaunchTarget:
    if expected_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("expected_sha256 must be lowercase SHA-256")
    paths = tuple(
        _launch_path_identity(path, directory=True) for path in ancestors
    ) + (_launch_path_identity(executable, directory=False),)
    return LaunchTarget(Path(executable), paths, expected_sha256)


def client_launch_target(
    runtime: Path,
    *,
    system: str | None = None,
    expected_sha256: str | None = None,
) -> LaunchTarget:
    system = system or platform.system()
    if system == "Darwin":
        from .macos import app_launch_paths

        app, contents, macos, executable = app_launch_paths(runtime)
        return executable_launch_target(
            executable,
            expected_sha256=expected_sha256,
            ancestors=(app, contents, macos),
        )
    executable = client_executable(runtime, system=system)
    return executable_launch_target(executable, expected_sha256=expected_sha256)


def _validate_launch_paths(target: LaunchTarget) -> None:
    if not isinstance(target, LaunchTarget) or not target.paths:
        raise HostPlatformError("Contrato de execução inválido.")
    for expected in target.paths:
        current = _launch_path_identity(expected.path, directory=expected.directory)
        if current != expected:
            raise HostPlatformError(
                f"Alvo de execução mudou após a validação: {expected.path}"
            )


def _open_validated_executable(target: LaunchTarget) -> int:
    executable = target.executable
    digest = hashlib.sha256()
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(executable, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (int(opened.st_dev), int(opened.st_ino))
            != target.paths[-1].identity
        ):
            raise OSError("executable identity changed")
        if target.expected_sha256 is not None:
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                while block := source.read(1024 * 1024):
                    digest.update(block)
        after = os.fstat(descriptor)
        current = _launch_path_identity(executable, directory=False)
        if current != target.paths[-1]:
            raise OSError("executable path changed")
    except BaseException as error:
        if descriptor >= 0:
            os.close(descriptor)
        if not isinstance(error, OSError):
            raise
        raise HostPlatformError(
            f"Alvo de execução mudou após a validação: {executable}"
        ) from error
    if (
        int(after.st_size) != target.paths[-1].size
        or int(getattr(after, "st_mtime_ns", after.st_mtime * 1_000_000_000))
        != target.paths[-1].mtime_ns
        or target.expected_sha256 is not None
        and digest.hexdigest() != target.expected_sha256
    ):
        os.close(descriptor)
        raise HostPlatformError(
            f"Alvo de execução mudou após a validação: {executable}"
        )
    return descriptor


def revalidate_launch_target(target: LaunchTarget) -> None:
    _validate_launch_paths(target)
    os.close(_open_validated_executable(target))


def _unlink_bound_posix_snapshot(path: Path, identity: tuple[int, int]) -> None:
    rename_api = managed_files._get_posix_rename_api()
    if rename_api is None:
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent = os.open(path.parent, flags)
    except OSError:
        return
    quarantine = f".x86qw-launch-cleanup-{token_hex(12)}"
    try:
        try:
            rename_api.move_no_replace(parent, path.name, parent, quarantine)
        except FileNotFoundError:
            return
        metadata = os.stat(quarantine, dir_fd=parent, follow_symlinks=False)
        if (
            stat.S_ISREG(metadata.st_mode)
            and (int(metadata.st_dev), int(metadata.st_ino)) == identity
        ):
            os.unlink(quarantine, dir_fd=parent)
            return
        try:
            rename_api.move_no_replace(parent, quarantine, parent, path.name)
        except OSError:
            pass
    except OSError:
        pass
    finally:
        os.close(parent)


def _copy_validated_executable(
    source: int,
    destination: int,
    target: LaunchTarget,
) -> None:
    if target.expected_sha256 is None:
        raise HostPlatformError("O vínculo de execução exige SHA-256.")
    os.lseek(source, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while block := os.read(source, 1024 * 1024):
        digest.update(block)
        pending = memoryview(block)
        while pending:
            written = os.write(destination, pending)
            if written <= 0:
                raise OSError("executable snapshot write did not advance")
            pending = pending[written:]
    if digest.hexdigest() != target.expected_sha256:
        raise HostPlatformError(
            f"Alvo de execução mudou após a validação: {target.executable}"
        )
    metadata = os.fstat(destination)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != target.paths[-1].size:
        raise HostPlatformError(
            f"Snapshot de execução divergente: {target.executable}"
        )
    os.fchmod(destination, 0o500)
    os.fsync(destination)


def _open_windows_launch_guard(target: LaunchTarget) -> tuple[object, int]:
    if target.expected_sha256 is None:
        raise HostPlatformError("O vínculo de execução Windows exige SHA-256.")
    api = managed_files._get_windows_file_api()
    if api is None:
        raise HostPlatformError("A API de identidade do Windows está indisponível.")
    handle = api.open_handle(
        target.executable,
        access=api.GENERIC_READ,
        creation=api.OPEN_EXISTING,
        directory=False,
    )
    try:
        if (
            api.checked_identity(handle, directory=False)
            != target.paths[-1].identity
            or api.hash(handle, expected_size=target.paths[-1].size)
            != target.expected_sha256
            or api.checked_identity(handle, directory=False)
            != target.paths[-1].identity
        ):
            raise HostPlatformError(
                f"Alvo de execução mudou após a validação: {target.executable}"
            )
    except BaseException:
        managed_files._close_windows_handle(api, handle)
        raise
    return api, handle


@contextmanager
def bound_launch_target(
    target: LaunchTarget,
    *,
    system_name: str | None = None,
) -> Iterator[BoundLaunchTarget]:
    """Keep the validated executable object stable until process creation."""

    _validate_launch_paths(target)
    active_system = platform.system() if system_name is None else system_name
    if active_system == "Windows":
        api, handle = _open_windows_launch_guard(target)
        try:
            yield BoundLaunchTarget(str(target.executable))
        finally:
            managed_files._close_windows_handle(api, handle)
        return
    descriptor = _open_validated_executable(target)
    if active_system == "Linux":
        snapshot = -1
        try:
            import fcntl

            snapshot = os.memfd_create(
                "x86qw-launch",
                getattr(os, "MFD_CLOEXEC", 0x0001)
                | getattr(os, "MFD_ALLOW_SEALING", 0x0002),
            )
            _copy_validated_executable(descriptor, snapshot, target)
            seals = (
                getattr(fcntl, "F_SEAL_SEAL", 0x0001)
                | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
                | getattr(fcntl, "F_SEAL_GROW", 0x0004)
                | getattr(fcntl, "F_SEAL_WRITE", 0x0008)
            )
            fcntl.fcntl(snapshot, getattr(fcntl, "F_ADD_SEALS", 1033), seals)
        except BaseException as error:
            os.close(descriptor)
            if snapshot >= 0:
                os.close(snapshot)
            if not isinstance(error, (AttributeError, OSError)):
                raise
            raise HostPlatformError("O Linux não permitiu selar o snapshot de execução.") from error
        os.close(descriptor)
        try:
            yield BoundLaunchTarget(f"/proc/self/fd/{snapshot}", (snapshot,))
        finally:
            os.close(snapshot)
        return
    if active_system == "Darwin":
        suffix = target.executable.suffix
        snapshot_directory: Path | None = None
        snapshot_path: Path | None = None
        snapshot_bundle: Path | None = None
        snapshot = -1
        snapshot_identity: tuple[int, int] | None = None
        try:
            snapshot_directory = Path(tempfile.mkdtemp(prefix="x86qw-launch-"))
            directory_metadata = snapshot_directory.lstat()
            if (
                not stat.S_ISDIR(directory_metadata.st_mode)
                or stat.S_IMODE(directory_metadata.st_mode) != 0o700
                or any(
                    snapshot_directory.is_relative_to(path.path)
                    for path in target.paths
                    if path.directory
                )
            ):
                raise HostPlatformError(
                    "O diretório privado do snapshot de execução é inseguro."
                )
            # macOS resolves resources relative to the application bundle. A
            # detached executable snapshot is valid for portable binaries but
            # makes an .app client exit before it can read its configuration.
            bundle = next(
                (
                    item.path for item in target.paths
                    if item.directory and item.path.suffix.casefold() == ".app"
                ),
                None,
            )
            if bundle is not None:
                relative_executable = target.executable.relative_to(bundle)
                snapshot_bundle = snapshot_directory / bundle.name
                shutil.copytree(bundle, snapshot_bundle, symlinks=True)
                snapshot_path = snapshot_bundle / relative_executable
            else:
                snapshot_path = snapshot_directory / f"executable{suffix}"
            flags = os.O_WRONLY
            if snapshot_bundle is None:
                flags |= os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            snapshot = os.open(snapshot_path, flags, 0o700)
            created = os.fstat(snapshot)
            snapshot_identity = int(created.st_dev), int(created.st_ino)
            _copy_validated_executable(descriptor, snapshot, target)
            os.close(snapshot)
            snapshot = -1
            revalidate_launch_target(target)
            metadata = snapshot_path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or (int(metadata.st_dev), int(metadata.st_ino))
                != snapshot_identity
            ):
                raise HostPlatformError(
                    f"Snapshot de execução mudou: {target.executable}"
                )
            yield BoundLaunchTarget(str(snapshot_path), retain_until_exit=True)
        finally:
            if snapshot >= 0:
                os.close(snapshot)
            if snapshot_identity is not None and snapshot_path is not None:
                _unlink_bound_posix_snapshot(snapshot_path, snapshot_identity)
            if snapshot_bundle is not None:
                try:
                    shutil.rmtree(snapshot_bundle)
                except OSError:
                    pass
            if snapshot_directory is not None:
                try:
                    snapshot_directory.rmdir()
                except OSError:
                    pass
            os.close(descriptor)
        return
    os.close(descriptor)
    raise HostPlatformError(
        f"Plataforma sem vínculo seguro de execução: {active_system or sys.platform}"
    )


def client_executable(runtime: Path, *, system: str | None = None) -> Path:
    runtime = Path(runtime)
    system = system or platform.system()
    if system == "Darwin":
        from .macos import app_executable

        return app_executable(runtime)
    try:
        metadata = runtime.lstat()
    except OSError as error:
        raise HostPlatformError(f"Executável do cliente não encontrado: {runtime}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise HostPlatformError(f"Caminho inseguro para o executável do cliente: {runtime}")
    return runtime


def inspect_portable_binary(
    binary: Path,
    *,
    platform_id: str,
    os_name: str | None = None,
) -> str:
    """Validate one distributed client binary and return its SHA-256 identity."""

    binary = Path(binary)
    try:
        path_metadata = binary.lstat()
        if stat.S_ISLNK(path_metadata.st_mode) or not stat.S_ISREG(path_metadata.st_mode):
            raise OSError("not a regular file")
        with binary.open("rb") as source:
            metadata = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or (int(metadata.st_dev), int(metadata.st_ino))
                != (int(path_metadata.st_dev), int(path_metadata.st_ino))
                or metadata.st_size <= 0
            ):
                raise OSError("binary identity changed")
            header = source.read(min(512, metadata.st_size))
            if platform_id == "linux":
                if (
                    len(header) < 20
                    or header[:5] != b"\x7fELF\x02"
                    or struct.unpack_from("<H", header, 18)[0] != 62
                ):
                    raise HostPlatformError(
                        f"unexpected Linux binary format: {binary}"
                    )
                if (
                    (os.name if os_name is None else os_name) != "nt"
                    and not os.access(binary, os.X_OK)
                ):
                    raise HostPlatformError(
                        f"Linux AppImage is not executable: {binary}"
                    )
            elif platform_id == "windows":
                if len(header) < 64 or header[:2] != b"MZ":
                    raise HostPlatformError(
                        f"unexpected Windows binary format: {binary}"
                    )
                pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
                if pe_offset < 0x40 or pe_offset + 26 > metadata.st_size:
                    raise HostPlatformError(
                        f"unexpected Windows binary format: {binary}"
                    )
                source.seek(pe_offset)
                pe = source.read(26)
                if (
                    len(pe) < 26
                    or pe[:4] != b"PE\0\0"
                    or struct.unpack_from("<H", pe, 4)[0] != 0x8664
                    or struct.unpack_from("<H", pe, 24)[0] != 0x20B
                ):
                    raise HostPlatformError(
                        f"unexpected Windows binary format: {binary}"
                    )
            else:
                raise HostPlatformError(
                    f"unsupported portable binary platform: {platform_id}"
                )
            source.seek(0)
            digest = hashlib.sha256()
            while block := source.read(1024 * 1024):
                digest.update(block)
            current = os.fstat(source.fileno())
            if (
                current.st_size != metadata.st_size
                or int(getattr(current, "st_mtime_ns", 0))
                != int(getattr(metadata, "st_mtime_ns", 0))
            ):
                raise OSError("binary changed during inspection")
            return digest.hexdigest()
    except HostPlatformError:
        raise
    except OSError as error:
        raise HostPlatformError(f"invalid ezQuake binary: {binary}") from error


def service_runtime_variant(
    runtimes: Mapping[str, object],
    architecture_aliases: Mapping[str, object],
    host_platforms: Mapping[str, str],
    *,
    runtime_id: str | None = None,
    system: str | None = None,
    machine: str | None = None,
) -> str:
    """Select one declared service artifact for the actual host platform.

    Callers own catalog loading and policy.  This boundary only normalizes host
    facts and matches them against the supplied declarative runtime entries.
    """

    host_system = system or platform.system()
    host_machine = (machine or platform.machine()).casefold()
    system_id = host_platforms.get(host_system, host_system.casefold())
    runtime_ids = (runtime_id,) if runtime_id is not None else ("mvdsv", "qtv", "qwfwd")
    variants: set[str] = set()
    for selected_runtime in runtime_ids:
        runtime = runtimes.get(selected_runtime)
        if not isinstance(runtime, Mapping):
            raise HostPlatformError("Catálogo de runtimes de serviço da CLI está inválido.")
        platforms = runtime.get("platforms")
        if not isinstance(platforms, (list, tuple)):
            raise HostPlatformError("Catálogo de runtimes de serviço da CLI está inválido.")
        for entry in platforms:
            if not isinstance(entry, Mapping) or entry.get("system") != system_id:
                continue
            architecture = entry.get("architecture")
            if not isinstance(architecture, str):
                continue
            aliases = architecture_aliases.get(architecture, ())
            if not isinstance(aliases, (list, tuple)) or not all(
                isinstance(alias, str) for alias in aliases
            ):
                raise HostPlatformError("Catálogo de arquiteturas da CLI está inválido.")
            variant = entry.get("variant")
            if (
                host_machine in {alias.casefold() for alias in aliases}
                and isinstance(variant, str)
                and variant
            ):
                variants.add(variant)
    if len(variants) == 1:
        return variants.pop()
    raise HostPlatformError(
        f"Runtime de serviço indisponível para {host_system} {host_machine}. "
        "Os alvos distribuídos são macOS arm64, Linux amd64 e Windows x64."
    )


def service_runtime_executable(binary: Path, *, os_name: str | None = None) -> Path:
    """Validate one managed service executable without repairing it implicitly."""

    binary = Path(binary)
    try:
        metadata = binary.lstat()
    except OSError as error:
        raise HostPlatformError(f"Executável gerenciado ausente ou inseguro: {binary}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise HostPlatformError(f"Executável gerenciado ausente ou inseguro: {binary}")
    if (os.name if os_name is None else os_name) != "nt" and not os.access(binary, os.X_OK):
        raise HostPlatformError(
            f"Executável gerenciado sem permissão de execução: {binary}. Execute repair."
        )
    return binary


def user_cache_directory(
    application: str,
    *,
    system: str | None = None,
    environment: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", application) is None:
        raise ValueError("invalid cache application name")
    system = system or platform.system()
    environment = os.environ if environment is None else environment
    home = Path.home() if home is None else Path(home)
    if system == "Darwin":
        try:
            result = subprocess.run(
                ["getconf", "DARWIN_USER_CACHE_DIR"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
        except FileNotFoundError as error:
            raise HostPlatformError(
                "Não foi possível localizar o cache nativo do macOS."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise HostPlatformError(
                "A consulta ao cache nativo do macOS excedeu o tempo limite."
            ) from error
        base = result.stdout.decode("utf-8", errors="strict").strip()
        if result.returncode != 0 or not base:
            raise HostPlatformError(
                "Não foi possível localizar o cache nativo do macOS."
            )
        return Path(base) / application
    if system == "Windows":
        base = environment.get("LOCALAPPDATA")
        if not base:
            raise HostPlatformError("LOCALAPPDATA não está definido.")
        return Path(base) / application
    base = environment.get("XDG_CACHE_HOME")
    return (Path(base) if base else home / ".cache") / application
