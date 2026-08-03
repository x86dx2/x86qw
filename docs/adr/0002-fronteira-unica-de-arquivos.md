# ADR 0002 — Fronteira única de arquivos ZIP, PK3 e PYZ

**Status:** aceito no código corretivo da PR 3; matriz CI concluída e sem
publicação

**Data:** 2026-08-03

**Issue:** [#49](https://github.com/x86dx2/x86qw/issues/49)

## Contexto

O instalador, os serviços, o gameplay e as ferramentas de manutenção liam ou
extraíam ZIP/PK3 por caminhos independentes. As garantias divergiam: alguns
consumidores validavam somente o membro selecionado, outros escreviam enquanto
a validação ainda ocorria, e os bootstraps delegavam a extração a `unzip` ou
`Expand-Archive`.

Essa fragmentação permitia que o mesmo arquivo fosse aceito por um consumidor e
recusado por outro. Também deixava falhas tardias — colisões de nomes, membro
especial, ZIP bomb ou CRC divergente no final — acontecerem depois do primeiro
write. A issue #49 exige um preflight integral e uma única interpretação
portável para todos os arquivos ZIP, PK3 e PYZ.

## Decisão

`x86qw_runtime/io/archive.py` é a fonte canônica stdlib-only. Ela expõe:

- `ArchiveLimits`, com os limites fechados do produto;
- `scan_archive()`, que lê integralmente o arquivo e só então produz um
  `ArchivePlan` imutável;
- `read_archive_member()` e `read_archive_members()`, que revalidam o plano
  antes da leitura;
- `extract_archive()`, que extrai para staging privado, sincroniza e promove o
  diretório completo sem substituir destino existente;
- `validate_installer_bundle()`, que valida também a identidade e o layout
  exatos do bundle público do instalador.

O módulo não importa `manager.py`, `services.py` nem `maintenance`. Os
consumidores convertem `ArchiveError` para o erro de sua própria camada. Writers
determinísticos podem continuar usando `zipfile` explicitamente em modo de
escrita, mas o artefato produzido volta pela fronteira canônica antes de ser
aceito.

### Preflight e vínculo do plano

`scan_archive()` acontece antes do primeiro write de extração ou payload
visível e percorre todos os membros, mesmo quando o consumidor deseja ler
somente um deles. O snapshot privado e limitado faz parte do preflight. O plano
registra SHA-256 da fonte e de cada membro, tamanho, CRC, método de compressão,
tipo e modo canônico. Uma fonte em disco também fica vinculada à identidade
obtida de `lstat`/descritor; precisa ser arquivo regular e não symlink.

Cada operação copia a fonte, com limite estrito de uma unidade além do teto,
para um snapshot temporário privado em disco. A fonte é revalidada antes e
depois do consumo, enquanto o pre-scan, `ZipFile` e o streaming dos membros
observam exatamente os mesmos bytes imutáveis. Leitura e extração reabrem a
fonte sem seguir symlink e conferem novamente identidade, SHA-256, envelope,
metadados, tamanho real, CRC e SHA-256 do membro. Troca, crescimento contínuo ou
mutação da fonte entre scan e consumo invalida o plano sem leitura ilimitada. O
envelope ZIP precisa ocupar o arquivo inteiro: prefixo executável, sufixo após
o EOCD, multi-volume e diretório central fora da região declarada são recusados.

### Semântica de nomes e tipos

Nomes internos usam `ZipInfo.orig_filename` e semântica POSIX explícita. São
recusados:

- nome vazio, caminho absoluto, UNC, drive, dois-pontos ou barra invertida;
- componente vazio, `.` ou `..`;
- caracteres das categorias Unicode C, incluindo controles e surrogates, e
  caminhos não canônicos;
- componente terminado em ponto ou espaço;
- nomes reservados Windows, inclusive com extensão e variação de caixa:
  `CON`, `PRN`, `AUX`, `NUL`, `CLOCK$`, `CONIN$`, `CONOUT$`, `COM1`–`COM9` e
  `LPT1`–`LPT9`;
- caracteres proibidos em nomes Win32: `<`, `>`, `"`, `|`, `?` e `*`;
- duplicata exata, colisão após NFC + `casefold`, colisão de prefixo e arquivo
  usado como diretório ancestral;
- symlink, tipo especial, tipo incompatível com o nome e extra field Unix capaz
  de representar link;
- atributo DOS de device ou reparse point e atributo de diretório incompatível
  com o nome do membro;
- membro criptografado ou método fora de Stored/Deflate.

Esse contrato usa a semântica mais restritiva comum aos sistemas suportados.
Um arquivo válido no filesystem de origem, mas ambíguo no Windows ou no macOS,
não é um pacote x86QW válido.

### Limites canônicos

| Recurso | Limite |
|---|---:|
| Membros | 4.096 |
| Arquivo-fonte compactado | 512 MiB |
| Metadados do diretório central | 32 MiB |
| Membro descompactado | 128 MiB |
| Total descompactado | 512 MiB |
| Profundidade | 16 componentes |
| Caminho | 240 unidades UTF-16 |
| Razão de compressão | 500:1 por membro |
| Métodos | Stored e Deflate |

O inventário inicial da issue #49 encontrou 59 ZIP/PK3/PYZ em `dist/`. Os
máximos observados foram 102.995.518 bytes na fonte, 216.156 bytes no diretório
central, 2.830 membros, 22.784.504 bytes por membro, 103.139.351 bytes totais,
profundidade 5, caminho 54 e razão 83,81: todos ficam dentro dos limites com
folga. Antes de criar objetos `ZipInfo`, um pre-scan estrutural percorre no
snapshot os registros do diretório central dentro do envelope declarado,
limita seus bytes e conta os registros reais. Assim, uma contagem subestimada no
EOCD ou uma troca concorrente da fonte não amplia o orçamento de memória. Os
demais limites continuam sendo aplicados tanto aos metadados declarados quanto
ao streaming real.

### Modos, staging e promoção

Permissões armazenadas em `external_attr` não são restauradas. O produto aplica
somente:

- `0644` a arquivos comuns;
- `0755` a diretórios;
- `0755` a arquivos que o consumidor declarou nominalmente como executáveis.

Setuid, setgid, sticky e outros bits do arquivo de origem nunca são propagados.

`extract_archive()` exige destino inexistente. A extração ocorre em diretório
irmão privado `0700`, com criação exclusiva e sem seguir symlinks. Cada arquivo
é escrito com `0600`, revalidado, recebe seu modo canônico e passa por `fsync`.
Diretórios também são sincronizados. Somente depois do sucesso integral o
staging é promovido por rename exclusivo e o diretório pai é sincronizado.

Toda revalidação da fonte termina antes da promoção. Falha anterior ao rename
remove somente o staging cuja identidade ainda possa ser comprovada. A
confirmação de que o destino possui a identidade exata do staging é o ponto de
commit irreversível: falha posterior de durabilidade ou estabilidade do caminho
preserva o destino completo e qualquer conteúdo pessoal criado nele. Uma
promoção de resultado inconclusivo também nunca percorre o nome público durante
rollback. O destino anterior nunca é sobrescrito.

Os serviços preservam uma etapa adicional: o PK3 é extraído integralmente para
staging privado e só depois seus arquivos válidos são materializados de forma
conflict-safe e journalizada no contexto do MVDSV. Arquivo pessoal diferente
continua preservado.

No Windows, essa materialização não depende de hardlink nem de uma sequência
`stat` + remoção por caminho. Cada ancestral permanece aberto sem
`FILE_SHARE_DELETE`, reparse points são recusados e a identidade persistida usa
`VolumeSerialNumber` + `FILE_ID_128`. O arquivo passa por staging exclusivo,
`MoveFileExW` sem substituição e verificação por handle. A limpeza marca para
remoção o mesmo handle cuja identidade e hash foram confirmados; uma identidade
divergente é preservada.

No POSIX, a limpeza não executa mais `hash` seguido de `unlink` diretamente no
nome público. Ela move o nome para uma quarentena por rename exclusivo e
atômico (`renameat2(RENAME_NOREPLACE)` no Linux e
`renameatx_np(RENAME_EXCL)` no macOS), confirma a identidade, calcula novamente
o hash pelo descritor ainda aberto e só então remove o nome privado. Se
conteúdo, tamanho, identidade ou número de links divergir, o nome original é
restaurado por outro rename sem substituição. Sistemas POSIX sem essa primitiva
falham fechados e preservam o arquivo. O journal registra também o tamanho
exato; registros legados sem esse campo são preservados em vez de provocar uma
leitura até EOF.

Essa proteção assume integridade dos processos do mesmo usuário. POSIX não
oferece, de forma portátil, exclusão mandatória contra um processo do mesmo UID
que já retenha um descritor gravável ou um `mmap` antes da quarentena. Esse ator
possui a mesma autoridade da instalação e poderia alterá-la ou apagá-la
diretamente. Os filhos controlados pelo x86QW são encerrados antes da limpeza;
interferência por nome, troca de inode e alteração detectável entre as duas
leituras continuam cobertas e preservadas.

### Bootstrap antes do zipapp

O bootstrap precisa extrair o bundle antes de poder importar o runtime contido
em `x86qw.pyz`. Para não manter um segundo extrator, os scripts Unix e
PowerShell do candidato incorporam uma projeção Base64 byte a byte da fonte
canônica. Eles materializam o helper em temporário privado pelo Python já
validado e invocam sua CLI com versão, membros obrigatórios e executáveis
declarados.

O builder regenera e valida essa projeção; testes exigem igualdade exata entre
os quatro bootstraps e `x86qw_runtime/io/archive.py`. `unzip` e
`Expand-Archive` deixam de participar do fluxo. A projeção existe apenas para
romper o ciclo de bootstrap e não é uma segunda API autorizada.

O bundle continua com o layout público exato de sete membros. VERSION, os dois
documentos `installer.json` e a identidade dentro de `x86qw.pyz` precisam
concordar com a versão solicitada antes da execução da CLI extraída.

## Contratos preservados

- bytes, hashes e estrutura instalada dos arquivos válidos atuais;
- comandos públicos, gameplay, cinco jogos, KTX e Frogbots;
- conteúdo e estratégia dos PAKs;
- arquivos pessoais e destinos já existentes;
- bundles, tags, checksums e ponteiros já publicados, inclusive `0.7.1`;
- os dois layouts históricos imutáveis do instalador: seis membros até
  `0.1.19` e sete membros a partir de `0.1.20`;
- writers determinísticos, desde que o resultado seja produzido em staging,
  validado pela fronteira e publicado sem substituir nome existente.

## Contratos alterados

- um membro inválido em qualquer posição invalida o arquivo inteiro, ainda que
  o consumidor não pretendesse lê-lo;
- arquivos com ambiguidade portável, tipo especial, envelope adicional,
  compressão não permitida ou limite excedido falham antes de mutação;
- executabilidade deixa de vir do ZIP e passa a ser declarada pelo produto;
- extração canônica exige destino inexistente e promoção do diretório completo;
- bootstraps deixam de depender de extratores externos;
- builders recusam pais symlink/reparse, alvos concorrentes e promoção
  automática de um ponteiro `latest` divergente.

## Consequências e riscos residuais

A superfície de review para leitura e extração passa a ser um módulo único, e
um gate estático impede a reintrodução de `extract`, `extractall`, `testzip`,
`is_zipfile`, `unzip`, `Expand-Archive` ou leitura por `ZipFile` fora da
fronteira. A exceção é escrita explícita em modo `w`, seguida de scan.

Permanecem fora desta decisão:

- DACL privada de staging e temporários no Windows, tratada no PR 4 e na
  [issue #47](https://github.com/x86dx2/x86qw/issues/47);
- autenticação, expiração, rollback e freeze de metadados, tratadas no PR 9 e
  na [issue #48](https://github.com/x86dx2/x86qw/issues/48);
- evidência e smokes nativos dos runtimes, que não são substituídos por testes
  portáveis de arquivo;
- promoção transacional do candidato, incluindo troca deliberada de `latest`,
  tratada no PR 10 e na
  [issue #51](https://github.com/x86dx2/x86qw/issues/51);
- TAR: não existe extração TAR para o filesystem nos fluxos abrangidos por esta
  PR. Se um consumidor futuro precisar disso, deve entrar por contrato próprio
  antes do primeiro uso, e não por exceção à fronteira ZIP/PK3/PYZ.

A versão pública `0.7.1` não contém esta decisão. Promover o candidato exige PR,
matriz verde e etapa de release separada; este ADR não autoriza publicação.
