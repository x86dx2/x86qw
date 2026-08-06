"""Pure, idempotent migrations for persisted x86QW runtime contracts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath

from .catalogs import profile_fingerprint, validate_portable_relative_path
from .io import private_fs
from .io.atomic import (
    atomic_create_bytes,
    atomic_write_bytes,
    atomic_write_json,
)
from .io.managed_files import remove_persistent_identity_bound_path
from .io.metadata import MetadataFileError, read_bounded_regular_file
from .receipts import (
    ComponentReceipt,
    HEX64,
    InventoryEntry,
    ReceiptError,
    inspect_receipt,
    parse_legacy_nquake_receipt,
    parse_inventory,
    serialize_component_receipt,
    serialize_inventory,
    validate_receipt_inventory,
)
from .state import (
    CURRENT_STATE_FORMAT,
    INSTALLATION_PROFILES,
    InstallState,
    StateError,
    installation_version,
    parse_install_state,
    serialize_install_state,
)


def _replace_component_ids(
    values: tuple[str, ...],
    *,
    replacements: Mapping[str, str],
    removals: frozenset[str],
) -> list[str]:
    migrated: list[str] = []
    for identifier in values:
        if identifier in removals:
            continue
        current = replacements.get(identifier, identifier)
        if current not in migrated:
            migrated.append(current)
    return migrated


def migrate_install_state(
    state: InstallState,
    *,
    replacements: Mapping[str, str],
    removals: Iterable[str],
    allowed_profiles: Iterable[str],
    allowed_capabilities: Iterable[str],
    target_version: str | None = None,
) -> InstallState:
    """Return the current state shape without mutating the accepted source."""

    removed = frozenset(removals)
    document = state.to_document()
    marker = installation_version(document)
    requested = _replace_component_ids(
        state.requested_components,
        replacements=replacements,
        removals=removed,
    )
    recorded = _replace_component_ids(
        state.recorded_components,
        replacements=replacements,
        removals=removed,
    )
    known = _replace_component_ids(
        state.known_components,
        replacements=replacements,
        removals=removed,
    )
    document.update({
        "format": CURRENT_STATE_FORMAT,
        "requested_components": requested,
        "recorded_components": recorded,
        "known_components": known,
        "capabilities": list(state.capabilities),
        "component_fingerprint": profile_fingerprint(recorded),
    })
    # Older state writers used ``version`` or ``installer_version``.  Keep
    # reading those aliases for compatibility, but emit exactly one canonical
    # marker so adding the new target does not leave duplicate version fields
    # that the strict parser must reject on the next run.
    document.pop("version", None)
    document.pop("installer_version", None)
    if target_version is not None:
        document["installation_version"] = target_version
    elif marker is not None:
        document["installation_version"] = marker
    return parse_install_state(
        document,
        allowed_profiles=allowed_profiles,
        allowed_capabilities=allowed_capabilities,
    )


class MigrationError(ValueError):
    """A migration cannot be planned or safely applied."""


class MigrationExecutionError(MigrationError):
    """A phase failed; the operation was rolled back when possible."""

    def __init__(
        self,
        message: str,
        *,
        phase: "MigrationPhase",
        rolled_back: bool,
        committed: bool = False,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.rolled_back = rolled_back
        self.committed = committed
        self.cause = cause


class MigrationPhase(str, Enum):
    PREFLIGHT = "preflight"
    STAGE = "stage"
    VERIFY = "verify"
    COMMIT = "commit"
    FINALIZE = "finalize"


@dataclass(frozen=True)
class MigrationSource:
    """Evidence collected from a historical installation before any write."""

    version: str | None
    family: str | None
    state_format: int | None
    managed_paths: tuple[str, ...]
    preserved_paths: tuple[str, ...]
    # Authenticated markers are kept separately from the caller's optional
    # source override.  A fixture may provide an explicit version when no
    # receipt/state marker exists, but an override must never replace a
    # contradictory marker already present in the installation.
    cli_versions: tuple[str, ...] = ()
    state_version: str | None = None


@dataclass(frozen=True)
class MigrationOperation:
    """One ownership-bound file conversion in a migration plan."""

    key: str
    phase: MigrationPhase
    source: str
    destination: str
    kind: str
    owner: str
    expected_size: int
    expected_sha256: str
    source_sha256: str = ""
    payload: bytes = field(default=b"", repr=False, compare=False)
    source_identity: tuple[int, int] | None = field(default=None, compare=False)

    def to_document(self) -> dict[str, object]:
        return {
            "key": self.key,
            "phase": self.phase.value,
            "source": self.source,
            "destination": self.destination,
            "kind": self.kind,
            "owner": self.owner,
            "expected_size": self.expected_size,
            "expected_sha256": self.expected_sha256,
        }


@dataclass(frozen=True)
class MigrationConflict:
    code: str
    path: str
    detail: str

    def to_document(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "detail": self.detail}


@dataclass(frozen=True)
class MigrationPlan:
    """An immutable, zero-write migration plan suitable for dry-run output."""

    root: Path
    source: MigrationSource
    target_version: str
    operations: tuple[MigrationOperation, ...]
    preserved_paths: tuple[str, ...]
    conflicts: tuple[MigrationConflict, ...] = ()
    dry_run: bool = True
    snapshot: tuple[tuple[str, str, int, str], ...] = ()
    retired_components: tuple[str, ...] = ()

    @property
    def executable(self) -> bool:
        return not self.conflicts

    @property
    def blocked(self) -> bool:
        return bool(self.conflicts)

    @property
    def changed(self) -> bool:
        return bool(self.operations)

    @property
    def phases(self) -> tuple[MigrationPhase, ...]:
        """Stable execution order exposed to CLI/dry-run renderers."""

        return tuple(MigrationPhase)

    def to_document(self) -> dict[str, object]:
        return {
            "source_version": self.source.version,
            "source_family": self.source.family,
            "target_version": self.target_version,
            "dry_run": self.dry_run,
            "phases": [phase.value for phase in self.phases],
            "operations": [operation.to_document() for operation in self.operations],
            "preserved_paths": list(self.preserved_paths),
            "retired_components": list(self.retired_components),
            "conflicts": [conflict.to_document() for conflict in self.conflicts],
        }


@dataclass(frozen=True)
class MigrationResult:
    """Result of applying a plan; inverse material remains private and live."""

    plan: MigrationPlan
    status: str
    applied_operations: tuple[str, ...] = ()
    preserved_paths: tuple[str, ...] = ()
    _rollback_records: tuple["_RollbackRecord", ...] = field(
        default=(), repr=False, compare=False,
    )

    def rollback(self) -> None:
        rollback_migration(self)

    def to_document(self) -> dict[str, object]:
        return {
            "status": self.status,
            "applied_operations": list(self.applied_operations),
            "preserved_paths": list(self.preserved_paths),
            "plan": self.plan.to_document(),
        }


_SOURCE_VERSION = re.compile(
    r"^0\.(7|8|9)(?:\.(\d+)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"|\.x)$"
)
_TARGET_VERSION = re.compile(
    r"^1\.0(?:\.0)?(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_COMPONENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")
_CLIENT_RECEIPT = re.compile(r"^ezquake-([A-Za-z0-9_.+-]+)-(stable|nightly)\.receipt$")
_SAFE_MIGRATION_BYTES = 16 * 1024 * 1024
# Historical identifiers are part of the runtime migration contract.  Keep
# this small table here instead of importing the maintenance manager.
LEGACY_COMPONENT_REPLACEMENTS = {"nquake-ktx": "ktx"}
LEGACY_COMPONENT_REMOVALS = frozenset({"nquake-sounds"})
_JOURNAL_FORMAT = 1
_JOURNAL_MAX_BYTES = 1024 * 1024
_JOURNAL_TRANSACTION = re.compile(r"^tx-[0-9a-f]{24}$")
_JOURNAL_KINDS = frozenset({
    "rewrite-state",
    "move-receipt",
    "move-inventory",
    "retire-duplicate",
    # Journals emitted before strict destination ownership was added may still
    # need to be recovered after a process crash.
    "finalize-duplicate",
})
_JOURNAL_OPERATION_STATUSES = frozenset({
    "backed-up", "staged", "verified", "commit-start", "committed",
    "finalize-start", "finalized",
})
_JOURNAL_TERMINAL_STATUSES = frozenset({"complete", "rolled_back"})
_JOURNAL_CLEANUP_STATUS = "cleanup-pending"


@dataclass(frozen=True)
class _RollbackRecord:
    source: str
    destination: str
    previous_source: bytes | None
    previous_destination: bytes | None
    destination_identity: tuple[int, int] | None
    destination_sha256: str
    kind: str = ""


@dataclass(frozen=True)
class PendingMigration:
    """One unfinished persistent migration transaction."""

    root: Path
    directory: Path
    journal: Path
    transaction_id: str
    document: dict[str, object] | None
    detail: str = ""
    # These observations deliberately live outside the journal document.  A
    # cleanup retry must not trust an inode copied into a replacement journal;
    # the identities are captured by the inspecting process and rechecked
    # immediately before each identity-bound removal.
    journal_identity: tuple[int, int] | None = None
    journal_sha256: str | None = None

    @property
    def valid(self) -> bool:
        return self.document is not None and not self.detail


@dataclass(frozen=True)
class _CleanupEntry:
    """One authenticated private object that may be removed during cleanup."""

    path: Path
    identity: tuple[int, int]
    directory: bool
    sha256: str | None = None
    expected_size: int | None = None


@dataclass(frozen=True)
class _CleanupPlan:
    """Closed allowlist and identities for one cleanup-pending transaction."""

    entries: tuple[_CleanupEntry, ...]


@dataclass(frozen=True)
class _ComponentMetadataCandidate:
    """One validated legacy receipt/inventory pair and its target identity."""

    source_component: str
    target_component: str
    receipt_path: Path
    inventory_path: Path
    receipt_payload: bytes
    inventory_payload: bytes
    normalized_receipt_payload: bytes
    normalized_inventory_payload: bytes


def _version_family(version: str | None) -> str | None:
    if version is None:
        return None
    match = _SOURCE_VERSION.fullmatch(version)
    if match is not None:
        return f"0.{match.group(1)}.x"
    if _TARGET_VERSION.fullmatch(version):
        return "1.0.x"
    return None


def _normalize_target(version: str) -> str:
    if not isinstance(version, str) or not _TARGET_VERSION.fullmatch(version):
        raise MigrationError(f"unsupported migration target: {version}")
    return "1.0.0" if version == "1.0" else version


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _safe_payload(root: Path, path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise MigrationError(f"migration source unavailable: {_rel(root, path)}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise MigrationError(f"migration source is not a regular file: {_rel(root, path)}")
    try:
        return read_bounded_regular_file(path, maximum_size=_SAFE_MIGRATION_BYTES)
    except MetadataFileError as error:
        raise MigrationError(f"migration source is unsafe: {_rel(root, path)}") from error


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise MigrationError("journal path is not a safe relative POSIX path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise MigrationError("journal path escapes the installation root")
    normalized = pure.as_posix()
    if normalized != value:
        raise MigrationError("journal path is not canonical")
    return normalized


def _migration_store(root: Path) -> Path:
    return root / ".x86qw" / "migrations" / "1.0"


def _journal_child(root: Path, relative: object) -> Path:
    value = _safe_relative(relative)
    candidate = root / value
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise MigrationError("journal path escapes the installation root") from error
    return candidate


def _optional_payload(root: Path, path: Path) -> tuple[bytes | None, tuple[int, int] | None]:
    if not path.exists() and not path.is_symlink():
        return None, None
    payload = _safe_payload(root, path)
    metadata = path.lstat()
    return payload, (int(metadata.st_dev), int(metadata.st_ino))


def _remove_private_tree(path: Path) -> None:
    """Remove a journal/staging tree without following a replacement link."""

    if not path.exists() and not path.is_symlink():
        return
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise MigrationError(f"migration journal path is unsafe: {path}")
    for child in sorted(path.iterdir(), key=lambda item: item.name, reverse=True):
        child_metadata = child.lstat()
        if stat.S_ISLNK(child_metadata.st_mode):
            raise MigrationError(f"migration journal child is a symlink: {child}")
        if stat.S_ISDIR(child_metadata.st_mode):
            _remove_private_tree(child)
        elif stat.S_ISREG(child_metadata.st_mode):
            private_fs.unlink_private_file(
                child,
                expected_identity=(int(child_metadata.st_dev), int(child_metadata.st_ino)),
            )
        else:
            raise MigrationError(f"migration journal child has an unsafe type: {child}")
    path.rmdir()


def _persist_migration_journal(path: Path, document: dict[str, object]) -> None:
    """Durably publish one bounded, owner-only journal checkpoint."""

    atomic_write_json(path, document, private=True)


@dataclass
class _JournalContext:
    root: Path
    directory: Path
    journal: Path
    backup: Path
    stage: Path
    document: dict[str, object]


def _journal_operation(
    root: Path,
    operation: MigrationOperation,
    index: int,
    backup: Path,
) -> dict[str, object]:
    source = root / operation.source
    destination = root / operation.destination
    source_payload, source_identity = _optional_payload(root, source)
    if source_payload is None:
        raise MigrationError(f"migration source unavailable: {operation.source}")
    if operation.source_identity is not None and source_identity != operation.source_identity:
        raise MigrationError(f"migration source identity changed: {operation.source}")
    if _digest(source_payload) != operation.source_sha256:
        raise MigrationError(f"migration source bytes changed: {operation.source}")
    destination_payload, destination_identity = _optional_payload(root, destination)
    source_backup = backup / f"source-{index}.bin"
    atomic_write_bytes(source_backup, source_payload, mode=0o600)
    destination_backup_name: str | None = None
    if destination_payload is not None:
        destination_backup_name = f"destination-{index}.bin"
        atomic_write_bytes(backup / destination_backup_name, destination_payload, mode=0o600)
    return {
        "key": operation.key,
        "phase": operation.phase.value,
        "source": operation.source,
        "destination": operation.destination,
        "kind": operation.kind,
        "expected_size": operation.expected_size,
        "expected_sha256": operation.expected_sha256,
        "source_sha256": operation.source_sha256,
        "source_identity": list(source_identity) if source_identity is not None else None,
        "destination_identity": (
            list(destination_identity) if destination_identity is not None else None
        ),
        "destination_after_identity": None,
        "source_backup": _rel(root, source_backup),
        "destination_backup": (
            _rel(root, backup / destination_backup_name)
            if destination_backup_name is not None else None
        ),
        "destination_before_sha256": (
            _digest(destination_payload) if destination_payload is not None else None
        ),
        "status": "backed-up",
    }


def _create_migration_journal(plan: MigrationPlan) -> _JournalContext:
    metadata = plan.root / ".x86qw"
    if metadata.is_symlink() or not metadata.is_dir():
        raise MigrationError("migration metadata root is not a private directory")
    private_fs.ensure_private_directory(metadata)
    store = _migration_store(plan.root)
    private_fs.ensure_private_directories(store, stop=metadata)
    directory = private_fs.private_mkdtemp(directory=store, prefix="tx-")
    backup = directory / "backup"
    stage = directory / "stage"
    try:
        private_fs.create_private_directory(backup)
        private_fs.create_private_directory(stage)
        operations = [
            _journal_operation(plan.root, operation, index, backup)
            for index, operation in enumerate(plan.operations)
        ]
        document: dict[str, object] = {
            "format": _JOURNAL_FORMAT,
            "project": "x86qw",
            "transaction_id": directory.name,
            "target_version": plan.target_version,
            "phase": MigrationPhase.PREFLIGHT.value,
            "status": "prepared",
            "operations": operations,
        }
        journal = directory / "journal.json"
        _persist_migration_journal(journal, document)
    except BaseException:
        try:
            _remove_private_tree(directory)
        except BaseException:
            pass
        raise
    return _JournalContext(plan.root, directory, journal, backup, stage, document)


def _journal_checkpoint(
    context: _JournalContext,
    phase: MigrationPhase,
    *,
    operation_index: int | None = None,
    operation_status: str | None = None,
) -> None:
    context.document["phase"] = phase.value
    if operation_index is not None and operation_status is not None:
        operations = context.document.get("operations")
        if not isinstance(operations, list) or operation_index >= len(operations):
            raise MigrationError("migration journal operation checkpoint is invalid")
        operation = operations[operation_index]
        if not isinstance(operation, dict):
            raise MigrationError("migration journal operation checkpoint is invalid")
        operation["status"] = operation_status
    _persist_migration_journal(context.journal, context.document)


def _journal_identity(value: object) -> tuple[int, int] | None:
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(item) is not int or item < 0 for item in value)
    ):
        raise MigrationError("journal file identity is invalid")
    return int(value[0]), int(value[1])


def _journal_backup_path(
    root: Path,
    directory: Path,
    relative: object,
) -> Path:
    path = _journal_child(root, relative)
    backup_root = directory / "backup"
    try:
        path.relative_to(backup_root)
    except ValueError as error:
        raise MigrationError("journal backup escapes its transaction directory") from error
    if path.parent != backup_root:
        raise MigrationError("journal backup nesting is invalid")
    return path


def _validate_pending_document(
    root: Path,
    directory: Path,
    journal: Path,
    document: object,
) -> tuple[dict[str, object] | None, str]:
    if not isinstance(document, dict):
        return None, "journal document is not an object"
    if (
        type(document.get("format")) is not int
        or document.get("format") != _JOURNAL_FORMAT
        or document.get("project") != "x86qw"
        or document.get("transaction_id") != directory.name
        or not _JOURNAL_TRANSACTION.fullmatch(directory.name)
        or not isinstance(document.get("target_version"), str)
        or document.get("phase") not in {
            *(phase.value for phase in MigrationPhase),
            "rollback",
        }
        or document.get("status") not in {
            "prepared", "complete", "rolled_back", _JOURNAL_CLEANUP_STATUS,
        }
        or not isinstance(document.get("operations"), list)
    ):
        return None, "journal identity or status is invalid"
    if document.get("status") in _JOURNAL_TERMINAL_STATUSES:
        return document, ""
    operations = document["operations"]
    if document.get("status") == _JOURNAL_CLEANUP_STATUS:
        if document.get("phase") != MigrationPhase.FINALIZE.value:
            return None, "cleanup-pending journal is not finalized"
        for operation in operations:
            if not isinstance(operation, dict):
                return None, "journal operation is not an object"
            kind = operation.get("kind")
            status = operation.get("status")
            if kind in {"move-receipt", "move-inventory", "retire-duplicate", "finalize-duplicate"}:
                if status != "finalized":
                    return None, "cleanup-pending journal has an unfinished finalize"
            elif kind == "rewrite-state" and status != "committed":
                return None, "cleanup-pending journal has an uncommitted rewrite"
    for operation in operations:
        if not isinstance(operation, dict):
            return None, "journal operation is not an object"
        try:
            source = _safe_relative(operation.get("source"))
            destination = _safe_relative(operation.get("destination"))
            source_backup = _safe_relative(operation.get("source_backup"))
            destination_backup = operation.get("destination_backup")
            if destination_backup is not None:
                destination_backup = _safe_relative(destination_backup)
            _journal_identity(operation.get("source_identity"))
            _journal_identity(operation.get("destination_identity"))
            _journal_identity(operation.get("destination_after_identity"))
        except MigrationError as error:
            return None, str(error)
        if (
            operation.get("source") != source
            or operation.get("destination") != destination
            or operation.get("source_backup") != source_backup
            or operation.get("destination_backup") != destination_backup
            or operation.get("kind") not in _JOURNAL_KINDS
            or operation.get("phase") not in {phase.value for phase in MigrationPhase}
            or type(operation.get("expected_size")) is not int
            or operation["expected_size"] < 0
            or operation["expected_size"] > _SAFE_MIGRATION_BYTES
            or not isinstance(operation.get("expected_sha256"), str)
            or not HEX64.fullmatch(operation["expected_sha256"])
            or not isinstance(operation.get("source_sha256"), str)
            or not HEX64.fullmatch(operation["source_sha256"])
            or operation.get("status") not in _JOURNAL_OPERATION_STATUSES
            or (destination_backup is None and operation.get("destination_before_sha256") is not None)
            or (
                destination_backup is not None
                and (
                    not isinstance(operation.get("destination_before_sha256"), str)
                    or not HEX64.fullmatch(operation["destination_before_sha256"])
                )
            )
            or (source == destination and operation.get("kind") not in {
                "rewrite-state", "retire-duplicate", "finalize-duplicate",
            })
            or (operation.get("kind") == "retire-duplicate" and source != destination)
        ):
            return None, "journal operation contract is invalid"
        try:
            source_backup_path = _journal_backup_path(root, directory, source_backup)
            source_backup_present = (
                source_backup_path.exists() or source_backup_path.is_symlink()
            )
            if source_backup_present:
                source_backup_payload = private_fs.read_private_file(
                    source_backup_path, maximum_size=_SAFE_MIGRATION_BYTES,
                )
                if _digest(source_backup_payload) != operation["source_sha256"]:
                    return None, "journal source backup hash is invalid"
            elif document.get("status") != _JOURNAL_CLEANUP_STATUS:
                return None, "journal source backup is unavailable"
            if destination_backup is not None:
                destination_backup_path = _journal_backup_path(root, directory, destination_backup)
                destination_backup_present = (
                    destination_backup_path.exists()
                    or destination_backup_path.is_symlink()
                )
                if destination_backup_present:
                    destination_backup_payload = private_fs.read_private_file(
                        destination_backup_path, maximum_size=_SAFE_MIGRATION_BYTES,
                    )
                    expected_destination = operation.get("destination_before_sha256")
                    if (
                        not isinstance(expected_destination, str)
                        or _digest(destination_backup_payload) != expected_destination
                    ):
                        return None, "journal destination backup hash is invalid"
                elif document.get("status") != _JOURNAL_CLEANUP_STATUS:
                    return None, "journal destination backup is unavailable"
        except (MigrationError, OSError) as error:
            return None, str(error)
    return document, ""


def inspect_pending_migration(
    root: os.PathLike[str] | str,
) -> PendingMigration | None:
    """Inspect one unfinished migration journal without changing the filesystem."""

    root = Path(root)
    try:
        root_mode = root.lstat().st_mode
    except OSError:
        return None
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        return None
    metadata = root / ".x86qw"
    try:
        metadata_mode = metadata.lstat().st_mode
    except OSError:
        return None
    if stat.S_ISLNK(metadata_mode) or not stat.S_ISDIR(metadata_mode):
        return None
    store = _migration_store(root)
    if not store.exists() and not store.is_symlink():
        return None
    try:
        store_metadata = store.lstat()
    except OSError as error:
        return PendingMigration(root, store, store / "journal.json", "", None, str(error))
    if stat.S_ISLNK(store_metadata.st_mode) or not stat.S_ISDIR(store_metadata.st_mode):
        return PendingMigration(
            root, store, store / "journal.json", "", None,
            "migration journal root is not a private directory",
        )
    entries = sorted(store.iterdir(), key=lambda item: item.name)
    for directory in entries:
        metadata = directory.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            return PendingMigration(
                root, directory, directory / "journal.json", directory.name, None,
                "migration transaction path is unsafe",
            )
        journal = directory / "journal.json"
        if not journal.exists() or journal.is_symlink():
            return PendingMigration(
                root, directory, journal, directory.name, None,
                "migration transaction journal is missing or unsafe",
            )
        try:
            payload = private_fs.read_private_file(journal, maximum_size=_JOURNAL_MAX_BYTES)
            document = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            return PendingMigration(root, directory, journal, directory.name, None, str(error))
        try:
            journal_metadata = journal.lstat()
        except OSError as error:
            return PendingMigration(root, directory, journal, directory.name, None, str(error))
        journal_identity = (
            int(journal_metadata.st_dev), int(journal_metadata.st_ino),
        )
        journal_sha256 = _digest(payload)
        validated, detail = _validate_pending_document(root, directory, journal, document)
        if detail:
            return PendingMigration(
                root, directory, journal, directory.name, None, detail,
                journal_identity, journal_sha256,
            )
        if validated is not None and validated.get("status") in _JOURNAL_TERMINAL_STATUSES:
            continue
        return PendingMigration(
            root, directory, journal, directory.name, validated, "",
            journal_identity, journal_sha256,
        )
    return None


def _read_journal_backup(root: Path, pending: PendingMigration, relative: object) -> bytes:
    path = _journal_child(root, relative)
    try:
        return private_fs.read_private_file(path, maximum_size=_SAFE_MIGRATION_BYTES)
    except OSError as error:
        raise MigrationError(f"migration journal backup is unavailable: {relative}") from error


def _current_payload(root: Path, path: Path) -> tuple[bytes | None, tuple[int, int] | None]:
    return _optional_payload(root, path)


def _remove_if_owned(root: Path, path: Path, expected_sha256: str) -> None:
    payload, identity = _current_payload(root, path)
    if payload is None:
        return
    if _digest(payload) != expected_sha256 or identity is None:
        raise MigrationError(f"migration recovery found changed path: {_rel(root, path)}")
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or (
        int(metadata.st_dev), int(metadata.st_ino)
    ) != identity:
        raise MigrationError(f"migration recovery found changed path: {_rel(root, path)}")
    if not remove_persistent_identity_bound_path(path, identity, directory=False):
        raise MigrationError(f"migration recovery found changed path: {_rel(root, path)}")


def _private_path_identity(path: Path, *, directory: bool) -> tuple[int, int]:
    """Capture one no-follow persistent identity for a private path."""

    try:
        metadata = path.lstat()
    except OSError as error:
        raise MigrationError(f"migration cleanup path is unavailable: {path}") from error
    if stat.S_ISLNK(metadata.st_mode) or (
        stat.S_ISDIR(metadata.st_mode) != directory
        or stat.S_ISREG(metadata.st_mode) == directory
    ):
        raise MigrationError(f"migration cleanup path has an unsafe type: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise MigrationError(f"migration cleanup path cannot be opened: {path}") from error
    try:
        opened = os.fstat(descriptor)
        if stat.S_ISLNK(opened.st_mode) or (
            stat.S_ISDIR(opened.st_mode) != directory
            or stat.S_ISREG(opened.st_mode) == directory
            or (
                int(opened.st_dev), int(opened.st_ino)
            ) != (int(metadata.st_dev), int(metadata.st_ino))
        ):
            raise MigrationError(f"migration cleanup path changed identity: {path}")
        try:
            return private_fs.persistent_descriptor_identity(
                descriptor, directory=directory,
            )
        except OSError as error:
            raise MigrationError(f"migration cleanup path has no stable identity: {path}") from error
    finally:
        os.close(descriptor)


def _capture_cleanup_file(
    path: Path,
    *,
    expected_sha256: str,
    expected_size: int | None = None,
    required: bool = False,
) -> _CleanupEntry | None:
    """Authenticate one optional private cleanup file and capture its identity."""

    if not path.exists() and not path.is_symlink():
        if required:
            raise MigrationError(f"migration cleanup file is unavailable: {path}")
        return None
    try:
        payload = private_fs.read_private_file(
            path, maximum_size=_SAFE_MIGRATION_BYTES,
        )
    except OSError as error:
        raise MigrationError(f"migration cleanup file is unsafe: {path}") from error
    if expected_size is not None and len(payload) != expected_size:
        raise MigrationError(f"migration cleanup file size changed: {path}")
    if _digest(payload) != expected_sha256:
        raise MigrationError(f"migration cleanup file hash changed: {path}")
    identity = _private_path_identity(path, directory=False)
    return _CleanupEntry(
        path=path,
        identity=identity,
        directory=False,
        sha256=expected_sha256,
        expected_size=expected_size,
    )


def _capture_cleanup_directory(
    path: Path,
    *,
    required: bool = False,
) -> _CleanupEntry | None:
    """Authenticate one optional private cleanup directory and capture identity."""

    if not path.exists() and not path.is_symlink():
        if required:
            raise MigrationError(f"migration cleanup directory is unavailable: {path}")
        return None
    try:
        private_fs.validate_private_directory(path)
    except OSError as error:
        raise MigrationError(f"migration cleanup directory is unsafe: {path}") from error
    return _CleanupEntry(
        path=path,
        identity=_private_path_identity(path, directory=True),
        directory=True,
    )


def _assert_cleanup_directory_current(entry: _CleanupEntry) -> None:
    """Recheck one private directory before traversing or removing below it."""

    if not entry.directory:
        raise MigrationError("migration cleanup parent entry is not a directory")
    if not entry.path.exists() and not entry.path.is_symlink():
        return
    try:
        private_fs.validate_private_directory(entry.path)
    except OSError as error:
        raise MigrationError(
            f"migration cleanup directory changed: {entry.path}"
        ) from error
    if _private_path_identity(entry.path, directory=True) != entry.identity:
        raise MigrationError(f"migration cleanup directory changed: {entry.path}")


def _cleanup_children(
    path: Path,
    *,
    parent_entry: _CleanupEntry | None = None,
) -> dict[str, Path]:
    """List one already-authenticated private directory without following links."""

    if parent_entry is not None:
        _assert_cleanup_directory_current(parent_entry)
    try:
        children = tuple(path.iterdir())
    except OSError as error:
        raise MigrationError(f"migration cleanup directory cannot be listed: {path}") from error
    result: dict[str, Path] = {}
    for child in children:
        if child.name in result:
            raise MigrationError(f"migration cleanup directory has duplicate child: {child}")
        result[child.name] = child
    return result


def _validate_cleanup_tree(root: Path, pending: PendingMigration) -> _CleanupPlan:
    """Build a closed, identity-bound allowlist for cleanup-pending recovery."""

    document = pending.document
    if not isinstance(document, dict) or document.get("status") != _JOURNAL_CLEANUP_STATUS:
        raise MigrationError("migration cleanup journal has an invalid status")
    operations = document.get("operations")
    if not isinstance(operations, list):
        raise MigrationError("migration cleanup journal operations are invalid")

    transaction = pending.directory
    transaction_entry = _capture_cleanup_directory(transaction, required=True)
    assert transaction_entry is not None
    top_children = _cleanup_children(transaction, parent_entry=transaction_entry)
    allowed_top = {"journal.json"}
    if "backup" in top_children:
        allowed_top.add("backup")
    if "stage" in top_children:
        allowed_top.add("stage")
    unexpected_top = set(top_children) - allowed_top
    if unexpected_top:
        raise MigrationError(
            "migration cleanup transaction contains unowned paths: "
            + ", ".join(sorted(unexpected_top))
        )

    journal = transaction / "journal.json"
    if pending.journal != journal:
        raise MigrationError("migration cleanup journal path is not canonical")
    journal_entry = _capture_cleanup_file(
        journal,
        expected_sha256=pending.journal_sha256 or "",
        required=True,
    )
    assert journal_entry is not None
    journal_metadata = journal.lstat()
    if (
        pending.journal_identity is not None
        and (
            int(journal_metadata.st_dev), int(journal_metadata.st_ino)
        ) != pending.journal_identity
    ):
        raise MigrationError("migration cleanup journal changed identity")

    backup = transaction / "backup"
    stage = transaction / "stage"
    backup_entry = _capture_cleanup_directory(backup)
    stage_entry = _capture_cleanup_directory(stage)
    backup_children = (
        _cleanup_children(backup, parent_entry=backup_entry)
        if backup_entry is not None else {}
    )
    stage_children = (
        _cleanup_children(stage, parent_entry=stage_entry)
        if stage_entry is not None else {}
    )

    expected_backups: dict[str, tuple[str, int | None]] = {}
    expected_stages: dict[str, tuple[str, int | None]] = {}
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise MigrationError("migration cleanup operation is invalid")
        source_backup = _journal_backup_path(root, transaction, operation.get("source_backup"))
        if source_backup.parent != backup:
            raise MigrationError("migration cleanup source backup is not direct")
        if source_backup.name in expected_backups:
            raise MigrationError("migration cleanup journal reuses a backup path")
        expected_backups[source_backup.name] = (
            str(operation["source_sha256"]), None,
        )
        destination_backup = operation.get("destination_backup")
        if destination_backup is not None:
            destination_path = _journal_backup_path(root, transaction, destination_backup)
            if destination_path.parent != backup:
                raise MigrationError("migration cleanup destination backup is not direct")
            if destination_path.name in expected_backups:
                raise MigrationError("migration cleanup journal reuses a backup path")
            expected_backups[destination_path.name] = (
                str(operation["destination_before_sha256"]), None,
            )
        expected_stages[str(index)] = (
            str(operation["expected_sha256"]), int(operation["expected_size"]),
        )

    unexpected_backups = set(backup_children) - set(expected_backups)
    if unexpected_backups:
        raise MigrationError(
            "migration cleanup backup contains unowned paths: "
            + ", ".join(sorted(unexpected_backups))
        )
    unexpected_stages = set(stage_children) - set(expected_stages)
    if unexpected_stages:
        raise MigrationError(
            "migration cleanup staging contains unowned paths: "
            + ", ".join(sorted(unexpected_stages))
        )

    files: list[_CleanupEntry] = []
    for name, (expected_sha256, _expected_size) in sorted(expected_backups.items()):
        entry = _capture_cleanup_file(
            backup / name,
            expected_sha256=expected_sha256,
        )
        if entry is not None:
            files.append(entry)
    for name, (expected_sha256, expected_size) in sorted(expected_stages.items()):
        entry = _capture_cleanup_file(
            stage / name,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )
        if entry is not None:
            files.append(entry)

    directories = [
        entry for entry in (stage_entry, backup_entry)
        if entry is not None
    ]
    # Remove only the authenticated files and directories.  The journal is
    # intentionally last so a partial cleanup remains discoverable and
    # retryable; the transaction root is removed only after it is empty.
    return _CleanupPlan(tuple(
        files + directories + [journal_entry, transaction_entry]
    ))


def _remove_cleanup_entry(entry: _CleanupEntry) -> None:
    """Remove one cleanup entry only if type, identity and bytes still match."""

    if not entry.path.exists() and not entry.path.is_symlink():
        return
    if entry.directory:
        _assert_cleanup_directory_current(entry)
        if not remove_persistent_identity_bound_path(
            entry.path, entry.identity, directory=True,
        ):
            raise MigrationError(f"migration cleanup directory changed: {entry.path}")
        return
    try:
        payload = private_fs.read_private_file(
            entry.path, maximum_size=_SAFE_MIGRATION_BYTES,
        )
    except OSError as error:
        raise MigrationError(f"migration cleanup file changed: {entry.path}") from error
    if (
        entry.expected_size is not None
        and len(payload) != entry.expected_size
    ) or (
        entry.sha256 is not None and _digest(payload) != entry.sha256
    ):
        raise MigrationError(f"migration cleanup file changed: {entry.path}")
    identity = _private_path_identity(entry.path, directory=False)
    if identity != entry.identity:
        raise MigrationError(f"migration cleanup file changed: {entry.path}")
    if not remove_persistent_identity_bound_path(
        entry.path, entry.identity, directory=False,
    ):
        raise MigrationError(f"migration cleanup file changed: {entry.path}")


def _remove_cleanup_tree(plan: _CleanupPlan) -> None:
    """Apply a prevalidated cleanup allowlist without recursively discovering files."""

    for entry in plan.entries:
        # Revalidate every parent directory before touching a child.  This
        # closes the replacement/symlink window between the allowlist scan and
        # the identity-bound unlink/rmdir operation.
        for parent in plan.entries:
            if parent.directory:
                _assert_cleanup_directory_current(parent)
        _remove_cleanup_entry(entry)


def recover_migration(root: os.PathLike[str] | str) -> bool:
    """Rollback an unfinished migration journal, failing closed on divergence."""

    root = Path(root)
    pending = inspect_pending_migration(root)
    if pending is None:
        return False
    if not pending.valid or pending.document is None:
        raise MigrationError(pending.detail or "migration journal is invalid")
    document = pending.document
    if document.get("status") == _JOURNAL_CLEANUP_STATUS:
        # All destination bytes and legacy removals are already committed.  A
        # cleanup-pending journal is a housekeeping retry, never a rollback
        # transaction.  Build a closed allowlist before removing anything so
        # a replacement directory or an extra private file is preserved and
        # reported instead of being recursively deleted.
        cleanup_plan = _validate_cleanup_tree(root, pending)
        _remove_cleanup_tree(cleanup_plan)
        return True
    operations = document.get("operations")
    assert isinstance(operations, list)
    for operation in reversed(operations):
        assert isinstance(operation, dict)
        source = _journal_child(root, operation["source"])
        destination = _journal_child(root, operation["destination"])
        source_backup = _read_journal_backup(root, pending, operation["source_backup"])
        destination_backup_name = operation.get("destination_backup")
        destination_backup = (
            _read_journal_backup(root, pending, destination_backup_name)
            if destination_backup_name is not None else None
        )
        expected_sha256 = str(operation["expected_sha256"])
        source_sha256 = str(operation["source_sha256"])
        source_payload, source_identity = _current_payload(root, source)
        destination_payload, destination_identity = _current_payload(root, destination)
        status = str(operation.get("status"))
        source_before_identity = _journal_identity(operation.get("source_identity"))
        destination_before_identity = _journal_identity(operation.get("destination_identity"))
        destination_after_identity = _journal_identity(
            operation.get("destination_after_identity")
        )
        precommit_statuses = {"backed-up", "staged", "verified", "commit-start"}
        if status in precommit_statuses:
            if (
                source_payload is not None
                and source_before_identity is not None
                and source_identity != source_before_identity
            ):
                raise MigrationError(f"migration recovery found changed source: {operation['source']}")
            if (
                destination_payload is not None
                and (
                    destination_before_identity is None
                    or destination_identity != destination_before_identity
                )
            ):
                raise MigrationError(
                    f"migration recovery found changed destination: {operation['destination']}"
                )
        elif (
            destination_payload is not None
            and destination_after_identity is not None
            and destination_identity != destination_after_identity
        ):
            raise MigrationError(
                f"migration recovery found changed destination: {operation['destination']}"
            )
        if (
            source != destination
            and source_payload is not None
            and source_before_identity is not None
            and source_identity != source_before_identity
        ):
            raise MigrationError(f"migration recovery found changed source: {operation['source']}")
        if source == destination:
            if source_payload is None:
                if operation.get("kind") not in {"retire-duplicate", "finalize-duplicate"}:
                    raise MigrationError(f"migration recovery lost state path: {operation['source']}")
                _destination_parent_safe(root, source, [])
                atomic_create_bytes(source, source_backup)
                continue
            current_digest = _digest(source_payload)
            if current_digest not in {expected_sha256, _digest(source_backup)}:
                raise MigrationError(f"migration recovery found changed path: {operation['source']}")
            if current_digest == expected_sha256 and current_digest != _digest(source_backup):
                _destination_parent_safe(root, destination, [])
                atomic_write_bytes(destination, source_backup)
            continue
        if source_payload is not None and _digest(source_payload) != source_sha256:
            raise MigrationError(f"migration recovery found changed source: {operation['source']}")
        if destination_payload is not None and _digest(destination_payload) not in {
            expected_sha256,
            _digest(destination_backup) if destination_backup is not None else "",
        }:
            raise MigrationError(f"migration recovery found changed destination: {operation['destination']}")
        if destination_backup is None:
            _remove_if_owned(root, destination, expected_sha256)
        elif destination_payload is None:
            _destination_parent_safe(root, destination, [])
            atomic_create_bytes(destination, destination_backup)
        elif _digest(destination_payload) == expected_sha256:
            _destination_parent_safe(root, destination, [])
            atomic_write_bytes(destination, destination_backup)
        if source_payload is None:
            _destination_parent_safe(root, source, [])
            atomic_create_bytes(source, source_backup)
    document["phase"] = "rollback"
    document["status"] = "rolled_back"
    _persist_migration_journal(pending.journal, document)
    _remove_private_tree(pending.directory)
    return True


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, int, str], ...]:
    result: list[tuple[str, str, int, str]] = []
    if not root.exists():
        return ()
    for path in sorted(root.rglob("*")):
        relative = _rel(root, path)
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if stat.S_ISLNK(metadata.st_mode):
            result.append((relative, "symlink", 0, os.readlink(path)))
            continue
        if not stat.S_ISREG(metadata.st_mode):
            result.append((relative, "special", int(metadata.st_size), ""))
            continue
        # Snapshot identity is enough for preflight and avoids loading large
        # PAKs, demos, or logs merely to display a dry-run plan.
        if metadata.st_size <= _SAFE_MIGRATION_BYTES:
            payload = path.read_bytes()
            marker = _digest(payload)
        else:
            marker = (
                f"identity:{metadata.st_dev}:{metadata.st_ino}:"
                f"{metadata.st_mtime_ns}:{metadata.st_size}"
            )
        result.append((relative, "file", int(metadata.st_size), marker))
    return tuple(result)


def _operation(
    root: Path,
    *,
    key: str,
    source: Path,
    destination: Path,
    kind: str,
    payload: bytes,
    phase: MigrationPhase = MigrationPhase.COMMIT,
    source_payload: bytes | None = None,
) -> MigrationOperation:
    return MigrationOperation(
        key=key,
        phase=phase,
        source=_rel(root, source),
        destination=_rel(root, destination),
        kind=kind,
        owner="x86qw",
        expected_size=len(payload),
        expected_sha256=_digest(payload),
        source_sha256=_digest(payload if source_payload is None else source_payload),
        payload=payload,
        source_identity=(
            int(source.lstat().st_dev), int(source.lstat().st_ino)
        ),
    )


def _append_destination_conflict(
    conflicts: list[MigrationConflict],
    root: Path,
    destination: Path,
    *,
    detail: str | None = None,
) -> None:
    conflicts.append(MigrationConflict(
        "destination-occupied",
        _rel(root, destination),
        detail or "managed destination already exists without matching ownership evidence",
    ))


def _destination_parent_safe(
    root: Path,
    destination: Path,
    conflicts: list[MigrationConflict],
) -> bool:
    current = root
    try:
        parts = destination.relative_to(root).parts[:-1]
    except ValueError:
        conflicts.append(MigrationConflict(
            "unsafe-destination", str(destination), "destination escapes migration root",
        ))
        return False
    for part in parts:
        current /= part
        if not current.exists() and not current.is_symlink():
            continue
        try:
            metadata = current.lstat()
        except OSError as error:
            conflicts.append(MigrationConflict(
                "unsafe-destination", _rel(root, current), str(error),
            ))
            return False
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            conflicts.append(MigrationConflict(
                "unsafe-destination", _rel(root, current),
                "destination parent is not a private directory",
            ))
            return False
    return True


def _candidate_operation(
    root: Path,
    source: Path,
    destination: Path,
    *,
    key: str,
    kind: str,
    payload: bytes,
    conflicts: list[MigrationConflict],
    source_payload: bytes | None = None,
) -> MigrationOperation | None:
    if destination == source:
        return None
    if not _destination_parent_safe(root, destination, conflicts):
        return None
    if destination.exists() or destination.is_symlink():
        try:
            destination_payload = _safe_payload(root, destination)
        except MigrationError:
            _append_destination_conflict(conflicts, root, destination, detail="destination is unsafe")
            return None
        if destination_payload == payload:
            # Matching bytes do not prove that this pathname belongs to a
            # previous migration.  A pending journal is the only evidence that
            # can authenticate an interrupted promotion; without it, preserve
            # the collision and require an explicit recovery/inspection step.
            _append_destination_conflict(
                conflicts,
                root,
                destination,
                detail="destination has matching bytes but no migration ownership journal",
            )
            return None
        _append_destination_conflict(conflicts, root, destination)
        return None
    return _operation(
        root, key=key, source=source, destination=destination,
        kind=kind, payload=payload, source_payload=source_payload,
    )


def _normalize_component_receipt(
    receipt_payload: bytes,
    inventory_payload: bytes,
    *,
    source_component: str,
    target_component: str,
) -> tuple[bytes, bytes]:
    """Validate a legacy pair and emit a receipt with the target identity."""

    receipt, inventory = validate_receipt_inventory(
        receipt_payload,
        inventory_payload,
        component=source_component,
    )
    # Receipt parsing validates the digest and line shape, but inventory paths
    # are later consumed as managed filesystem paths.  Apply the same
    # cross-platform path boundary while the pair is still only a plan; a
    # migration must never publish metadata that the installer cannot safely
    # consume after the move.
    for entry in inventory:
        _validate_managed_inventory_path(entry)

    normalized_inventory_payload = inventory_payload
    if source_component != target_component:
        normalized_entries = tuple(
            InventoryEntry(
                (
                    f"qw/{target_component}.pk3"
                    if entry.path == f"qw/{source_component}.pk3"
                    else entry.path
                ),
                entry.sha256,
            )
            for entry in inventory
        )
        normalized_inventory_payload = serialize_inventory(normalized_entries)
    if source_component == target_component:
        return receipt_payload, normalized_inventory_payload
    normalized = ComponentReceipt(
        format=receipt.format,
        component=target_component,
        selection=receipt.selection,
        source=receipt.source,
        inventory_sha256=_digest(normalized_inventory_payload),
    )
    normalized_payload = serialize_component_receipt(normalized)
    # Revalidate the rewritten identity and the recalculated inventory binding
    # before it can enter an operation or a journal.
    validate_receipt_inventory(
        normalized_payload,
        normalized_inventory_payload,
        component=target_component,
    )
    return normalized_payload, normalized_inventory_payload


def _validate_managed_inventory_path(entry: InventoryEntry) -> None:
    """Apply the portable path boundary to one inventory entry."""

    try:
        validate_portable_relative_path(entry.path, "managed inventory path")
    except ValueError as error:
        raise ReceiptError(
            "managed inventory path is not portable",
            code="inventory_path",
            field_name=entry.path,
            value=entry.path,
        ) from error
    if entry.path in {"ezquake/configs/config.cfg", "ezquake/configs/preset.cfg"}:
        raise ReceiptError(
            "personal configuration cannot be managed",
            code="inventory_personal_path",
            field_name=entry.path,
            value=entry.path,
        )


def _validate_managed_inventory(inventory: Iterable[InventoryEntry]) -> None:
    """Validate every path in a legacy aggregate inventory."""

    for entry in inventory:
        _validate_managed_inventory_path(entry)


def _validate_canonical_client_receipts(
    root: Path,
    metadata: Path,
    conflicts: list[MigrationConflict],
) -> None:
    """Authenticate receipts already present in the canonical client tree."""

    clients_root = metadata / "clients" / "ezquake"
    if not clients_root.exists() and not clients_root.is_symlink():
        return
    try:
        clients_mode = clients_root.lstat().st_mode
    except OSError as error:
        conflicts.append(MigrationConflict(
            "unsafe-metadata", _rel(root, clients_root), str(error),
        ))
        return
    if stat.S_ISLNK(clients_mode) or not stat.S_ISDIR(clients_mode):
        conflicts.append(MigrationConflict(
            "unsafe-metadata", _rel(root, clients_root),
            "canonical client metadata root is not a private directory",
        ))
        return
    try:
        platform_roots = tuple(sorted(clients_root.iterdir()))
    except OSError as error:
        conflicts.append(MigrationConflict(
            "unsafe-metadata", _rel(root, clients_root), str(error),
        ))
        return
    for platform_root in platform_roots:
        try:
            platform_mode = platform_root.lstat().st_mode
        except OSError as error:
            conflicts.append(MigrationConflict(
                "unsafe-metadata", _rel(root, platform_root), str(error),
            ))
            continue
        if stat.S_ISLNK(platform_mode) or not stat.S_ISDIR(platform_mode):
            conflicts.append(MigrationConflict(
                "unsafe-metadata", _rel(root, platform_root),
                "canonical client platform path is not a private directory",
            ))
            continue
        try:
            receipt_paths = tuple(sorted(platform_root.iterdir()))
        except OSError as error:
            conflicts.append(MigrationConflict(
                "unsafe-metadata", _rel(root, platform_root), str(error),
            ))
            continue
        for receipt_path in receipt_paths:
            if not receipt_path.name.endswith(".receipt"):
                continue
            match = re.fullmatch(r"(stable|nightly)\.receipt", receipt_path.name)
            if match is None:
                conflicts.append(MigrationConflict(
                    "unknown-receipt", _rel(root, receipt_path),
                    "unrecognized canonical client receipt name",
                ))
                continue
            try:
                identity = inspect_receipt(_safe_payload(root, receipt_path))
                if (
                    identity.kind != "ezquake"
                    or identity.subject != platform_root.name
                    or identity.channel != match.group(1)
                ):
                    raise ReceiptError(
                        "canonical client receipt identity differs from its path"
                    )
            except (MigrationError, ReceiptError, OSError) as error:
                conflicts.append(MigrationConflict(
                    "corrupt-receipt", _rel(root, receipt_path), str(error),
                ))


def _retire_duplicate_operation(
    root: Path,
    candidate: _ComponentMetadataCandidate,
    *,
    kind: str,
) -> tuple[MigrationOperation, MigrationOperation]:
    """Build source==destination cleanup operations for duplicate metadata."""

    receipt = _operation(
        root,
        key=f"component:{candidate.target_component}:retire:{candidate.source_component}:receipt",
        source=candidate.receipt_path,
        destination=candidate.receipt_path,
        kind=kind,
        payload=candidate.receipt_payload,
    )
    inventory = _operation(
        root,
        key=f"component:{candidate.target_component}:retire:{candidate.source_component}:inventory",
        source=candidate.inventory_path,
        destination=candidate.inventory_path,
        kind=kind,
        payload=candidate.inventory_payload,
    )
    return receipt, inventory


def inspect_migration_source(
    root: os.PathLike[str] | str,
    *,
    source_version: str | None = None,
) -> MigrationSource:
    """Inspect historical metadata and classify untouched files."""

    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise MigrationError(f"migration root is not a directory: {root}")
    metadata = root / ".x86qw"
    try:
        metadata_mode = metadata.lstat().st_mode
    except OSError:
        metadata_safe = False
    else:
        metadata_safe = stat.S_ISDIR(metadata_mode) and not stat.S_ISLNK(metadata_mode)

    def metadata_parent_safe(path: Path) -> bool:
        current = metadata
        try:
            parts = path.relative_to(metadata).parts[:-1]
        except ValueError:
            return False
        for part in parts:
            current /= part
            try:
                current_mode = current.lstat().st_mode
            except OSError:
                return False
            if stat.S_ISLNK(current_mode) or not stat.S_ISDIR(current_mode):
                return False
        return True
    state_format: int | None = None
    state_version: str | None = None
    state_path = metadata / "state.json"
    if metadata_safe and state_path.is_file() and not state_path.is_symlink():
        try:
            payload = _safe_payload(root, state_path)
            # Historical state is intentionally parsed without changing it.
            document = __import__("json").loads(payload.decode("utf-8"))
            if isinstance(document, dict):
                state_format = document.get("format")
                try:
                    state_version = installation_version(document)
                except StateError:
                    state_version = None
        except (MigrationError, UnicodeDecodeError, ValueError):
            state_format = None
    cli_versions: list[str] = []
    if metadata_safe:
        for candidate in (metadata / "cli.receipt", metadata / "cli" / "receipt"):
            if (
                not metadata_parent_safe(candidate)
                or not candidate.is_file()
                or candidate.is_symlink()
            ):
                continue
            try:
                identity = inspect_receipt(_safe_payload(root, candidate))
            except (MigrationError, ReceiptError):
                continue
            if identity.kind == "cli" and identity.selection is not None:
                cli_versions.append(identity.selection)
    # Authenticated metadata wins over an optional API override.  The
    # override remains useful for synthetic fixtures that intentionally omit
    # historical receipts, but it cannot downgrade a real installation into
    # a different source family.
    if cli_versions:
        version = cli_versions[0]
    elif state_version is not None:
        version = state_version
    else:
        version = source_version
    family = _version_family(version)
    managed: list[str] = []
    preserved: list[str] = []
    if root.exists():
        for path in sorted(root.rglob("*")):
            try:
                metadata = path.lstat()
            except OSError:
                continue
            if stat.S_ISDIR(metadata.st_mode):
                continue
            relative = _rel(root, path)
            if relative.startswith(".x86qw/") and (
                path.name.endswith((".receipt", ".inventory"))
                or relative == ".x86qw/state.json"
            ):
                managed.append(relative)
            else:
                preserved.append(relative)
    return MigrationSource(
        version=version,
        family=family,
        state_format=state_format,
        managed_paths=tuple(managed),
        preserved_paths=tuple(preserved),
        cli_versions=tuple(cli_versions),
        state_version=state_version,
    )


def plan_migration(
    root: os.PathLike[str] | str,
    *,
    source_version: str | None = None,
    target_version: str = "1.0.0",
    dry_run: bool = True,
) -> MigrationPlan:
    """Build a complete, zero-write 0.7/0.8/0.9 → 1.0 plan."""

    root = Path(root)
    target = _normalize_target(target_version)
    source = inspect_migration_source(root, source_version=source_version)
    conflicts: list[MigrationConflict] = []
    operations: list[MigrationOperation] = []
    pending = inspect_pending_migration(root)
    if pending is not None:
        pending_path = _rel(root, pending.journal) if pending.journal.is_absolute() else str(pending.journal)
        conflicts.append(MigrationConflict(
            "recovery-required",
            pending_path,
            pending.detail or "an unfinished migration must be recovered before planning",
        ))
    metadata = root / ".x86qw"
    metadata_safe = True
    if metadata.exists() or metadata.is_symlink():
        metadata_mode = metadata.lstat().st_mode
        if stat.S_ISLNK(metadata_mode) or not stat.S_ISDIR(metadata_mode):
            metadata_safe = False
            conflicts.append(MigrationConflict(
                "unsafe-metadata", ".x86qw", "metadata root is not a private directory",
            ))
    # A caller-provided source version is only an assertion for metadata-light
    # synthetic fixtures.  Once a valid CLI receipt or state marker exists,
    # authenticated evidence controls the source family.  Keep a target-state
    # marker compatible with an old override so an already migrated, state-only
    # installation remains idempotent when callers replay their original
    # source argument.
    if source.cli_versions:
        if len(set(source.cli_versions)) != 1:
            conflicts.append(MigrationConflict(
                "source-version-conflict",
                ".x86qw",
                "CLI receipts disagree on the authenticated source version",
            ))
        if source_version is not None and source_version not in source.cli_versions:
            conflicts.append(MigrationConflict(
                "source-version-mismatch",
                ".x86qw",
                "source_version override disagrees with the authenticated CLI receipt",
            ))
        if (
            source.state_version is not None
            and source.state_version != source.cli_versions[0]
            and _version_family(source.state_version) != "1.0.x"
        ):
            conflicts.append(MigrationConflict(
                "source-version-conflict",
                ".x86qw/state.json",
                "state marker disagrees with the authenticated CLI receipt",
            ))
    elif source.state_version is not None and source_version is not None:
        if (
            _version_family(source.state_version) != "1.0.x"
            and source_version != source.state_version
        ):
            conflicts.append(MigrationConflict(
                "source-version-mismatch",
                ".x86qw/state.json",
                "source_version override disagrees with the authenticated state marker",
            ))
    if source_version is not None and _version_family(source_version) is None:
        conflicts.append(MigrationConflict(
            "source-version-mismatch",
            ".x86qw",
            "source_version override is outside the supported source contract",
        ))
    # The canonical CLI receipt is already in its target location after a
    # successful migration, but it remains ownership evidence on re-plans.
    # Never let an explicit override bypass a malformed or externally linked
    # canonical path.
    if metadata_safe:
        canonical_cli_root = metadata / "cli"
        canonical_cli = canonical_cli_root / "receipt"
        cli_root_present = canonical_cli_root.exists() or canonical_cli_root.is_symlink()
        if cli_root_present:
            try:
                cli_root_mode = canonical_cli_root.lstat().st_mode
            except OSError as error:
                conflicts.append(MigrationConflict(
                    "unsafe-metadata", _rel(root, canonical_cli_root), str(error),
                ))
                cli_root_mode = None
            if cli_root_mode is not None and (
                stat.S_ISLNK(cli_root_mode) or not stat.S_ISDIR(cli_root_mode)
            ):
                conflicts.append(MigrationConflict(
                    "unsafe-metadata", _rel(root, canonical_cli_root),
                    "canonical CLI metadata root is not a private directory",
                ))
            elif cli_root_mode is not None and (
                canonical_cli.exists() or canonical_cli.is_symlink()
            ):
                try:
                    identity = inspect_receipt(_safe_payload(root, canonical_cli))
                    if identity.kind != "cli":
                        raise ReceiptError("canonical CLI path does not contain a CLI receipt")
                except (MigrationError, ReceiptError, OSError) as error:
                    conflicts.append(MigrationConflict(
                        "corrupt-receipt", _rel(root, canonical_cli), str(error),
                    ))
        _validate_canonical_client_receipts(root, metadata, conflicts)
    if source.family is None:
        conflicts.append(MigrationConflict(
            "unknown-source", ".x86qw", "the historical installer version is not authenticated",
        ))
    elif source.family in {"0.8.x", "0.9.x"}:
        # No public 0.8.x/0.9.x release exists yet.  Keep the contract explicit
        # rather than treating a hand-written directory as release evidence.
        conflicts.append(MigrationConflict(
            "prospective-source", source.family,
            "public fixtures are not available for this source family yet",
        ))
    elif source.family not in {"0.7.x", "1.0.x"}:
        conflicts.append(MigrationConflict(
            "unsupported-source", source.version or "", "source is outside the 0.7.x/0.8.x/0.9.x contract",
        ))

    state_path = metadata / "state.json"
    if metadata_safe and (state_path.exists() or state_path.is_symlink()):
        try:
            payload = _safe_payload(root, state_path)
            import json
            document = json.loads(payload.decode("utf-8"))
            state = parse_install_state(
                document,
                allowed_profiles=INSTALLATION_PROFILES,
                allowed_capabilities=frozenset(),
            )
            # Both state formats can carry legacy component IDs or an older
            # installation marker.  Normalize the semantic state first and
            # only emit a write when its canonical bytes actually change.
            migrated = migrate_install_state(
                state,
                replacements=LEGACY_COMPONENT_REPLACEMENTS,
                removals=LEGACY_COMPONENT_REMOVALS,
                allowed_profiles=INSTALLATION_PROFILES,
                allowed_capabilities=frozenset(),
                target_version=target,
            )
            migrated_payload = serialize_install_state(migrated)
            if migrated_payload != payload:
                operations.append(_operation(
                    root,
                    key="state",
                    phase=MigrationPhase.COMMIT,
                    source=state_path,
                    destination=state_path,
                    kind="rewrite-state",
                    payload=migrated_payload,
                    source_payload=payload,
                ))
        except (MigrationError, StateError, UnicodeDecodeError, ValueError) as error:
            conflicts.append(MigrationConflict("corrupt-state", ".x86qw/state.json", str(error)))

    # Existing canonical and aggregate metadata is retained during a one-way
    # migration, but it still has to be authenticated before the plan can be
    # considered safe.  A partial or corrupt pair must never be silently
    # preserved as if it were an owned installation.
    if metadata_safe:
        aggregate_receipt = metadata / "nquake.receipt"
        aggregate_inventory = metadata / "nquake.inventory"
        aggregate_present = (
            aggregate_receipt.exists() or aggregate_receipt.is_symlink(),
            aggregate_inventory.exists() or aggregate_inventory.is_symlink(),
        )
        if any(aggregate_present):
            if not all(aggregate_present):
                conflicts.append(MigrationConflict(
                    "partial-receipt", ".x86qw/nquake.receipt", "aggregate nQuake metadata pair is incomplete",
                ))
            else:
                try:
                    receipt_payload = _safe_payload(root, aggregate_receipt)
                    inventory_payload = _safe_payload(root, aggregate_inventory)
                    legacy_receipt = parse_legacy_nquake_receipt(receipt_payload)
                    if hashlib.sha256(inventory_payload).hexdigest() != legacy_receipt.inventory_sha256:
                        raise ReceiptError("aggregate nQuake inventory differs from receipt")
                    _validate_managed_inventory(parse_inventory(inventory_payload))
                except (MigrationError, ReceiptError, OSError) as error:
                    conflicts.append(MigrationConflict(
                        "corrupt-receipt", ".x86qw/nquake.receipt", str(error),
                    ))
        canonical_root = metadata / "components"
        canonical_present = canonical_root.exists() or canonical_root.is_symlink()
        canonical_mode: int | None = None
        canonical_entries: tuple[Path, ...] = ()
        if canonical_present:
            try:
                canonical_mode = canonical_root.lstat().st_mode
            except OSError as error:
                conflicts.append(MigrationConflict(
                    "unsafe-metadata", _rel(root, canonical_root), str(error),
                ))
                canonical_mode = None
            if canonical_mode is not None and (
                stat.S_ISLNK(canonical_mode) or not stat.S_ISDIR(canonical_mode)
            ):
                conflicts.append(MigrationConflict(
                    "unsafe-metadata", _rel(root, canonical_root),
                    "canonical component metadata root is not a private directory",
                ))
            elif canonical_mode is not None:
                canonical_entries = tuple(sorted(canonical_root.iterdir()))
        if canonical_present and canonical_mode is not None and stat.S_ISDIR(canonical_mode):
            for component_root in canonical_entries:
                if not component_root.is_dir() or component_root.is_symlink():
                    conflicts.append(MigrationConflict(
                        "unsafe-metadata", _rel(root, component_root), "canonical component metadata is not a directory",
                    ))
                    continue
                receipt_path = component_root / "receipt"
                inventory_path = component_root / "inventory"
                present = (
                    receipt_path.exists() or receipt_path.is_symlink(),
                    inventory_path.exists() or inventory_path.is_symlink(),
                )
                if not any(present):
                    continue
                label = _rel(root, component_root)
                if not all(present):
                    conflicts.append(MigrationConflict(
                        "partial-receipt", label, "canonical component metadata pair is incomplete",
                    ))
                    continue
                try:
                    _normalize_component_receipt(
                        _safe_payload(root, receipt_path),
                        _safe_payload(root, inventory_path),
                        source_component=component_root.name,
                        target_component=component_root.name,
                    )
                except (MigrationError, ReceiptError, OSError) as error:
                    conflicts.append(MigrationConflict("corrupt-receipt", label, str(error)))

    retired_components: set[str] = set()
    component_candidates: dict[str, list[_ComponentMetadataCandidate]] = {}
    if metadata.is_dir() and not metadata.is_symlink():
        names = {path.name for path in metadata.iterdir() if path.is_file() or path.is_symlink()}
        for name in sorted(names):
            legacy = metadata / name
            if name in {"state.json", "cli.receipt", "nquake.receipt", "nquake.inventory"}:
                continue
            if name.endswith(".inventory"):
                # Inventory ownership is validated together with its receipt
                # when the receipt branch below is visited.  An orphan with a
                # recognizable component identity is nevertheless a partial
                # pair and must block rather than being silently preserved.
                component = name[:-10]
                if _COMPONENT_NAME.fullmatch(component) is not None:
                    receipt_path = metadata / f"{component}.receipt"
                    if not receipt_path.exists() or receipt_path.is_symlink():
                        conflicts.append(MigrationConflict(
                            "partial-metadata", _rel(root, receipt_path),
                            "receipt/inventory pair is incomplete",
                        ))
                else:
                    conflicts.append(MigrationConflict(
                        "unknown-receipt", _rel(root, legacy),
                        "ownership cannot be proven",
                    ))
                continue
            if name.startswith("ezquake-") and name.endswith(".receipt"):
                match = _CLIENT_RECEIPT.fullmatch(name)
                if match is None:
                    conflicts.append(MigrationConflict(
                        "unknown-receipt", _rel(root, legacy),
                        "unrecognized client receipt name",
                    ))
                    continue
                try:
                    payload = _safe_payload(root, legacy)
                    identity = inspect_receipt(payload)
                    if (
                        identity.kind != "ezquake"
                        or identity.channel != match.group(2)
                        or identity.subject != match.group(1)
                    ):
                        raise ReceiptError("client receipt identity differs from its filename")
                except (MigrationError, ReceiptError) as error:
                    conflicts.append(MigrationConflict("corrupt-receipt", _rel(root, legacy), str(error)))
                    continue
                destination = metadata / "clients" / "ezquake" / match.group(1) / f"{match.group(2)}.receipt"
                operation = _candidate_operation(
                    root, legacy, destination,
                    key=f"client:{match.group(1)}:{match.group(2)}",
                    kind="move-receipt", payload=payload, conflicts=conflicts,
                )
                if operation is not None:
                    operations.append(operation)
                continue
            if not name.endswith(".receipt") or not _COMPONENT_NAME.fullmatch(name[:-8]):
                if name.endswith((".receipt", ".inventory")):
                    conflicts.append(MigrationConflict(
                        "unknown-receipt", _rel(root, legacy),
                        "ownership cannot be proven",
                    ))
                continue
            component = name[:-8]
            receipt_path = legacy
            inventory_path = metadata / f"{component}.inventory"
            if not inventory_path.exists() or inventory_path.is_symlink():
                conflicts.append(MigrationConflict(
                    "partial-metadata", _rel(root, inventory_path),
                    "receipt/inventory pair is incomplete",
                ))
                continue
            try:
                receipt_payload = _safe_payload(root, receipt_path)
                inventory_payload = _safe_payload(root, inventory_path)
                target_component = LEGACY_COMPONENT_REPLACEMENTS.get(component, component)
                (
                    normalized_receipt_payload,
                    normalized_inventory_payload,
                ) = _normalize_component_receipt(
                    receipt_payload,
                    inventory_payload,
                    source_component=component,
                    target_component=target_component,
                )
            except (MigrationError, ReceiptError) as error:
                conflicts.append(MigrationConflict("corrupt-receipt", _rel(root, receipt_path), str(error)))
                continue
            candidate = _ComponentMetadataCandidate(
                source_component=component,
                target_component=target_component,
                receipt_path=receipt_path,
                inventory_path=inventory_path,
                receipt_payload=receipt_payload,
                inventory_payload=inventory_payload,
                normalized_receipt_payload=normalized_receipt_payload,
                normalized_inventory_payload=normalized_inventory_payload,
            )
            if component in LEGACY_COMPONENT_REMOVALS:
                # This component is no longer active.  Its validated bytes stay
                # at the legacy path for diagnosis and manual disposition;
                # only the persisted active state is rewritten above.
                retired_components.add(component)
                continue
            component_candidates.setdefault(target_component, []).append(candidate)

        for target_component in sorted(component_candidates):
            candidates = component_candidates[target_component]
            normalized_pairs = {
                (
                    candidate.normalized_receipt_payload,
                    candidate.normalized_inventory_payload,
                )
                for candidate in candidates
            }
            if len(normalized_pairs) != 1:
                paths = ", ".join(
                    _rel(root, candidate.receipt_path) for candidate in candidates
                )
                conflicts.append(MigrationConflict(
                    "component-collision",
                    target_component,
                    f"legacy and canonical metadata disagree: {paths}",
                ))
                continue
            canonical = next(
                (
                    candidate for candidate in candidates
                    if candidate.source_component == target_component
                ),
                candidates[0],
            )
            duplicates = [candidate for candidate in candidates if candidate is not canonical]
            destination_root = metadata / "components" / target_component
            receipt_operation = _candidate_operation(
                root,
                canonical.receipt_path,
                destination_root / "receipt",
                key=f"component:{target_component}:receipt",
                kind="move-receipt",
                payload=canonical.normalized_receipt_payload,
                source_payload=canonical.receipt_payload,
                conflicts=conflicts,
            )
            inventory_operation = _candidate_operation(
                root,
                canonical.inventory_path,
                destination_root / "inventory",
                key=f"component:{target_component}:inventory",
                kind="move-inventory",
                payload=canonical.normalized_inventory_payload,
                source_payload=canonical.inventory_payload,
                conflicts=conflicts,
            )
            operations.extend(
                item for item in (receipt_operation, inventory_operation) if item is not None
            )
            for duplicate in duplicates:
                # Equivalent legacy/current pairs are safe to collapse because
                # both identities and inventory hashes were validated.  The
                # duplicate is removed only after the canonical pair commits,
                # and its bytes remain in the journal for rollback/recovery.
                operations.extend(_retire_duplicate_operation(
                    root,
                    duplicate,
                    kind="retire-duplicate",
                ))

        cli_legacy = metadata / "cli.receipt"
        if cli_legacy.exists() or cli_legacy.is_symlink():
            try:
                payload = _safe_payload(root, cli_legacy)
                identity = inspect_receipt(payload)
                if identity.kind != "cli":
                    raise ReceiptError("legacy CLI path does not contain a CLI receipt")
            except (MigrationError, ReceiptError) as error:
                conflicts.append(MigrationConflict("corrupt-receipt", _rel(root, cli_legacy), str(error)))
            else:
                operation = _candidate_operation(
                    root, cli_legacy, metadata / "cli" / "receipt",
                    key="cli", kind="move-receipt", payload=payload, conflicts=conflicts,
                )
                if operation is not None:
                    operations.append(operation)

    # An operation that would replace a destination is never emitted when any
    # conflict exists.  This keeps dry-run and execution equally non-destructive.
    if conflicts:
        operations = []
    preserved = tuple(sorted(set(source.preserved_paths) | {
        path[0] for path in _tree_snapshot(root)
        if path[0] not in {item.source for item in operations}
    }))
    return MigrationPlan(
        root=root,
        source=source,
        target_version=target,
        operations=tuple(operations),
        preserved_paths=preserved,
        conflicts=tuple(conflicts),
        dry_run=dry_run,
        snapshot=_tree_snapshot(root),
        retired_components=tuple(sorted(retired_components)),
    )


def _rollback_records(root: Path, records: list[_RollbackRecord]) -> None:
    for record in reversed(records):
        source = root / record.source
        destination = root / record.destination
        try:
            if (
                source == destination
                and not destination.exists()
                and not destination.is_symlink()
                and record.previous_source is not None
                and record.kind in {"retire-duplicate", "finalize-duplicate"}
            ):
                # ``retire-duplicate`` uses a source==destination record and
                # removes that pathname during finalize.  Restore its retained
                # bytes before applying the ordinary destination identity
                # checks used by move/rewrite operations.
                destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(destination, record.previous_source)
                continue
            if record.destination_identity is not None:
                metadata = destination.lstat()
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                    raise OSError(f"migration destination changed identity: {record.destination}")
                if (
                    int(metadata.st_dev), int(metadata.st_ino)
                ) != record.destination_identity:
                    raise OSError(f"migration destination changed identity: {record.destination}")
                if _digest(_safe_payload(root, destination)) != record.destination_sha256:
                    raise OSError(f"migration destination bytes changed: {record.destination}")
            elif destination.exists() or destination.is_symlink():
                raise OSError(f"migration destination identity is unknown: {record.destination}")
            if record.previous_destination is None:
                if destination.exists() or destination.is_symlink():
                    destination.unlink()
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(destination, record.previous_destination)
            if record.previous_source is not None:
                source.parent.mkdir(parents=True, exist_ok=True)
                if source.exists() or source.is_symlink():
                    metadata = source.lstat()
                    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                        raise OSError(f"migration source changed identity: {record.source}")
                    # A source that survived finalize belongs to this plan only
                    # when its original inode is still present; otherwise leave
                    # the user-owned object untouched.
                    if _digest(_safe_payload(root, source)) != _digest(record.previous_source):
                        raise OSError(f"migration source bytes changed: {record.source}")
                else:
                    atomic_write_bytes(source, record.previous_source)
        except OSError as error:
            raise MigrationError(f"migration rollback failed: {record.source}") from error


def execute_migration(
    plan: MigrationPlan,
    *,
    fail_phase: MigrationPhase | str | None = None,
) -> MigrationResult:
    """Apply a plan with staged bytes and byte-for-byte inverse material."""

    if not isinstance(plan, MigrationPlan):
        raise TypeError("plan must be MigrationPlan")
    if not plan.executable:
        raise MigrationError("migration plan is blocked by conflicts")
    if plan.snapshot != _tree_snapshot(plan.root):
        raise MigrationError("installation changed after migration planning")
    if not plan.operations:
        return MigrationResult(plan, "noop", (), plan.preserved_paths)
    requested_failure = MigrationPhase(fail_phase) if fail_phase is not None else None
    root = plan.root
    stage: Path | None = None
    journal_context: _JournalContext | None = None
    records: list[_RollbackRecord] = []
    applied: list[str] = []
    phase = MigrationPhase.PREFLIGHT
    committed = False
    try:
        if requested_failure == phase:
            raise RuntimeError("injected migration failure")
        journal_context = _create_migration_journal(plan)
        stage = journal_context.stage
        phase = MigrationPhase.STAGE
        for index, operation in enumerate(plan.operations):
            source = root / operation.source
            source_payload = _safe_payload(root, source)
            if operation.source_identity is not None:
                source_metadata = source.lstat()
                if (
                    int(source_metadata.st_dev), int(source_metadata.st_ino)
                ) != operation.source_identity:
                    raise MigrationError(f"source identity changed during staging: {operation.source}")
            if _digest(source_payload) != operation.source_sha256:
                raise MigrationError(f"source changed during staging: {operation.source}")
            payload = operation.payload or source_payload
            if len(payload) != operation.expected_size or _digest(payload) != operation.expected_sha256:
                raise MigrationError(f"planned bytes failed staging: {operation.key}")
            staged = stage / str(index)
            atomic_write_bytes(staged, payload, mode=0o600)
            _journal_checkpoint(
                journal_context, phase, operation_index=index, operation_status="staged",
            )
        if requested_failure == phase:
            raise RuntimeError("injected migration failure")
        phase = MigrationPhase.VERIFY
        for index, operation in enumerate(plan.operations):
            staged = stage / str(index)
            payload = staged.read_bytes()
            if len(payload) != operation.expected_size or _digest(payload) != operation.expected_sha256:
                raise MigrationError(f"staged bytes failed verification: {operation.key}")
            _journal_checkpoint(
                journal_context, phase, operation_index=index, operation_status="verified",
            )
        if requested_failure == phase:
            raise RuntimeError("injected migration failure")
        phase = MigrationPhase.COMMIT
        for index, operation in enumerate(plan.operations):
            source = root / operation.source
            destination = root / operation.destination
            payload = (stage / str(index)).read_bytes()
            _journal_checkpoint(
                journal_context, phase, operation_index=index, operation_status="commit-start",
            )
            previous_source = _safe_payload(root, source)
            previous_destination = None
            if destination.exists() or destination.is_symlink():
                previous_destination = _safe_payload(root, destination)
            if operation.kind == "rewrite-state":
                atomic_write_bytes(destination, payload)
            elif operation.kind in {"retire-duplicate", "finalize-duplicate"}:
                # A duplicate source is retained until the commit checkpoint;
                # finalization removes only the exact bytes authenticated by
                # the journal.  ``finalize-duplicate`` is kept for journals
                # written by an earlier runtime version.
                pass
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists() or destination.is_symlink():
                    if previous_destination != payload:
                        raise MigrationError(f"destination changed during commit: {operation.destination}")
                else:
                    atomic_create_bytes(destination, payload)
            destination_metadata = destination.lstat()
            records.append(_RollbackRecord(
                operation.source,
                operation.destination,
                previous_source,
                previous_destination,
                (
                    int(destination_metadata.st_dev), int(destination_metadata.st_ino)
                ),
                _digest(_safe_payload(root, destination)),
                operation.kind,
            ))
            journal_operations = journal_context.document.get("operations")
            if not isinstance(journal_operations, list) or not isinstance(
                journal_operations[index], dict
            ):
                raise MigrationError("migration journal operation checkpoint is invalid")
            journal_operations[index]["destination_after_identity"] = [
                int(destination_metadata.st_dev), int(destination_metadata.st_ino),
            ]
            applied.append(operation.key)
            _journal_checkpoint(
                journal_context, phase, operation_index=index, operation_status="committed",
            )
        if requested_failure == phase:
            raise RuntimeError("injected migration failure")
        phase = MigrationPhase.FINALIZE
        for index, operation in enumerate(plan.operations):
            if operation.kind in {
                "move-receipt", "move-inventory", "retire-duplicate", "finalize-duplicate",
            }:
                source = root / operation.source
                _journal_checkpoint(
                    journal_context, phase, operation_index=index, operation_status="finalize-start",
                )
                if source.exists() or source.is_symlink():
                    current_payload = _safe_payload(root, source)
                    if (
                        operation.source_identity is not None
                        and (
                            int(source.lstat().st_dev), int(source.lstat().st_ino)
                        ) != operation.source_identity
                    ):
                        raise MigrationError(
                            f"source identity changed before finalize: {operation.source}"
                        )
                    if _digest(current_payload) != operation.source_sha256:
                        raise MigrationError(
                            f"source bytes changed before finalize: {operation.source}"
                        )
                    source.unlink()
                _journal_checkpoint(
                    journal_context, phase, operation_index=index, operation_status="finalized",
                )
        if requested_failure == phase:
            raise RuntimeError("injected migration failure")
        # The bytes are now committed and all legacy paths have been
        # finalized.  Persist that boundary before housekeeping so a cleanup
        # failure cannot be mistaken for a recoverable data rollback.
        journal_context.document["phase"] = phase.value
        journal_context.document["status"] = _JOURNAL_CLEANUP_STATUS
        _persist_migration_journal(journal_context.journal, journal_context.document)
        committed = True
        # Reuse the same closed, identity-bound cleanup path used by a later
        # process.  If housekeeping fails, the cleanup-pending journal remains
        # the durable recovery checkpoint while the committed bytes stay put.
        recover_migration(root)
        journal_context = None
    except BaseException as error:
        cleanup_pending = committed
        try:
            if cleanup_pending:
                # Recovery only retries private journal cleanup.  The final
                # installation bytes remain the committed result.
                recover_migration(root)
            elif journal_context is not None:
                recover_migration(root)
            else:
                _rollback_records(root, records)
            rolled_back = True
        except BaseException:
            rolled_back = False
        if cleanup_pending:
            rolled_back = False
        if journal_context is None and stage is not None and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        outcome = (
            "committed; cleanup=pending"
            if cleanup_pending
            else f"rollback={'ok' if rolled_back else 'incomplete'}"
        )
        raise MigrationExecutionError(
            f"migration failed during {phase.value}; {outcome}",
            phase=phase,
            rolled_back=rolled_back,
            committed=cleanup_pending,
            cause=error,
        ) from error
    finally:
        if journal_context is None and stage is not None and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    return MigrationResult(
        plan,
        "committed",
        tuple(applied),
        plan.preserved_paths,
        tuple(records),
    )


def rollback_migration(result: MigrationResult) -> None:
    """Restore the exact source/destination bytes retained by a result."""

    if not isinstance(result, MigrationResult):
        raise TypeError("result must be MigrationResult")
    if result.status != "committed":
        return
    _rollback_records(result.plan.root, list(result._rollback_records))


def migrate_installation(
    root: os.PathLike[str] | str,
    *,
    source_version: str | None = None,
    target_version: str = "1.0.0",
    dry_run: bool = True,
    fail_phase: MigrationPhase | str | None = None,
) -> MigrationPlan | MigrationResult:
    """Convenience API: return a plan for dry-run, otherwise execute it."""

    root_path = Path(root)
    pending = inspect_pending_migration(root_path)
    if pending is not None and not dry_run:
        recover_migration(root_path)
    plan = plan_migration(
        root_path,
        source_version=source_version,
        target_version=target_version,
        dry_run=dry_run,
    )
    return plan if dry_run else execute_migration(plan, fail_phase=fail_phase)


# Explicit aliases make the public boundary discoverable without duplicating
# the planner implementation.
build_migration_plan = plan_migration
apply_migration = execute_migration
