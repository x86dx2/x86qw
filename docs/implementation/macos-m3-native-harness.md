# Harness nativo macOS M3

## Estado

O harness e o agregado desta etapa são infraestrutura local. Nenhum smoke M3
do candidato foi executado, nenhuma evidência foi assinada e nenhum estado de
suporte ou release foi promovido.

## Entrada executável

O candidato produzido pela etapa F em `main@216e90b62c8dabdd749e462a884d115cb5993253`
vincula os bytes de runtime em `runtime/{clients,servers,services}` e contém o
contrato e o entrypoint Python candidato-owned. Isso torna possível gerar um
plano local determinístico, mas não substitui a execução em hardware Apple M3
nem cria evidência de release por si só.

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

Somente um handoff `passed` pode alimentar o agregado intermediário:

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
