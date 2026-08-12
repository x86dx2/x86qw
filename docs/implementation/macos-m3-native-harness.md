# Harness nativo macOS M3

## Estado

O harness e o agregado desta etapa são infraestrutura local. A execução real
do candidato continua sendo uma operação externa ao CI portátil; neste
checkpoint, uma execução local M3 foi concluída e gerou handoff v1 e relatório
de smoke v2. Nenhuma evidência foi assinada e nenhum estado de suporte ou
release foi promovido.

## Entrada executável

Cada candidato produzido pela etapa F vincula os bytes de runtime em
`runtime/{clients,servers,services}` e contém o contrato e o entrypoint Python
candidate-owned. O `candidate.json` é a autoridade da versão, commit e digest;
isso torna possível gerar um plano local determinístico, mas não substitui a
execução em hardware Apple M3 nem cria evidência de release por si só.

Um candidato capaz de executar o smoke precisa declarar, em `artifacts` de
`candidate.json`, tanto o contrato quanto o executável. O contrato fechado é:

```json
{
  "format": 1,
  "project": "x86qw",
  "platform": "macOS-ARM64",
  "protocol": "x86qw-native-case-v1",
  "entrypoint_artifact": "runtime/native-smoke/macos-arm64/x86qw-native-smoke"
}
```

Com essa capacidade explícita, o adaptador gera o plano fora do candidato:

```sh
python3 -m maintenance.tools.native_plan_adapter \
  --candidate /caminho/candidate \
  --expected-candidate-sha256 SHA256_DE_CANDIDATE_JSON \
  --entrypoint-contract runtime/native-smoke/macos-arm64/entrypoint.json \
  --output /fora/do/candidate/native-plan.json
```

O plano formato 2 é determinístico e fechado. Ele:

- vincula `version`, `commit` e SHA-256 dos bytes de `candidate.json`;
- registra exatamente os 18 casos canônicos, na ordem do contrato;
- vincula contrato e entrypoint aos artifacts declarados, por tamanho e SHA-256;
- usa somente o protocolo literal `--candidate-root {candidate} --case NOME
  --scratch-root {scratch} --receipt {receipt}`;
- não incorpora caminhos absolutos, timestamps ou comandos inferidos.

Os dois casos de instalação continuam usando esse mesmo protocolo fechado, mas
o entrypoint candidato-owned deriva a versão stable macOS do próprio candidato
e passa ao instalador as seleções explícitas de plataforma, canal, release e
ausência de componentes. A CLI mantém os menus quando essas opções não são
fornecidas; no harness, `stdin` permanece fechado (`DEVNULL`) e nenhum prompt
humano é reaberto. O `output-dir` também é normalizado para absoluto antes de
criar scratch, logs e recibos, evitando caminhos duplicados quando o chamador
usa um caminho relativo.

O primeiro caso de instalação usa ainda `--online-only`, extrai os dois
launchers do bundle candidato ao lado do `x86qw.pyz` e executa, na instalação
temporária criada pelo próprio caso, `help`, `version`, `changes` e
`migrate --dry-run`. O recibo nativo só é aprovado quando os quatro comandos
terminam com código zero, o help lista `changes` e `migrate`, e a versão do
launcher corresponde à versão do candidato. Isso comprova a CLI instalada sem
reutilizar uma instalação pessoal ou depender da árvore `quake-world`.

Contrato ausente resulta em exit code 2 e `not-run`, sem plano. Contrato
malformado, artifact não registrado ou bytes divergentes resultam em erro,
também sem plano. O adaptador nunca altera o candidato.

## Execução e agregado

Com candidato e plano exatos disponíveis em um host Darwin/arm64 cujo chip foi
observado explicitamente como Apple M3 (M3, M3 Pro, M3 Max ou M3 Ultra):

```sh
python3 -m maintenance.tools.native_macos_harness run \
  --candidate /caminho/candidate \
  --plan /caminho/native-plan.json \
  --output-dir /caminho/handoff-local
```

