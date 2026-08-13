# Runbook de release

O fluxo de release tem duas camadas: a validação local/portátil e o workflow
protegido de candidato. Linux, Windows e macOS Intel continuam `preview`; o
único smoke nativo obrigatório deste escopo é um Mac M3 (Apple M3/macOS arm64) real.

## Candidato local

Use um diretório temporário fora do repositório e nunca copie metadata de outro
candidato:

```sh
set -eu

VERSION="1.0.0-rc.1"
COMMIT="$(git rev-parse HEAD)"
GENERATED_AT="$(python3 -c 'import sys; from datetime import datetime, timezone; print(datetime.fromisoformat(sys.argv[1]).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))' "$(git show -s --format=%cI "$COMMIT")")"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/x86qw-release.XXXXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT

PYTHONDONTWRITEBYTECODE=1 ./maintenance/manage.py verify --no-tests
PYTHONDONTWRITEBYTECODE=1 ./maintenance/manage.py build --project-ref "$COMMIT"
python3 maintenance/tools/build_installer_bundle.py \
  --output "$WORK_DIR/installer-build" \
  --version "$VERSION" \
  --ownership-output "$WORK_DIR/ownership-installer.json"

mkdir -p "$WORK_DIR/input/content" "$WORK_DIR/input/installer" \
  "$WORK_DIR/input/runtime" "$WORK_DIR/input/site"
find maintenance/build/packages -type f -print0 | while IFS= read -r -d '' artifact; do
  relative="${artifact#maintenance/build/packages/}"
  destination="$WORK_DIR/input/content/$relative"
  mkdir -p "$(dirname "$destination")"
  install -m644 "$artifact" "$destination"
done
for runtime_root in clients servers services; do
  find "dist/$runtime_root" -type f -print0 | while IFS= read -r -d '' artifact; do
    relative="${artifact#dist/$runtime_root/}"
    destination="$WORK_DIR/input/runtime/$runtime_root/$relative"
    mkdir -p "$(dirname "$destination")"
    install -m644 "$artifact" "$destination"
  done
done
install -m644 \
  "$WORK_DIR/installer-build/$VERSION/x86qw-installer-$VERSION.zip" \
  "$WORK_DIR/input/installer/x86qw-installer-$VERSION.zip"
mkdir -p "$WORK_DIR/input/runtime/native-smoke/macos-arm64"
install -m644 maintenance/native_case_entrypoint.py \
  "$WORK_DIR/input/runtime/native-smoke/macos-arm64/x86qw-native-smoke"
install -m644 maintenance/native/macos-arm64/entrypoint.json \
  "$WORK_DIR/input/runtime/native-smoke/macos-arm64/entrypoint.json"
python3 maintenance/tools/build_release_catalog.py \
  --source site/public/api/v1/catalog.json \
  --installer "$WORK_DIR/input/installer/x86qw-installer-$VERSION.zip" \
  --output "$WORK_DIR/input/catalog.json" \
  --version "$VERSION" \
  --release-title "x86QW $VERSION" \
  --generated-at "$GENERATED_AT" \
  --release-notes "Release candidate local." \
  --product-source site/public/api/v1/product.json \
  --product-output "$WORK_DIR/input/product.json"
python3 maintenance/tools/render_release_site.py \
  --source site/public \
  --catalog "$WORK_DIR/input/catalog.json" \
  --product "$WORK_DIR/input/product.json" \
  --bootstrap-source dist/installer/bin \
  --output "$WORK_DIR/input/site/public"
rm -rf "$WORK_DIR/input/site/public/api/v1/trust"
python3 - "$WORK_DIR" <<'PY'
import hashlib
import sys
from pathlib import Path
from maintenance.tools import release_ownership

root = Path(sys.argv[1]) / "input"
artifacts = {}
for path in sorted(root.rglob("*")):
    if path.is_file():
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        artifacts[relative] = {
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
release_ownership.write_document(
    Path(sys.argv[1]) / "ownership-all.json",
    release_ownership.default_document(artifacts),
)
PY

python3 maintenance/tools/release_candidate.py prepare \
  --source "$WORK_DIR/input" \
  --output "$WORK_DIR/candidate" \
  --version "$VERSION" \
  --commit "$COMMIT" \
  --generated-at "$GENERATED_AT" \
  --ownership-fragment "$WORK_DIR/ownership-all.json"
python3 maintenance/tools/release_candidate.py verify "$WORK_DIR/candidate"
python3 maintenance/tools/release_candidate.py promote \
  "$WORK_DIR/candidate" "$WORK_DIR/promoted"
```

