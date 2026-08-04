# Fronteira segura de arquivos — implementação da issue #49

**Baseline inicial:** `b833ba45e08a9de644dc7368f82c905522a0a558`

**Versão pública preservada:** `0.7.1`

**Estado:** implementação, regressão local e matriz CI concluídas; PR 3 não
publicada

## Problemas confirmados no baseline

Antes da mudança, o mesmo ZIP/PK3 tinha interpretações diferentes:

- `manager.py` extraía enquanto ainda validava e restaurava modos vindos de
  `external_attr`;
- `services.py` tinha um validador próprio mais forte, mas ainda extraía por um
  caminho exclusivo;
- `gameplay.py` lia mapas, assets KTX e `qwprogs.dat` diretamente;
- ferramentas de componentes e releases tinham readers próprios;
- `unzip` e `Expand-Archive` ignoravam o contrato Python nos bootstraps.

Foram reproduzidas aceitações indevidas de colisão por caixa, colisão Unicode,
`CON.txt`, membro FIFO, conflito `a` arquivo + `a/b` e barra invertida. O risco
era falha dependente do sistema, leitura de arquivo semanticamente ambíguo ou
mutação parcial antes de descobrir o último membro inválido.

## Implementação

O contrato detalhado está no
[ADR 0002](../adr/0002-fronteira-unica-de-arquivos.md). A mudança cria o
pacote incremental `x86qw_runtime.io.archive` e migra:

- instalação e verificação de clientes, componentes, dados-base e handoff da
  CLI;
- descoberta de mapas, inventário KTX e leitura de gamecode;
- scan e materialização efêmera de PK3 para MVDSV;
- readers, rewrites e builders de componentes e releases;
- validação do zipapp e do bundle externo;
- testes do site que inspecionam bundles;
- bootstraps Unix e PowerShell.

Os builders continuam escrevendo ZIP determinístico com `zipfile` em modo `w`.
Eles não constituem um reader alternativo: o arquivo produzido precisa passar
por `scan_archive()`, `validate_installer_bundle()` ou pelo contrato histórico
versionado equivalente antes de ser aceito. A escrita ocorre em staging sob uma
raiz explícita, seguida de publicação sem substituição.

## Fluxos resultantes

### Leitura sem extração

```text
fonte regular
  → cópia limitada para snapshot privado em disco
  → pre-scan e streaming integral no mesmo snapshot imutável
  → ArchivePlan ligado à identidade e SHA-256 da fonte
  → revalidação do plano
  → leitura limitada do membro solicitado
```

Um membro hostil não selecionado também encerra o fluxo antes que o conteúdo
solicitado seja entregue.

### Extração de payload

```text
fonte regular
  → snapshot privado limitado e revalidação da fonte
  → scan integral dos bytes imutáveis
  → destino inexistente
  → staging irmão privado
  → extração + CRC/SHA-256 + fsync + modos canônicos
  → revalidação integral final da fonte
  → rename exclusivo
  → confirmação da identidade publicada (commit)
  → fsync do diretório pai
```

Qualquer falha anterior à promoção deixa o destino ausente e remove somente o
staging cuja identidade ainda possa ser confirmada. Depois que a identidade do
staging é confirmada no destino, o commit é irreversível: uma falha tardia
preserva o diretório publicado e qualquer arquivo pessoal criado nele. Uma
promoção inconclusiva nunca autoriza percorrer ou remover o nome público.

### Materialização de serviço

```text
PK3
  → scan integral
  → extração canônica em staging privado
  → preflight de todos os conflitos no contexto do MVDSV
  → cópias atômicas journalizadas
  → cleanup do staging
```

Isso preserva a política histórica de não sobrescrever arquivo pessoal
diferente sem manter um descompressor dentro de `services.py`.

No Windows, a materialização mantém handles dos ancestrais sem compartilhamento
de remoção, recusa reparse points e registra identidade por volume +
`FILE_ID_128`. O arquivo é escrito em staging exclusivo, verificado e promovido
por `MoveFileExW` sem substituição. Cleanup e rollback atuam no mesmo handle
confirmado com `SetFileInformationByHandle`; não dependem de hardlink nem de
`DeleteFileW` por caminho. Os testes portáveis usam uma API fake com o mesmo
contrato, e os casos nativos permanecem obrigatórios no job Windows.

No POSIX, arquivos criados pela sessão passam por rename exclusivo e atômico
para uma quarentena no mesmo diretório; não existe uma sequência pública de
`stat` + `unlink`. O conteúdo é validado novamente pelo descritor aberto e uma
divergência restaura o nome sem substituir conteúdo concorrente. Linux usa
`renameat2(RENAME_NOREPLACE)`, macOS usa `renameatx_np(RENAME_EXCL)` e outro
POSIX sem a primitiva falha fechado. Hashes de cleanup são limitados ao tamanho
exato registrado no journal; journals legados sem `expected_size` preservam o
arquivo. A fronteira não promete isolamento contra outro processo já
comprometido sob o mesmo UID com descritor gravável, `mmap` pré-aberto ou acesso
direto ao nome aleatório da quarentena, uma exclusão que POSIX não fornece de
modo portátil.

### Bootstrap

Os quatro scripts de bootstrap contêm a mesma projeção Base64 do módulo
canônico, verificada byte a byte pelo builder e pelos testes. O helper é criado
em temporário privado e valida o contrato exato do bundle antes de executar
`x86qw.pyz`; nenhuma chamada a `unzip` ou `Expand-Archive` permanece.

## Limites e modos

Os limites canônicos são 512 MiB para o arquivo-fonte compactado, 32 MiB para
os metadados do diretório central, 4.096 membros, 128 MiB por membro, 512 MiB
no total expandido, profundidade 16, caminho de 240 unidades UTF-16 e razão
máxima de compressão 500:1. Stored e Deflate são os únicos métodos aceitos.