Antes da execução, o harness copia os bytes exatos do entrypoint para um
diretório privado sob `--output-dir`, valida tamanho e SHA-256 e torna somente
essa cópia executável. O artifact original permanece inalterado. O entrypoint
é executado com o interpretador Python explícito, sem depender de shebang ou
shell. Cada caso usa scratch próprio para extrações, mas compartilha o estado
da instalação; as pré-condições `clean → installed → uninstalled` são
registradas no recibo candidato-owned. O recibo contém o artefato cliente,
servidor, serviço ou instalador efetivamente selecionado, tamanho, SHA-256 e
resultado da execução. Candidato, contrato, entrypoint e artefatos são
revalidados antes e depois de cada caso.

O detector local usa `/usr/sbin/system_profiler SPHardwareDataType -json`,
com timeout, e conserva apenas chip e modelo. Falha de detecção, Apple M1/M2/M4,
arquitetura diferente, candidato ausente ou plano ausente produzem `not-run`;
nenhum desses estados é convertido em evidência nativa.

O mesmo comando também grava `release-smoke.json`, que é o relatório v2
diretamente consumível pelo fluxo de release. Ele contém os comandos efetivos,
asserções declaradas e os hashes dos recibos/stdout/stderr; não contém chaves,
segredos ou caminhos de custódia. O arquivo ainda não é evidência assinada.

Para transformar o relatório bruto em uma forma aceita pelo produtor de
evidência:

```sh
python3 maintenance/tools/native_release_smoke.py \
  --candidate /caminho/candidate \
  --platform macOS-ARM64 \
  --handoff /caminho/handoff-local/release-smoke.json \
  --output /fora/release-smoke-normalized.json
```

O handoff v1 continua disponível para auditoria detalhada e para o agregado
local legado. Somente um handoff `passed` pode alimentar o agregado
intermediário:

```sh
python3 -m maintenance.tools.native_handoff_evidence aggregate \
  --candidate /caminho/candidate \
  --handoff /caminho/handoff-local/handoff.json \
  --expected-candidate-sha256 SHA256_DE_CANDIDATE_JSON \
  --output /fora/do/candidate/native-evidence-pending.json
```

O agregado remove conteúdo e caminhos de logs, comandos, variáveis de ambiente
e caminhos absolutos de runtime. Ele conserva somente a identidade do
candidato, chip/modelo não sensíveis, estados fechados, recibos redigidos e
digests necessários para uma futura cerimônia protegida.

O arquivo produzido é sempre `status: pending`, `signed: false` e
`promotable: false`. Ele fica fora do candidato imutável, não pode se chamar
`release-evidence.json` e não satisfaz o gate de promoção da etapa F.

## Montagem protegida da evidência

O workflow `native-m3.yml` também valida o `release-smoke.json` no runner e
produz, fora do candidato, `records/macOS-ARM64.json` e
`release-evidence-body.json`. O artifact `native-m3-input` leva apenas esses
dois arquivos, o plano e o agregado pending; os logs e recibos brutos ficam no
scratch privado do runner.

O custodiante assina os bytes exatos de `release-evidence-body.json` fora do
repositório e devolve um envelope JSON público com `body_sha256` e
`{keyid,sig}`. O workflow `sign-native-evidence.yml` verifica a proveniência do
run M3, baixa o candidato e o input, executa:

```sh
python3 -m maintenance.tools.assemble_release_evidence assemble \
  --candidate candidate \
  --records-dir native-input/records \
  --body native-input/release-evidence-body.json \
  --signatures signatures.json \
  --trust-root m3-root.json \
  --output signed-evidence/release-evidence.json
```

O comando não possui modo de gerar assinatura nem aceita chave privada. Sem
`--trust-root`, a CLI falha antes de produzir o agregado; depois da validação,
o artifact `native-m3-signed` é o único que o workflow de promoção aceita.
