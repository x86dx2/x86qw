# Estabilização obrigatória até 1.0 — estado do código corretivo

Esta nota registra o trabalho local iniciado sobre a tag pública
`x86qw-installer-0.7.3`, sem promover ou alterar qualquer bundle já publicado.
O checkout corretivo parte do HEAD público analisado
`3bbc7a01faf8d472c5ccbab9233e05e9abadc379`; as alterações desta nota ainda
são um snapshot local não publicado.

## Fronteira da mudança

O objetivo desta linha é tornar os contratos auditáveis antes de uma futura
1.0.0. Nenhum runtime, engine, jogo, mod, mapa, serviço persistente ou recurso
de gameplay novo foi incorporado. FTEQW e a estratégia técnica dos PAKs estão
fora do escopo.

## Estado por frente

| Frente | Estado funcional | Evidência atual | Bloqueio restante |
|---|---|---|---|
| PR 1 — bootstrap Python | publicada em 0.7.1 | matriz portável e testes de launchers | nenhum P0 conhecido |
| PR 2 — downloader limitado | publicada em 0.7.3 | HTTPS, limites, deadline, retry e regressão adversarial | nenhum bloqueio nativo neste fluxo Mac |
| PR 3 — ZIP/PK3/PYZ único | publicada em 0.7.3 | `scan_archive`/`extract_archive`, LFS e compatibilidade Windows | nenhum bloqueio nativo neste fluxo Mac |
| PR 4 — DACL Windows | publicada em 0.7.3 | adaptador nativo e casos de herança/reparse | compatibilidade Windows preservada, não executada neste checkout |
| PR 5 — stable macOS | publicada em 0.7.3 | bundle upstream preservado; sem assinatura ad hoc | assinatura/upstream e documentação do bundle |
| PR 6 — fronteiras runtime | integrada e pausada | ownership canônico, zipapp e regressão | revisão arquitetural final |
| PR 7 — contratos 1.0 | local, não publicada | SemVer, schemas, envelopes JSON e códigos estáveis | compatibilidade externa e aprovação |
| PR 8 — migração 1.0 | local, não publicada | fixtures 0.7.0–0.7.3, normalização de `nquake-ktx`, aposentadoria diagnosticável de `nquake-sounds`, rollback e ownership | fixtures de releases futuras reais |
| PR 9 — trust metadata | local, fail-closed | cadeia root/current/snapshot, papel `evidence`, RSA-PSS, threshold, expiração, anti-rollback e vetores positivos/negativos | chave de produção, revisão criptográfica e cerimônia |
| PR 10 — candidato sem rebuild | local, fail-closed | manifest fechado com hashes dos bytes de checksums/ownership/SBOM/provenance, ownership explícito dos builders, SBOM SPDX, provenance, promoção local sem overwrite e verificação individual de todos os mirrors antes do publicador | publicação de metadata e trust de produção |
| PR 11 — compatibilidade nativa | preservada, não operacional | schemas, ferramentas e nomes `Linux-X64`, `macOS-ARM64`, `Windows-X64` e `macOS-X64` permanecem disponíveis sem executar smokes | nenhuma execução nativa faz parte deste fluxo Mac |
| PR 12 — governança | parcial | LICENSE, NOTICE, carriage dos avisos em bundles modernos, Dependabot e runbooks; validação e promoção operacionais diretamente no Mac, sem workflow, runner ou environment externo | versionar o `CODEOWNERS` em `main` e revisar a publicação remota opcional |

## Regra de publicação

### Avisos da distribuição própria

A partir de `1.0.0`, o builder inclui `LICENSE` e `NOTICE` byte a byte no ZIP
externo e na projeção `x86qw.pyz`. O contrato de arquivo exige exatamente nove
membros no ZIP moderno e valida a igualdade entre as duas camadas. O layout de
`0.1.20` até `0.7.x` continua com sete membros e os bundles publicados não são
reconstruídos. Registros novos do instalador usam SPDX `MIT`; registros
históricos continuam com os metadados publicados originalmente.

