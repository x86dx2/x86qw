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
mkdir -p "$WORK_DIR/input/runtime/native-smoke/macos-arm64/fixtures/migrations"
cp -R maintenance/tests/fixtures/migrations/0.7.13 \
  "$WORK_DIR/input/runtime/native-smoke/macos-arm64/fixtures/migrations/0.7.13"
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
   sem rebuild; a etapa também materializa `release-evidence.json`,
   `evidence-root.json` e `release-receipt.json` sem overwrite. No modo
   `promote-1.0`, o recibo inclui o handoff do acceptance público; no modo
   `promote-rc`, essa seção permanece ausente por desenho. `promotion-gate`
   valida a root de trust e a cobertura M3;
7. `publish-assets` consulta o estado remoto, recusa overwrite e publica os
   assets exatos já transportados pelo candidato promovido, sem rebuild ou
   overwrite;
8. `verify-mirrors` valida cada URL declarado pelo catálogo;
9. `metadata-last` valida metadata TUF assinada fornecida pela custódia e só a
   disponibiliza para staging depois dos assets;
   a verificação final de TUF, produto e bootstraps públicos ocorre no mesmo job
   após o deploy; o produto é comparado byte a byte com o candidato aprovado.
10. antes da promoção final `1.0.0`, o workflow manual
    `.github/workflows/public-acceptance.yml` deve executar no Apple M3 contra o
    catálogo, bootstrap, mirrors e metadata TUF públicos. O recibo produzido é
    baixado por ID e validado por
    `maintenance/tools/verify_public_acceptance.py`; sem esse handoff a etapa
    `verify-public-acceptance` mantém a promoção final bloqueada. O verificador
    pós-publicação também exige a seção `public_acceptance` no recibo durável
    quando a versão é `1.0.0`. Essa seção inclui as coordenadas do artifact e os
    SHA-256 do recibo JSON, do instalador público e do catálogo TUF aceitos; o
    workflow final compara esses três valores antes de anexar a evidência M3.
    A aceitação de um RC não pode ser fabricada pelo próprio workflow de
    promoção.
11. antes da promoção final `1.0.0`,
    `.github/workflows/rc-soak.yml` deve ser despachado na ref exata do commit
    do RC sob uso. O run protegido consulta a issue canônica, exige sete dias
    completos de observações verdes e publica um único `report.json` como
    artifact imutável. O relatório `format: 2` registra explicitamente
    `macos-arm64`, o hardware do M3 e uma referência HTTPS de evidência para
    cada data observada. O input `observation_evidence_b64` é o JSON base64
    `{"YYYY-MM-DD":"https://..."}`; suas chaves precisam corresponder
    exatamente a `observed_dates`, sem lacunas ou duplicatas. A etapa
    `verify-soak` da promoção valida a procedência do run, o ID/nome/digest do
    artifact, a issue fechada, o hardware M3, as referências diárias e a
    identidade do RC; sem esse handoff a promoção final permanece bloqueada.
    As coordenadas entram na seção `soak` do `release-receipt.json`.

## Sequência protegida de despacho

Depois que esta ref estiver publicada no repositório remoto, a sequência de
execução é a seguinte. Os valores entre `<...>` são obrigatórios e devem ser
copiados dos resumos dos runs; não se deve inferir um ID de artifact pelo nome.

```sh
REPO=x86dx2/x86qw
CODE_COMMIT=<SHA-do-branch-que-contem-os-workflows>
RC_COMMIT=a8758ee27bebd7c72c24a31dc19335652e260c0a
RC_VERSION=1.0.0-rc.1
RC_CANDIDATE_SHA256=1552a896a0076dd2e347ed5b732b6dd31ba892292e1f9fb8c97fe9111f755bcb
RC_BUNDLE_SHA256=9600be7eb2ed14e23b2eeb079bd6aa0e4611f996be0c89741fda12587eb7fed8
RC_CANDIDATE_RUN_ID=31752738047
RC_CANDIDATE_ARTIFACT_ID=9201652983
RC_CANDIDATE_ARTIFACT_NAME=candidate-a8758ee27bebd7c72c24a31dc19335652e260c0a-31752738047-1

gh workflow run public-acceptance.yml --repo "$REPO" --ref "$CODE_COMMIT" \
  -f release_code_commit="$CODE_COMMIT" \
  -f candidate_version="$RC_VERSION"
```

O run de aceitação deve terminar verde no runner Apple M3. Registre seu
`run_id`, `artifact_id`, nome do artifact, `receipt_sha256`, `bundle_sha256` e
`catalog_sha256` do resumo. Em seguida, execute o drill TUF na mesma ref do
candidato, com o relatório produzido na máquina de custódia:

