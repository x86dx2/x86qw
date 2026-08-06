# Runbook de release

Este checkout é validado e operado diretamente no Mac. Linux-X64, Windows-X64,
macOS-ARM64 e macOS-X64 continuam nomes de compatibilidade/catalogo; nenhuma
dessas plataformas é executada ou usada como pré-condição da promoção local.

## Fluxo local

O caminho canônico não usa GitHub Actions, runners, artifacts remotos, tokens,
environments, assinatura externa ou publicador. A sequência mínima é:

```sh
set -eu

VERSION="1.0.0"
CANDIDATE_COMMIT="$(git rev-parse HEAD)"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/x86qw-release.XXXXXXXX")"
INPUT_DIR="$WORK_DIR/release-input"
CANDIDATE_DIR="$WORK_DIR/candidate"
PROMOTED_DIR="$WORK_DIR/promoted"
INSTALLER_BUILD_DIR="$WORK_DIR/installer-build"
trap 'rm -rf "$WORK_DIR"' EXIT

git lfs pull
git lfs fsck
PYTHONDONTWRITEBYTECODE=1 ./maintenance/manage.py verify
PYTHONDONTWRITEBYTECODE=1 ./maintenance/manage.py build \
  --project-ref "$CANDIDATE_COMMIT"

mkdir -p "$INPUT_DIR/installer"
cp -a maintenance/build/packages/. "$INPUT_DIR/"
python3 maintenance/tools/build_installer_bundle.py \
  --output "$INSTALLER_BUILD_DIR" \
  --version "$VERSION" \
  --ownership-output "$WORK_DIR/ownership-installer.json"
cp "$INSTALLER_BUILD_DIR/$VERSION/x86qw-installer-$VERSION.zip" "$INPUT_DIR/installer/"

python3 maintenance/tools/release_candidate.py prepare \
  --source "$INPUT_DIR" \
  --output "$CANDIDATE_DIR" \
  --version "$VERSION" \
  --commit "$CANDIDATE_COMMIT" \
  --ownership-fragment maintenance/build/ownership/content.json \
  --ownership-fragment "$WORK_DIR/ownership-installer.json"
python3 maintenance/tools/release_candidate.py verify "$CANDIDATE_DIR"
python3 maintenance/tools/release_candidate.py promote \
  "$CANDIDATE_DIR" "$PROMOTED_DIR"
```

O `release_candidate.py prepare` gera `candidate.json`, `checksums.txt`,
`ownership.json`, `sbom.spdx.json` e `provenance.json` junto dos payloads. Ele
não gera `release-evidence.json`. A verificação e a promoção local não fazem
rede e não exigem `trust_root`; mantêm identidade por commit, hashes, ownership,
SBOM, provenance, revalidação do staging e destino novo sem overwrite.

Se os builders produzirem caminhos de pacote diferentes, ajuste somente a
montagem de `INPUT_DIR`; não copie metadata de outro candidato nem altere o
commit registrado. O diretório de entrada deve conter exatamente os artefatos
que serão promovidos.

## Compatibilidade opcional

`release-evidence.json`, `trust_root`, handoffs externos, metadata assinada,
mirrors e ferramentas de publicação continuam disponíveis para uma decisão
explícita futura. Eles não são chamados automaticamente por `verify` ou
`promote`, e nenhum arquivo de evidência nativa transforma um candidato Mac em
afirmação de validação multiplataforma.

O conjunto público, quando uma publicação remota for autorizada separadamente,
contém os ZIPs e cinco documentos auditáveis: `candidate.json`, `checksums.txt`,
`ownership.json`, `sbom.spdx.json` e `provenance.json`. A publicação remota é uma
operação posterior, opcional e fora da promoção local; este runbook não executa
upload, criação de release, alteração de catálogo ou uso de chave privada.

Depois de uma publicação autorizada, `public_install_smoke.py` pode ser usado
como verificação pública de instalação no Mac. Isso não é smoke nativo de
runtime e não participa da preparação ou promoção local.

A release `0.7.3` permanece imutável. A `1.0.0` só pode ser publicada depois
dos controles de ownership, trust e aprovação que forem explicitamente
autorizados para a operação remota; a ausência de smokes Linux/Windows não é
um bloqueador deste fluxo Mac/local.
