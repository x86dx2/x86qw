# ADR 0006 — Autenticação do catálogo com TUF

- **Estado:** aprovada e implementada no candidato do hotfix `0.7.5`
- **Data:** 2026-08-06
- **Issue:** [#48](https://github.com/x86dx2/x86qw/issues/48)
- **Baseline analisada:** `3bbc7a01faf8d472c5ccbab9233e05e9abadc379`
- **Implementação:** E2 mesclada na PR #69; root e metadata de produção entram
  pelo hotfix `0.7.5`, sob o waiver solo-maintainer do ADR 0007
- **Emenda owner-only (2026-08-16):** expiração máxima de `timestamp` = 7 dias
  enquanto a audiência for um mantenedor e `external-public=NO-GO`. O teto de
  24 horas volta a valer antes de qualquer anúncio de catálogo público.
- **Emenda owner-only (2026-08-27):** expiração de `snapshot` = 90 dias e de
  `timestamp` = 30 dias, no mesmo regime owner-only. A emenda anterior deixou as
  duas roles vencendo no mesmo instante, o que anulava o único fluxo protegido de
  renovação do projeto: `tuf-timestamp-publish` só pode alterar
  `metadata/timestamp.json`, e um timestamp renovado continua apontando para um
  snapshot já vencido. A partir daqui `snapshot` sobrevive ao `timestamp` por
  construção. Os tetos de 7 dias e 24 horas voltam a valer antes de qualquer
  anúncio de catálogo público, junto com a separação de custódia da tabela
  abaixo; o INC-001 de 2026-08-27 é a evidência que motivou a emenda.

## Contexto

O catálogo público atual usa HTTPS, registra tamanho e SHA-256 dos payloads e
preserva múltiplos mirrors. Esses controles detectam corrupção de um artefato
depois que o catálogo já foi aceito, mas não autenticam o próprio catálogo. Um
atacante que controle a origem ou um mirror pode apresentar catálogo e payload
maliciosos coerentes entre si, repetir metadata antiga ou manter clientes
congelados numa versão ainda válida para eles.

A CLI também possui fallbacks para arquivos em branches `main` mutáveis. Uma
branch não é uma identidade de release e não pode participar da nova fronteira
de confiança.

Esta E1 decide somente arquitetura, custódia e operação. Ela não cria chaves,
metadata, roles de fixture, dependências instaladas, código de verificação,
workflow, release ou publicação. Em particular, nenhum material do checkpoint
com expiração em `2026-08-08` é promovido ou usado como raiz de confiança.

## Decisão

O x86QW adotará The Update Framework (TUF), conforme a especificação 1.0, com
consistent snapshots. Não será criado protocolo equivalente nem verificador
criptográfico próprio.

### Biblioteca e primitivas

- o cliente usará `tuf.ngclient.Updater` de `python-tuf` 7.0.0 para executar o
  fluxo root → timestamp → snapshot → targets e validar alvos;
- a preparação e assinatura do repositório usarão as APIs de metadata do
  `python-tuf` e `securesystemslib[crypto]`, num ambiente isolado de cerimônia;
- E2 fixará versões e hashes de todas as dependências, registrará licenças e
  SBOM e empacotará o cliente sem depender de `pip` ou de pacote global no host;
- todas as roles usarão Ed25519; payloads e referências entre metadata usarão
  SHA-256;
- serialização, key IDs, contagem de assinaturas distintas e verificação de
  threshold pertencem às bibliotecas, não ao código x86QW;
- RSA-PSS próprio, canonicalização própria e fallback para “hash assinado” fora
  do TUF são proibidos.

A versão exata das bibliotecas precisa ser reconfirmada na abertura de E2. Uma
mudança de major, algoritmo ou formato exige nova aprovação deste ADR.

### Roles, thresholds e custódia

A coluna de alvo público é a política final. A coluna owner-only é o que vale
enquanto `external-public` for NO-GO, conforme as emendas registradas no topo.

| Role | Chaves autorizadas | Threshold | Custódia | Alvo público | Owner-only em vigor |
|---|---:|---:|---|---:|---:|
| root | 3 | 2 | três custodians distintos; chaves offline e não exportáveis | 365 dias | 365 dias |
| targets | 3 | 2 | três autoridades de release; uso offline e aprovação humana | 90 dias | 90 dias |
| snapshot | 2 | 1 | signer online isolado; uma chave ativa e uma reserva selada | 7 dias | 90 dias |
| timestamp | 2 | 1 | signer online isolado; uma chave ativa e uma reserva selada | 24 horas | 30 dias |

A expiração de `snapshot` é sempre estritamente maior que a de `timestamp`, nas
duas colunas. Igualá-las remove a capacidade de recuperar a cadeia pelo fluxo
protegido, e o teste
`test_initializes_root_and_generates_refreshable_catalog_repository` trava essa
ordem.

Cada chave pertence a uma única role. Nenhuma chave privada entra em Git, no
bundle, em logs, artefatos de CI ou secrets de jobs de pull request. Root e
targets não ficam em runners nem no provedor que hospeda o repositório. Pessoas
podem acumular funções somente se continuarem existindo dois custodians humanos
independentes para qualquer threshold 2-de-3; uma mesma pessoa nunca fornece
duas assinaturas para o mesmo threshold.

Antes da primeira cerimônia, o owner de segurança deve registrar fora do
repositório: custodian de cada key ID, meio físico, localização, acesso de
recuperação e substituto. O repositório recebe somente chaves públicas,
fingerprints e evidência sem segredo.

### Janelas operacionais

- no modo owner-only, `timestamp` expira em 30 dias e é renovado antes de
  restarem 7 dias; alerta crítico começa quando restarem 72 horas. A janela de
  alerta precisa superar com folga o pior intervalo observado entre execuções do
  monitor, que em 2026-08-27 foi de 10,25 horas; a janela anterior, de 6 horas,
  era menor que esse intervalo e por isso não avisava. O teto de 24 horas e a
  renovação no máximo a cada 12 horas voltam a valer antes de autorizar catálogo
  público, para limitar o freeze da chave online;
- no modo owner-only, `snapshot` expira em 90 dias. É renovado a cada mudança de
  targets e, sem mudança, antes de restarem 14 dias. Ele nunca vence antes do
  `timestamp`, para que a renovação isolada de timestamp seja capaz de recuperar
  a cadeia sem cerimônia completa;
- `targets` é renovado a cada promoção e, sem promoção, antes de restarem 30
  dias;
- `root` é revisado trimestralmente e renovado antes de restarem 120 dias;
- a data de expiração sempre é calculada a partir do instante da assinatura,
  nunca copiada de metadata anterior;
- um cliente fixa o relógio no início de cada refresh, como exige TUF. Metadata
  expirada encerra a atualização; conteúdo remoto não é promovido.

Expiração bloqueia instalar, atualizar ou reparar a partir da rede. Ela não
impede executar uma instalação local já validada e não autoriza voltar a um
catálogo sem assinatura.

### Endpoints e objetos assinados

As origens canônicas serão versionadas sob:

```text
https://qw.x86.com.br/api/v1/trust/metadata/
https://qw.x86.com.br/api/v1/trust/targets/
```

Os mirrors aprovados expõem os mesmos caminhos e bytes. O layout é o layout
TUF de consistent snapshots:

```text
metadata/<N>.root.json
metadata/timestamp.json
metadata/<N>.snapshot.json
metadata/<N>.targets.json
targets/catalog/<sha256>.catalog.json
```

Todas as versões de root permanecem disponíveis. `timestamp.json` é o único
ponteiro mutável de “current”: ele é assinado pela role timestamp e referencia
por versão, tamanho e hash um snapshot versionado e imutável. Não haverá
symlink, arquivo `latest` ou ponteiro `current` paralelo na fronteira de trust.

O snapshot TUF é assinado pela role snapshot e fixa a versão e o hash de
`targets.json`. A role targets autentica por tamanho e SHA-256 o catálogo como
alvo TUF. O campo de domínio `current` que já existe dentro do catálogo continua
indicando a versão atual do instalador, mas só é consumido depois que o catálogo
inteiro foi autenticado. Assim, tanto o snapshot quanto a decisão de current
estão cobertos pela cadeia assinada sem envelope criptográfico inventado pelo
projeto.

URLs de payload registradas no catálogo continuam usando múltiplos mirrors. O
payload mantém sua verificação atual por tamanho e SHA-256, agora ancorada num
catálogo autenticado. Um ciclo de refresh usa uma única origem de metadata; se
ela falhar, o ciclo é descartado antes de tentar outro mirror. Metadata de
origens diferentes nunca é misturada no mesmo ciclo.

### Estado confiável do cliente

A primeira root pública aprovada é incorporada como bytes no bundle da CLI e
passada no argumento `bootstrap` de `Updater`; cache gravável nunca é raiz de
confiança. E2 deve preservar esses bytes como membro declarado do zipapp e
validar sua identidade durante o build.

O cliente persiste em diretório privado as últimas metadata confiáveis. Versões
menores são rollback e são rejeitadas. Uma mesma versão com bytes diferentes é
equivocation e também é rejeitada. O cliente não apaga o último estado válido
quando um refresh falha, mas não usa metadata expirada para uma nova mutação
remota.

A atualização de root ocorre estritamente de `N` para `N+1`. Cada nova root é
verificada pelo threshold da root antiga e pelo threshold da nova. Saltos,
roots não ancoradas e remoção de versões intermediárias falham fechados.

### Rotação e revogação

- rotação planejada de root produz `N+1.root.json` assinado por 2-de-3 da root
  `N` e 2-de-3 da root `N+1`;
- toda versão intermediária de root é publicada de modo imutável antes da
  atualização de `timestamp.json`;
- trocar targets, snapshot ou timestamp exige uma nova root que remova a chave
  antiga e autorize a nova;
- após rotação de snapshot ou timestamp, clientes descartam o cache dessas
  roles conforme o fluxo TUF, impedindo fast-forward após comprometimento;
- revogação nunca reduz temporariamente um threshold abaixo da política. Se não
  houver quorum, publicação para e a recuperação é escalada;
- comprometimento do threshold de root não possui recuperação silenciosa: exige
  nova âncora distribuída fora da cadeia afetada e comunicação pública do
  incidente.

O procedimento completo está no
[runbook de operações de trust](../runbooks/trust-metadata-operations.md).

### Compatibilidade e corte

E2 removerá da CLI autenticada qualquer leitura de
`raw.githubusercontent.com/.../main` e `gitlab.com/.../raw/main`. Clientes que
suportam TUF consultam somente endpoints de trust e alvos autenticados.

Clientes legados não podem ser corrigidos retroativamente. Antes do corte, uma
release corretiva separada deve substituir seus fallbacks mutáveis por um
snapshot de compatibilidade imutável e fixado por SHA-256. Esse endpoint pode
manter o schema antigo, mas não recebe novas promoções depois do corte. Não há
downgrade automático de TUF para o catálogo legado.

`/api/v1/catalog.json` pode continuar como projeção pública para o site e para
observação humana; a CLI autenticada não o trata como autoridade.

## Ameaças e comportamento de falha

| Evento | Resultado obrigatório |
|---|---|
| assinatura ausente, inválida ou repetida | abortar antes de aceitar metadata |
| root não ancorada ou salto de versão | abortar e preservar a última root confiável |
| versão menor | classificar como rollback e abortar |
| mesma versão com conteúdo diferente | classificar como equivocation e abortar |
| metadata expirada | classificar como freeze possível e bloquear mutação remota |
| snapshot/targets incoerentes | classificar como mix-and-match e abortar |
| mirror indisponível | descartar o ciclo antes de tentar outro mirror |
| payload com tamanho ou SHA-256 divergente | descartar staging e preservar destino anterior |
| relógio local sem confiabilidade | não renovar confiança nem promover conteúdo |

Erros de trust são distintos de falhas de transporte e precisam ser visíveis em
saída humana e estruturada, sem incluir chaves privadas, tokens ou URLs com
credenciais.

## Gate para E2

E2 só pode começar depois de registrar na issue #48:

1. aprovação deste ADR por maintainers e owner de segurança;
2. revisão criptográfica independente do modelo, thresholds e janelas;
3. designação dos custodians e operadores, fora do repositório;
4. ensaio da cerimônia com material descartável que nunca será promovido;
5. estratégia aprovada para empacotar dependências, licenças e SBOM no zipapp;
6. plano de corte dos clientes legados sem branch `main` mutável.

A implementação deve começar com testes adversariais RED para assinatura,
threshold, expiração, rollback, freeze, equivocation, mix-and-match, rotação,
revogação, root não ancorada e divergência entre mirrors. Só depois entra código
produtivo. Chaves e metadata de produção são criadas na cerimônia aprovada, não
em fixtures nem durante testes.

## Consequências

A cadeia passa a tolerar comprometimento isolado de timestamp ou snapshot sem
autorizar payload arbitrário, e comprometimento de menos de duas chaves root ou
targets sem alcançar quorum. Expiração limita freeze, versões monotônicas
limitam rollback e consistent snapshots impedem mistura entre gerações.

O custo é operacional: dependências auditadas entram no artefato, há cache
confiável persistente, renovação contínua de metadata, custodians offline e
procedimentos de incidente. Indisponibilidade ou expiração falham fechadas para
atualização; disponibilidade nunca prevalece sobre a cadeia de confiança.

## Referências

- [The Update Framework Specification 1.0](https://theupdateframework.github.io/specification/latest/)
- [`tuf.ngclient.Updater`](https://theupdateframework.readthedocs.io/en/stable/api/tuf.ngclient.updater.html)
- [Deploy de root confiável no python-tuf](https://theupdateframework.readthedocs.io/en/stable/INSTALLATION.html#application-deployment)
- [Conformidade dos clientes TUF](https://theupdateframework.github.io/tuf-conformance/)