```sh
OPERATION_REPORT_B64="$(base64 < /secure/x86qw/records/tuf-drill.json | tr -d '\n')"
gh workflow run tuf-operation-drill.yml --repo "$REPO" --ref "$CODE_COMMIT" \
  -f candidate_artifact_id="$RC_CANDIDATE_ARTIFACT_ID" \
  -f candidate_artifact_name="$RC_CANDIDATE_ARTIFACT_NAME" \
  -f candidate_run_id="$RC_CANDIDATE_RUN_ID" \
  -f candidate_commit="$RC_COMMIT" \
  -f candidate_sha256="$RC_CANDIDATE_SHA256" \
  -f operation_report_b64="$OPERATION_REPORT_B64"
```

Depois de sete dias completos de uso sem P0/P1, feche a issue canônica e
despache `rc-soak.yml` com datas contínuas, as cinco flags verdadeiras e o
JSON base64 `date→URL HTTPS` das observações. Só então copie os IDs e digests
dos três handoffs para `release.yml` em modo `promote-1.0`. Cada run deve ser
consultado por `gh run view <run-id>` e validado pelo verificador do workflow;
um run local, uma issue aberta ou um nome de artifact sem ID não satisfaz o
gate.

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
explicitamente sob o ADR 0007, monitor de expiração e uma cerimônia manual
protegida (modo B deste ciclo); não há signer online agendado implementado.
`.github/workflows/tuf-monitor.yml` executa a
verificação autenticada de hora em hora, falha 6 horas antes do vencimento e
abre/atualiza uma única issue acionável quando o monitor falha; ele é somente
observabilidade e não substitui a cerimônia autorizada de renovação. Sem signer,
alertas entregues e um drill de recuperação observados, a 1.0 permanece NO-GO
mesmo que a validação técnica da TUF passe.
O drill operacional está em
[`docs/runbooks/tuf-operation.md`](tuf-operation.md) e usa
`maintenance/tools/tuf_operation_drill.py` em um diretório temporário; ele não
publica metadata e não aceita overwrite do relatório. A execução exige operador,
host de custódia e SLA de timestamp, registrados no campo não secreto
`operation`; isso documenta a responsabilidade do exercício, mas não substitui
a prova de custódia independente nem a operação sustentada em produção. Para a
promoção final, `.github/workflows/tuf-operation-drill.yml` registra o relatório
validado em `format: 2`, incluindo as versões `current`/`renewed` de
`timestamp`, `snapshot` e `targets`; `release-receipt.json` vincula seu
artifact, digest, operador, host e SLA. Sem esse handoff a promoção permanece
fail-closed.
Além disso, `release.yml` executa `monitor_public_tuf.py` com janela de alerta de
seis horas imediatamente antes da promoção final. Uma lease dentro dessa janela
exige renovação/recuperação manual e mantém a promoção em `NO-GO`.

O caminho de signer limitado está preparado como handoff protegido, mas não é
agendado nem publica sozinho. Com `TUF_TIMESTAMP_KEY_B64` configurada somente no
ambiente protegido `release`, o operador pode despachar:

```sh
TUF_SOURCE_WORKFLOW_COMMIT=335d9a062f8ce33b226a9892de82979828a0fd1b
TUF_SOURCE_RUN_ID=31750740500
TUF_SOURCE_ARTIFACT_ID=9200820996
TUF_SOURCE_ARTIFACT_NAME=tuf-metadata-a8758ee27bebd7c72c24a31dc19335652e260c0a-31750740500-1
TUF_SOURCE_ARTIFACT_DIGEST=sha256:6d4c5b560283ecf7b688ecd9320e20ed7c508d301de09a86db44765c7aeb98ac
TUF_TIMESTAMP_KEY_ID=<uma-das-duas-chaves-timestamp-da-root>

gh workflow run tuf-timestamp-renewal.yml --repo "$REPO" --ref "$CODE_COMMIT" \
  -f source_workflow_commit="$TUF_SOURCE_WORKFLOW_COMMIT" \
  -f source_run_id="$TUF_SOURCE_RUN_ID" \
  -f source_artifact_id="$TUF_SOURCE_ARTIFACT_ID" \
  -f source_artifact_name="$TUF_SOURCE_ARTIFACT_NAME" \
  -f candidate_commit="$RC_COMMIT" \
  -f timestamp_key_id="$TUF_TIMESTAMP_KEY_ID" \
  -f lease_hours=24
```

Esse workflow valida a procedência do artifact TUF, renova somente
`metadata/timestamp.json` e grava um artifact `published: false`; a publicação
continua exigindo uma cerimônia separada, mirrors convergentes e metadata-last.

## Rollback e publicação

Tags, assets e metadata publicados são imutáveis. Em divergência, interrompa a
promoção, preserve o candidato e siga `docs/runbooks/incident-response.md`;
não sobrescreva release, mirror ou metadata. Este runbook não executa upload,
cria tag, altera site público ou usa chave privada por conta própria.