`release_candidate.py` só copia bytes já produzidos. `verify` e `promote` não
fazem rede, não assinam e não fazem rebuild; a promoção exige um destino novo e
não substitui uma árvore existente. A pasta `site/public/api/v1/trust` fica fora
do candidato de propósito: ela só entra na árvore final após a autenticação da
metadata assinada em `metadata-last`; `release_candidate` rejeita metadata TUF
stale se ela aparecer no staging.

## Workflow protegido

`.github/workflows/release.yml` é a autoridade operacional para uma promoção;
`.github/workflows/native-m3.yml` observa o candidato e
`.github/workflows/sign-native-evidence.yml` monta a saída assinada:

1. `build-once` cria o candidato e o artifact imutável, cujo nome exato é
   `candidate-<commit>-<run_id>-<run_attempt>`;
2. `portable-verify` baixa o artifact por ID e valida os mesmos bytes;
3. o workflow `native-m3.yml` recebe `candidate_run_id`, `candidate_artifact_id`
   e o nome exato do artifact, verifica pela API que a publicação pertence ao
   `build-once`, confere o SHA do candidato no runner self-hosted Apple M3 e
   produz somente observação `pending`, sem assinatura;
4. o mantenedor autorizado assina o corpo canônico fora do repositório, sob o
   waiver do ADR 0007; isso não é revisão humana independente. O workflow
   `sign-native-evidence.yml` autentica esse envelope e fornece o artifact
   `native-m3-signed` ao workflow de promoção;
5. `approval` e `release-blockers` formam o limite protegido: a API do GitHub
   é consultada com `issues: read` e falha
   fechado se houver issue P0/P1 aberta ou se a resposta não puder ser
   validada;
6. `attach-native-evidence` baixa o candidato e a evidência assinada por IDs,
   sem rebuild; `promotion-gate` valida a root de trust e a cobertura M3;
7. `publish-assets` consulta o estado remoto, recusa overwrite e publica os
   assets exatos já transportados pelo candidato promovido, sem rebuild ou
   overwrite;
8. `verify-mirrors` valida cada URL declarado pelo catálogo;
9. `metadata-last` valida metadata TUF assinada fornecida pela custódia e só a
   disponibiliza para staging depois dos assets;
   a verificação final de TUF, produto e bootstraps públicos ocorre no mesmo job
   após o deploy; o produto é comparado byte a byte com o candidato aprovado.

O artifact fica retido por 90 dias, acima do período mínimo de soak do RC. Não
há etapa de rebuild após a aprovação. Plano ausente, candidato divergente,
evidência não assinada ou metadata TUF ausente falham fechados.

## TUF e metadata-last

`maintenance/tools/publish_tuf_metadata.py` valida a root incorporada, autentica
timestamp/snapshot/targets e compara o target `catalog/catalog.json` com o
catálogo final. Ele não cria chaves, assina, renova ou publica metadata. A
cerimônia autorizada do mantenedor deve fornecer `metadata/` e `targets/`; o
processo de publicação do site recebe somente o staging validado.

A renovação operacional, neste repositório de mantenedor único, requer custódia
explicitamente sob o ADR 0007, monitor de expiração e um signer agendado.
`.github/workflows/tuf-monitor.yml` executa a
verificação autenticada de hora em hora, falha 6 horas antes do vencimento e
abre/atualiza uma única issue acionável quando o monitor falha; ele é somente
observabilidade e não substitui a cerimônia autorizada de renovação. Sem signer,
alertas entregues e um drill de recuperação observados, a 1.0 permanece NO-GO
mesmo que a validação técnica da TUF passe.

## Rollback e publicação

Tags, assets e metadata publicados são imutáveis. Em divergência, interrompa a
promoção, preserve o candidato e siga `docs/runbooks/incident-response.md`;
não sobrescreva release, mirror ou metadata. Este runbook não executa upload,
cria tag, altera site público ou usa chave privada por conta própria.
