# Runbook — evidência nativa

O executor `maintenance/tools/native_macos_harness.py` é o único caminho que pode
produzir um handoff `macOS-ARM64` aprovado. Ele exige Mac M3 (macOS arm64), Apple M3,
usuário padrão, candidato verificado e plano fechado ligado ao SHA do manifest.
O handoff preserva uma atestação fechada de hardware (`chip` e `model`); um
relatório que declara apenas `macOS`/`arm64`, sem confirmar Apple M3, é rejeitado.
Cada caso usa `subprocess.Popen(..., shell=False)`, registra exit code e só
passa quando cada assertion possui um arquivo produzido após o processo. Cada
caso também precisa de exatamente um artefato `case-attestation` contendo o
nonce transitório recebido pelo processo; isso impede que um arquivo estático
do candidato seja contado como observação daquela execução. Casos são
executados em grupos POSIX isolados e timeouts encerram os descendentes.
No caso QWFWD, o config candidato é copiado e seu `net_port` é sobrescrito
somente no scratch por uma porta efêmera; o arquivo original e seu digest não
são alterados, evitando colisão com qualquer serviço pessoal em `30000`.
Depois do `j` de conexão, o caso envia e aguarda uma resposta `print` encaminhada
pelo servidor antes do primeiro payload do cliente; isso elimina a corrida de
handshake que poderia descartar o primeiro datagrama e produzir um falso negativo.

## Casos obrigatórios

O plano deve cobrir exatamente:

- instalação limpa e instalação sobre árvore existente em caminho com espaço e
  Unicode;
- stable e nightly com janela, mapa carregado e encerramento;
- KTX, Final Arena, Pro-X, Team Fortress e TD2 com gamecode e mapa;
- MVDSV com MVD válido;
- QTV com HTTP/upstream/stream legível;
- QWFWD com encaminhamento UDP real;
- update, upgrade, verify, repair, cleanup e uninstall.

Um exit code zero isolado não é suficiente. O plano deve ligar cada assertion a
um artifact observável; o handoff é então validado por
`native_release_smoke.py`, convertido em registro por
`native_release_evidence.py` e consumido pelos gates de release. O corpo
canônico liga por SHA-256 o manifest do candidato; a proveniência do run e do
artifact é validada por `verify_external_handoff.py`. O
`release_evidence_binding.py` continua disponível para auditorias privadas que
transportem também os arquivos brutos do handoff, mas não é alegado como parte
do artifact redigido atual.

## Limites honestos

- CI portátil não é smoke nativo;
- Linux-X64, Windows-X64 e macOS-X64 permanecem `preview` neste ciclo;
- stable macOS continua `conditional` enquanto assinatura Developer ID e
  notarização não estiverem comprovadas;
- um relatório `not-run`, sintético, `fixture`, `mock` ou `dry-run` nunca é
  convertido em evidência de release;
- o harness não registra serial, token, cookie, senha ou caminho de custódia.

O plano M3 é uma entrada protegida do runner e não é inventado pelo workflow.
Se estiver ausente ou apontar para outro candidato, a promoção para antes da
aprovação e nenhum asset ou metadata é publicado.

O workflow `.github/workflows/native-m3.yml` recebe o `candidate_run_id`, o
`candidate_artifact_id`, o nome exato
`candidate-<commit>-<run_id>-<run_attempt>`, o commit e o SHA-256 de
`candidate.json`. Antes do download, ele consulta a API do GitHub e confirma
que nome, ID, run, workflow e commit são a mesma publicação; depois baixa o
artifact imutável da execução de `build-once`, verifica o SHA de `candidate.json`, gera o plano com o
contrato que está dentro do candidato e executa esse mesmo harness. Ele não usa
`quake-world/`, não instala a árvore pessoal do mantenedor e não assina
evidência. O artifact produzido é deliberadamente `pending`,
`signed: false` e `promotable: false`; a cerimônia protegida de assinatura é
uma etapa posterior e obrigatória. O upload `native-m3-input` contém somente o
agregado redigido, o plano, o registro de plataforma sem assinatura e o corpo
JSON canônico que será assinado; stdout, stderr, recibos brutos e o runtime
temporário permanecem no scratch privado do runner.

O workflow `.github/workflows/sign-native-evidence.yml` é o adaptador protegido
da custódia. Sob o waiver do ADR 0007, o único mantenedor pode executar a
assinatura fora da CI; isso não cria revisão humana independente. O workflow
verifica por API que o artifact do `build-once` e o run
`native-m3` pertencem ao repositório, ao commit e aos IDs/nomes informados,
baixa os mesmos bytes, recebe apenas um
envelope público de assinaturas (`signatures.json` em base64) e exige
`M3_TRUST_ROOT_B64` no ambiente protegido. `maintenance.tools.assemble_release_evidence`
recusa qualquer chave privada, exige o `--trust-root`, confere o hash do corpo
canônico e autentica o agregado antes de o workflow produzir
`native-m3-signed`. Esse workflow não assina: a assinatura é gerada pelo
mantenedor autorizado fora da CI e a CI verifica somente o envelope público.
A root pública versionada é `maintenance/trust/m3-root.json`; a política
criptográfica continua 2-de-3 e cada evidência precisa de duas chaves distintas.
As três sementes privadas ficam no cofre Proton Pass `x86QW`; o mantenedor único
é o custodiante de todas elas, sem alegação de independência humana.