Não há workflow de release nem CI externo nesta linha. A preparação, verificação
e promoção local ocorrem diretamente no Mac sem `native-release`,
`release-evidence`, smokes nativos, runners ou artifacts externos. O candidato
não gera `release-evidence.json`; esse documento só é compatibilidade opcional
quando fornecido explicitamente. A promoção de assets recusa substituição e
mantém a identidade por commit, hashes, ownership, SBOM e provenance.

Metadata pública, mirrors, P0/P1 e o ponteiro `current` pertencem a uma
publicação remota opcional posterior. Essa proteção é separada da promoção
local e não transforma a presença das plataformas no catálogo em afirmação de
que elas foram executadas.

Uma verificação pós-publicação opcional consulta somente o contrato HTTP que
existe no site:
`/api/v1/catalog.json`. Ele exige a lista `packages` e exatamente um registro
`x86qw-installer` com a versão candidata e `current: true`. Em seguida, o
helper `maintenance/tools/public_install_smoke.py` (verificação de instalação
pública) baixa o mesmo bundle por
SHA-256, extrai-o com a fronteira canônica e executa uma instalação real em
um diretório limpo com espaço e Unicode, usando `--non-interactive`,
`version --json` e `verify --json`. Essa verificação roda somente no Mac quando
uma publicação remota tiver sido autorizada e exige uma URL base HTTPS para
`root/current/snapshot`; sem essa cadeia pública, ela falha fechada. O ponteiro
`current` assinado continua sendo validado dentro da cadeia de trust;
não há um endpoint público separado `current.json`.

Antes de uma publicação remota opcional, `maintenance/tools/verify_release_mirrors.py`
valida todos os URLs HTTPS declarados no catálogo assinado individualmente. A
operação não usa o fallback de mirrors do instalador: qualquer 404, timeout,
divergência de tamanho/SHA-256, query string, credencial ou duplicata interrompe
a publicação de `snapshot/current`.

As duas fronteiras locais de materialização (`prepare` e `promote`) usam criação
sem substituição; `promote` ainda reverifica o candidato de origem e o snapshot
copiado antes de torná-lo visível. Uma corrida que crie o destino ou altere a
origem faz a operação falhar, preservando os bytes concorrentes.

O `candidate.json` usa o formato 2 e contém um mapa fechado `metadata` com tamanho e
SHA-256 exatos de `checksums.txt`, `ownership.json`, `sbom.spdx.json` e
`provenance.json`. O ownership é produzido pelos builders, cobre exatamente os
artefatos do candidato e mantém archives mistos, upstream e PAKs como
`NOASSERTION`; entradas próprias só recebem `MIT` com URL imutável da licença.
Assim, os quatro documentos vinculados e o próprio `candidate.json` ficam
disponíveis para uma publicação permanente posterior e são conferidos pelo
verificador de estado da release; não dependem de artifact efêmero externo. Os schemas e
ferramentas de evidência nativa permanecem documentados no runbook legado, mas
não fazem parte da cadeia operacional deste checkout.

## Erro de entitlements observado

A mensagem `Os entitlements do ezQuake não contêm um plist válido` pertence ao
instalador `0.7.2`, cujo parser tratava a ausência de entitlements como erro. A
linha `0.7.3` já aceita a saída legítima de `codesign` sem plist e continua
rejeitando um plist realmente malformado. O teste correspondente e a verificação
do bundle stable local permanecem na regressão; o cache do instalador antigo deve
ser descartado ou atualizado pelo catálogo para usar a `0.7.3`.

## Inicialização KTX no cliente

O arquivo de configuração KTX usado para uma partida local é efêmero e precisa
permanecer disponível até que o ezQuake tenha assumido a sessão. A configuração
é criada com journal privado e, assim que o launcher recebe um `Popen` válido,
o controlador do journal é transferido para o processo guardião do cliente
(POSIX) ou para o processo do cliente (Windows). Isso elimina a corrida em que
o launcher terminava e uma execução seguinte removia o arquivo antes de o
ezQuake consumi-lo. A recuperação só remove a configuração depois de confirmar
que a identidade do controlador transferido morreu; enquanto o cliente está
vivo, o arquivo permanece disponível.