Antes de `zipfile` materializar a lista central, um pre-scan estrutural limita
os bytes dos metadados, percorre cada registro e compara sua contagem real com o
EOCD. Isso evita tanto alocação sem limite quanto bypass por contagem
subestimada. Pre-scan e `ZipFile` operam sobre o mesmo snapshot privado; uma
fonte concorrente não troca os bytes entre as duas etapas e toda leitura da
fonte é interrompida no teto mais um byte. Caracteres Win32 proibidos e
atributos DOS de device ou reparse point também são recusados em qualquer
plataforma.

Diretórios e executáveis declarados recebem `0755`; os demais arquivos recebem
`0644`. Modos do ZIP não são restaurados. Durante staging, diretórios começam
privados e arquivos são criados exclusivamente com `0600`.

## Registro da mudança

| Área | Antes | Candidato corretivo |
|---|---|---|
| Scan | leitores independentes | `scan_archive()` único |
| Plano | validação implícita e parcial | `ArchivePlan` imutável |
| Leitura | membro selecionado podia ignorar vizinho hostil | arquivo inteiro validado primeiro |
| Extração | write durante validação | staging completo antes da promoção |
| Modos | podiam vir de `external_attr` | somente `0644`/`0755` declarados |
| Serviços | validador e extração próprios | scan/extract canônicos + materialização journalizada |
| Serviços Windows | fallback sem cleanup reversível | handles, identidade persistente e unlink exato |
| Bootstrap | `unzip`/`Expand-Archive` | projeção exata da fronteira canônica |
| Builders | escrita direta no nome final | snapshot estável, staging sob raiz explícita, validação e hardlink sem substituição |
| Regressão | proibição por convenção | gate estático de readers/extratores paralelos |

## Compatibilidade e migração

Não existe migração de estado ou de payload. Os 59 ZIP/PK3/PYZ existentes no
inventário inicial cabem nos novos limites e passam no scan. A estrutura
instalada, os hashes de arquivos válidos, os comandos e o gameplay são
preservados.

O histórico imutável do instalador mantém seus dois layouts reais: seis membros
entre `0.1.0` e `0.1.19`, e sete a partir de `0.1.20`. Ambos exigem conjunto
exato de membros, identidades externas coerentes e identidade interna do
`x86qw.pyz`; o layout moderno continua obrigatório para bundles novos.

Os builders escrevem em arquivo privado sob uma raiz de saída explícita,
rejeitam symlink ou reparse point nos pais gerenciados, validam o snapshot e
publicam por hardlink sem substituir nome existente. Uma falha posterior à
criação do link preserva o alvo para inspeção e impede manifesto, registro e
publicação. O ponteiro `latest` só é criado quando ausente ou aceito quando já
possui exatamente o valor esperado; promover um valor divergente permanece
bloqueado até a transação imutável da PR 10.

Arquivos antes tolerados somente por validação incompleta passam a falhar antes
do primeiro write de extração ou payload visível. Esse é o único contrato
deliberadamente incompatível.

## Evidência atual

Na validação local deste candidato, a suíte de manutenção reportou
`Ran 695 tests` e `OK (skipped=15)` tanto no Python 3.10 quanto no Python 3.14;
os cinco testes do site também reportaram `OK`. Dos 15 skips locais, quatorze
exigem o runner Windows e um é o smoke de rede opt-in. `verify`, Git LFS,
`git diff --check`, os parsers dos bootstraps e o dry-run do Worker também
passaram.

A execução
[#30856293818](https://github.com/x86dx2/x86qw/actions/runs/30856293818)
repetiu com sucesso:

- testes positivos, negativos e adversariais da fronteira;
- regressão de instalador, serviços, gameplay, componentes, recipes,
  launchers e site;
- scan de todos os arquivos distribuídos;
- gate estático sem reader/extrator paralelo;
- `verify`, Git LFS, `git diff --check` e dry-run do Worker;
- matriz Ubuntu, macOS e Windows com Python 3.10 e 3.13, incluindo os casos
  nativos Windows.

Os sete jobs passaram. Nos dois jobs Windows, os casos nativos aceitaram fontes
`.exe`/`.cmd`, rejeitaram reparse points e validaram as identidades reais de
Python 3.10 e 3.13. Essa evidência classifica o contrato de arquivos como
multiplataforma completo, mas não substitui smoke nativo de ezQuake, MVDSV, QTV
ou QWFWD.

## Riscos residuais

- a DACL privada no Windows foi implementada no candidato da PR 4 conforme o
  [ADR 0003](../adr/0003-dacl-privada-windows.md) e validada nativamente nos
  runners Windows com Python 3.10 e 3.13; o smoke de runtime sob conta padrão
  permanece pendente;
- trust metadata pertence ao PR 9;
- promoção transacional de candidato e atualização segura de `latest` pertencem
  à PR 10; este candidato falha fechado diante de um ponteiro divergente;
- smokes nativos e evidência do candidato pertencem aos PRs 10 e 11;
- TAR não é extraído para o filesystem pelos fluxos atuais e permanece fora do
  contrato desta PR;
- a sincronização build-time dos bootstraps detecta trocas, não segue symlink e
  publica por replace atômico, mas a biblioteca padrão não oferece CAS portável
  sobre um pathname existente; o build exige checkout exclusivo, e essa
  garantia operacional será formalizada pela promoção imutável da PR 10;
- `0.7.1` continua sendo a release pública e não recebe esta implementação.

Nenhuma versão, catálogo, bootstrap público implantado ou bundle publicado foi
alterado por esta nota.
