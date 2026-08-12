"""Verify provenance of protected external GitHub Actions handoffs.

This gate is intentionally read-only.  It binds a supplied run ID to this
repository and candidate commit, and checks that the exact artifact names and
IDs to be downloaded were published by that successful run.  The artifact
contents are validated by the native handoff normalizer afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.release_candidate import CandidateError
from x86qw_runtime.io.remote import RemoteClient


SHA1 = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[1-9][0-9]{0,19}$")
ARTIFACT_ID = re.compile(r"^[1-9][0-9]{0,19}$")
ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
WORKFLOW_PATH = re.compile(r"^\.github/workflows/[A-Za-z0-9._/-]+\.ya?ml$")
EVENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
MAX_RESPONSE = 2 * 1024 * 1024
MAX_ARTIFACT_PAGES = 10


class _QuietReporter:
    def detail(self, message: str) -> None:
        del message

    def warning(self, message: str) -> None:
        del message

    def download_progress(self, received: int, total: int | None, *, done: bool = False) -> None:
        del received, total, done


def _get_json(url: str, *, token: str) -> dict[str, object]:
    try:
        payload = RemoteClient(_QuietReporter()).get(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "x86qw-release-gate/1",
            },
            maximum_size=MAX_RESPONSE,
            timeout=15,
            attempts=1,
        )
    except Exception as error:
        raise CandidateError("não foi possível consultar a API de Actions") from error
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CandidateError("resposta da API de Actions inválida") from error
    if not isinstance(value, dict):
        raise CandidateError("resposta da API de Actions não é um objeto")
    return value


def verify_external_run(
    *,
    repository: str,
    run_id: str,
    commit: str,
    artifacts: tuple[str, ...],
    artifact_ids: dict[str, int] | None = None,
    token: str,
    workflow: str,
    event: str = "workflow_dispatch",
    head_branch: str | None = None,
) -> dict[str, object]:
    repository_parts = repository.split("/")
    if (
        len(repository_parts) != 2
        or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in repository_parts)
        or any(char in repository for char in "\r\n\x00")
    ):
        raise CandidateError("repositório GitHub inválido")
    if RUN_ID.fullmatch(run_id) is None:
        raise CandidateError("run_id externo inválido")
    if SHA1.fullmatch(commit) is None:
        raise CandidateError("commit externo inválido")
    if not token:
        raise CandidateError("GITHUB_TOKEN é obrigatório para validar o run externo")
    if WORKFLOW_PATH.fullmatch(workflow) is None:
        raise CandidateError("workflow externo inválido ou não fixado")
    if EVENT.fullmatch(event) is None:
        raise CandidateError("evento do workflow externo inválido")
    if head_branch is not None and BRANCH.fullmatch(head_branch) is None:
        raise CandidateError("ref do workflow externo inválida")
    if not artifacts or len(set(artifacts)) != len(artifacts):
        raise CandidateError("artefatos externos devem ser únicos e não vazios")
    if any(ARTIFACT_NAME.fullmatch(name) is None for name in artifacts):
        raise CandidateError("nome do artefato externo inválido")
    if artifact_ids is not None:
        if set(artifact_ids) != set(artifacts):
            raise CandidateError("IDs de artefatos externos não correspondem aos nomes")
        for name, artifact_id in artifact_ids.items():
            if isinstance(artifact_id, bool) or not isinstance(artifact_id, int) or artifact_id <= 0:
                raise CandidateError(f"ID do artefato externo inválido: {name}")
    encoded_repo = urllib.parse.quote(repository, safe="/")
    base = f"https://api.github.com/repos/{encoded_repo}/actions/runs/{run_id}"
    run = _get_json(base, token=token)
    if run.get("head_sha") != commit or run.get("status") != "completed" or run.get("conclusion") != "success":
        raise CandidateError("run externo não está concluído com sucesso no commit candidato")
    if run.get("path") != workflow or run.get("event") != event:
        raise CandidateError("run externo não pertence ao workflow/evento esperado")
    if head_branch is not None and run.get("head_branch") != head_branch:
        raise CandidateError("run externo não pertence à ref esperada")
    repository_value = run.get("repository")
    if not isinstance(repository_value, dict) or repository_value.get("full_name") != repository:
        raise CandidateError("run externo pertence a outro repositório")
    head_repository = run.get("head_repository")
    if not isinstance(head_repository, dict) or head_repository.get("full_name") != repository:
        raise CandidateError("run externo tem head repository divergente")
    entries: list[dict[str, object]] = []
    for page in range(1, MAX_ARTIFACT_PAGES + 1):
        artifacts_value = _get_json(
            f"{base}/artifacts?per_page=100&page={page}", token=token,
        )
        page_entries = artifacts_value.get("artifacts")
        if not isinstance(page_entries, list):
            raise CandidateError("lista de artefatos do run externo inválida")
        for entry in page_entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                raise CandidateError("artefato do run externo inválido")
            if entry.get("expired") is not False:
                raise CandidateError("artefato do run externo expirado ou sem estado explícito")
            entries.append(entry)
        if len(page_entries) < 100:
            break
    else:
        raise CandidateError("lista de artefatos do run externo excede o limite de páginas")
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise CandidateError("run externo publicou artefatos duplicados")
    by_name = {str(entry["name"]): entry for entry in entries}
    for name, entry in by_name.items():
        if ARTIFACT_ID.fullmatch(str(entry.get("id", ""))) is None:
            raise CandidateError(f"artefato externo sem ID imutável: {name}")
        if artifact_ids is not None and name in artifact_ids and int(str(entry["id"])) != artifact_ids[name]:
            raise CandidateError(f"ID do artefato externo diverge do nome verificado: {name}")
        digest = entry.get("digest")
        if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
            raise CandidateError(f"artefato externo sem digest SHA-256: {name}")
        if not isinstance(entry.get("created_at"), str) or not isinstance(entry.get("updated_at"), str):
            raise CandidateError(f"artefato externo sem timestamps: {name}")
    missing = sorted(set(artifacts) - set(names))
    if missing:
        raise CandidateError(f"run externo não publicou os artefatos esperados: {', '.join(missing)}")
    # Return the exact API identities to the caller so a later download can be
    # pinned to the artifact that was checked, rather than a mutable name.
    run["verified_artifacts"] = {
        name: {
            "id": int(str(by_name[name]["id"])),
            "digest": by_name[name]["digest"],
            "created_at": by_name[name]["created_at"],
            "updated_at": by_name[name]["updated_at"],
        }
        for name in artifacts
    }
    return run


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="valida run externo protegido da cerimônia de release")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--artifact", action="append", dest="artifacts", required=True)
    parser.add_argument(
        "--artifact-id",
        action="append",
        dest="artifact_ids",
        type=int,
        help="ID imutável correspondente a cada --artifact, na mesma ordem",
    )
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--event", default="workflow_dispatch")
    parser.add_argument("--head-branch")
    parser.add_argument(
        "--output",
        type=Path,
        help="grava as identidades verificadas dos artefatos em JSON privado",
    )
    options = parser.parse_args(arguments)
    try:
        artifact_ids = None
        if options.artifact_ids is not None:
            if len(options.artifact_ids) != len(options.artifacts):
                raise CandidateError("cada --artifact precisa de um --artifact-id correspondente")
            artifact_ids = dict(zip(options.artifacts, options.artifact_ids, strict=True))
        result = verify_external_run(
            repository=options.repository,
            run_id=options.run_id,
            commit=options.commit,
            artifacts=tuple(options.artifacts),
            artifact_ids=artifact_ids,
            token=os.environ.get("GITHUB_TOKEN", ""),
            workflow=options.workflow,
            event=options.event,
            head_branch=options.head_branch,
        )
        if options.output is not None:
            destination = options.output
            if destination.exists() or destination.is_symlink():
                raise CandidateError(f"destino de handoff já existe: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                result.get("verified_artifacts", {}),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n"
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}-", dir=destination.parent,
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    except CandidateError as error:
        print(f"[ERRO] {error}")
        return 1
    print(f"[OK] Run externo {options.run_id} pertence ao candidato e publicou os handoffs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
