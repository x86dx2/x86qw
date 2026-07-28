"""Acesso autenticado e amigavel a API publica do GitHub."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from functools import lru_cache


@lru_cache(maxsize=1)
def github_token() -> str | None:
    """Reutiliza credenciais existentes sem tornar o gh uma dependencia obrigatoria."""
    for variable in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(variable, "").strip()
        if token:
            return token
    if shutil.which("gh") is None:
        return None
    result = subprocess.run(
        ["gh", "auth", "token"],
        check=False,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


def github_json(path: str, *, user_agent: str = "x86qw-maintenance/1", timeout: int = 90) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": user_agent,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"https://api.github.com/{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in {403, 429}:
            error.close()
            mode = "autenticada" if token else "anonima"
            guidance = (
                "Verifique o limite da conta com 'gh api rate_limit'."
                if token
                else "Autentique com 'gh auth login' ou defina GH_TOKEN/GITHUB_TOKEN."
            )
            raise ValueError(
                f"o GitHub recusou a consulta {mode} por limite de requisicoes. {guidance}"
            ) from error
        raise