O handoff exige um `Popen` cuja identidade possa ser autenticada; não existe
mais um caminho alternativo baseado apenas em uma janela de espera para mocks
ou launchers legados. Se a transferência não puder ser comprovada, o launcher
encerra somente o processo recém-criado e preserva o journal quando a
finalização também for inconclusiva. Um encerramento real durante a janela de
startup retorna seu código, remove a configuração efêmera e não cria payload
permanente.

O bundle público `0.7.3` não é alterado nesta linha e ainda contém a lógica
anterior; a transferência de ownership só estará disponível em um pacote futuro
aprovado. A verificação local em macOS, em janela, com KTX Duel, mapa
`aerowalk`, um Frogbot e log de console confirmou a abertura sem a mensagem
`O ezQuake encerrou antes de carregar a configuração KTX`; essa validação local
no Mac pertence ao fluxo atual e não é apresentada como evidência de outras
plataformas.

## Contrato JSON do Hub

O envelope JSON 1.0 agora valida endpoints com uma gramática única para
`IPv4:porta`, `hostname:porta` e `[IPv6]:porta`, inclusive no identificador de
stream QTV. Formas ambíguas de IPv6, portas fora de 1–65535 e controles Unicode
(`Cc`, `Cf`, `Cs` ou U+FFFD) são rejeitados antes de qualquer valor entrar no
envelope; texto Unicode legítimo continua permitido. Os casos adversariais
estão em `maintenance/tests/test_cli_json_contract.py`.

Os contratos SemVer aceitam `alpha`, `beta` e `rc` para estados, componentes e
metadados. O bundle público do instalador continua deliberadamente estável
(`x.y.z`) por compatibilidade com os validadores e bootstraps da série 0.7.x;
A validação local do candidato não é um workflow remoto e não aplica um gate
externo para prereleases. O bundle público do instalador continua
deliberadamente estável (`x.y.z`) por compatibilidade com os validadores e
bootstraps da série 0.7.x; qualquer mudança na ordenação de `latest` ou no
formato dos quatro bootstraps deve ser aprovada separadamente.

## Limitações declaradas

- A preparação/rehearsal de candidato não é publicação e pode executar com
  P0/P1 em aberto, somente no Mac, sem depender de ambientes, rede ou smokes
  nativos; ela produz um candidato verificável sem `release-evidence.json`.
- `check_release_blockers.py`, trust metadata, verificação de mirrors e
  publicação remota continuam disponíveis como operações opt-in posteriores;
  nenhuma delas é chamada por `release_candidate.py verify/promote`.
- a chave presente em `maintenance/inventory/trust/` é fixture pública de teste,
  não chave privada de produção;
- a cadeia de trust vence deliberadamente em curto prazo até uma cerimônia de
  renovação ser executada;
- a matriz portável não executa validação gráfica, de rede ou de Gatekeeper;
- nenhuma versão 1.0.0 foi preparada, promovida ou publicada;
- o catálogo, o bootstrap e os bundles públicos da `0.7.3` permanecem
  imutáveis.

## Validação deste checkout

Após integrar os contratos 1.0, migração, trust e candidato:

- `PYTHONDONTWRITEBYTECODE=1 ./maintenance/manage.py verify`: a execução
  integral deste checkout passou nas suítes de manutenção e de site; ela foi
  executada em uma sessão isolada para não interferir nos runtimes gráficos
  locais ativos;
- a suíte de manutenção descoberta neste checkout contém 1.474 testes: 1.436
  passaram e 38 foram ignorados explicitamente. Os skips continuam restritos a
  casos nativos Windows/macOS, PowerShell ou rede opt-in; eles não são
  apresentados como smokes de runtime. O cenário de cancelamento do resolver
  DNS também valida que erros de coleta não mascaram o deadline;
- os cinco testes do site também passaram; a validação integral não alterou
  arquivos do checkout nem os bundles públicos da `0.7.3`;
- a mensagem antiga de entitlements foi reproduzida somente no bundle `0.7.2`;
  a regressão da linha `0.7.3` passou com o bundle stable local.
- O último workflow `Validate` público da `main` (run `30954949561`) é um
  registro histórico de CI; o checkout corretivo valida o caminho ativo somente
  no Mac e não depende de rerun ou confirmação nativa em runner Windows.
