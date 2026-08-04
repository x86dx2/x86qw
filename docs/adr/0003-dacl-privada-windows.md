# ADR 0003 — DACL privada no Windows

- **Estado:** aceita no código corretivo da PR 4; casos Win32 nativos aprovados
  em Windows com Python 3.10 e 3.13; não publicada
- **Data:** 2026-08-03
- **Issue:** [#47](https://github.com/x86dx2/x86qw/issues/47)

## Contexto

Os modos POSIX `0600` e `0700` não definem uma política de acesso equivalente
no Windows. Uma criação exclusiva por `mkstemp`, `O_EXCL` ou `CreateFileW`
impede colisão de nome, mas ainda pode herdar de seu diretório uma DACL que
concede acesso a `Users`, `Everyone` ou outro principal local.

Essa diferença afeta o plano de controle `.x86qw/`, locks, journals, logs de
background, pedidos de parada, configurações temporárias que podem conter
senhas, temporários de download e os arquivos materializados pelo bootstrap
antes de o zipapp existir. Um arquivo externo fornecido por `--password-file`
também precisa ser provado privado antes da leitura.

O risco coberto é leitura ou alteração por outro usuário local comum durante a
criação, uso ou recuperação do objeto. O contrato não tenta proteger dados de
código já executando como o mesmo usuário nem de um administrador que exerça
privilégios nativos para tomar posse ou ignorar a DACL.

## Decisão

### DACL canônica

Um objeto privado gerenciado pelo x86QW no Windows usa:

- proprietário igual ao usuário atual nas criações novas; um objeto gerenciado
  preexistente pode manter o owner `LOCAL SYSTEM` quando sua identidade é
  comprovada;
- DACL protegida, sem herança do diretório pai;
- exatamente uma ACE `Allow/Full Control` para o SID do usuário atual;
- exatamente uma ACE `Allow/Full Control` para `LOCAL SYSTEM`
  (`S-1-5-18`);
- herança `Object Inherit` e `Container Inherit` somente em diretórios;
- nenhuma ACE explícita para `Administrators`, `Users`, `Everyone` ou outro
  principal.

A implementação não solicita elevação. O usuário atual é o proprietário dos
objetos novos e `LOCAL SYSTEM` permanece autorizado para compatibilidade com
operações normais do sistema. A execução end-to-end com token de usuário
padrão ainda precisa ser comprovada no PR 11.

### Criação antes da primeira escrita

Arquivos e diretórios sensíveis precisam nascer com a DACL canônica. Aplicar a
proteção apenas depois de criar ou escrever o objeto deixaria uma janela de
exposição. A fronteira Windows usa `SECURITY_ATTRIBUTES` em `CreateFileW` e
`CreateDirectoryW`, mantém handles durante validação e rejeita reparse points
na cadeia de caminho.

Objetos gerenciados preexistentes podem ser endurecidos quando encontrados,
desde que a identidade e o proprietário sejam comprovados. A implementação não
faz uma migração recursiva e inferencial de arquivos arbitrários da instalação.

### Escopo gerenciado

O contrato se aplica aos objetos privados que o x86QW cria ou controla,
incluindo:

- `.x86qw/`, `.x86qw/sessions/` e diretórios de sessão;
- lock ativo, lock reclamado, journals e pedidos de parada;
- logs de processos em background;
- configurações efêmeras e outros arquivos que contenham segredos;
- temporários e staging do downloader, do instalador e da fronteira de
  ZIP/PK3/PYZ;
- diretório de trabalho do bootstrap PowerShell e seus arquivos antes da
  promoção.

O bootstrap cria o diretório de trabalho privado antes de materializar helper,
bundle ou conteúdo extraído. A implementação anterior ao zipapp é uma projeção
mínima do mesmo descritor; ela não cria uma segunda política de acesso.

O mutex nomeado que serializa a reclamação de locks usa o namespace global do
Windows para abranger sessões distintas e recebe ACL equivalente, limitada ao
usuário atual e a `LOCAL SYSTEM`. Se ele não puder ser criado ou adquirido, a
operação falha fechada e não tenta um caminho concorrente alternativo.

### Arquivos externos com segredo

Um arquivo indicado pelo usuário é externo ao ownership do x86QW. A CLI:

- abre e valida o arquivo sem seguir symlink ou reparse point;
- exige filesystem com ACLs persistentes, DACL protegida e proprietário igual
  ao usuário atual ou `LOCAL SYSTEM`;
- permite ACEs somente para o usuário atual e `LOCAL SYSTEM`, exige que o
  usuário atual consiga ler e rejeita duplicatas ou flags de herança;
- limita a leitura e a realiza pelo mesmo handle validado;
- nunca troca a DACL, move, apaga ou reescreve o arquivo externo.

Falha ou resultado inconclusivo na leitura da DACL interrompe a operação antes
de o segredo ser consumido. Mensagens e journals não registram seu conteúdo.

### Falha conservadora

Filesystem sem suporte comprovado a ACL persistente, erro de API, owner
inesperado, reparse point, ACE desconhecida, herança ativa ou diferença entre a
DACL esperada e a observada produzem erro. A implementação preserva um resíduo
privado cuja identidade não possa mais ser provada em vez de apagar por
pathname e arriscar remover uma substituição concorrente.

## Contratos preservados

- contrato de instalação sem privilégios administrativos, ainda sujeito ao
  smoke de usuário padrão do PR 11;
- caminhos e nomes públicos do plano de controle `.x86qw/`;
- compatibilidade de leitura dos locks formatos 1 e 2; lock novo formato 3 com
  `private_filesystem`; journal formato 1 com extensão aditiva; recibo e estado
  inalterados;
- semântica de cleanup e recuperação conservadora;
- opções de prompt e arquivos de senha;
- bundles, hashes, tags e ponteiros já publicados, inclusive `0.7.1`.

## Contratos alterados

- no Windows, um objeto privado gerenciado só pode ser usado depois de a DACL
  canônica ser criada ou comprovada;
- arquivos de senha externos precisam satisfazer a política privada antes da
  leitura;
- falha ou ambiguidade de ACL passa a ser terminal, ainda que o arquivo fosse
  aceito por uma verificação POSIX inócua no Windows;
- o temporário do bootstrap PowerShell nasce privado antes da primeira escrita.

## Validação

Os testes portáteis cobrem geração do descritor, roteamento pela fronteira
privada, falha conservadora e ausência de mutação em arquivos externos. Os
jobs nativos Windows da PR 4, em Python 3.10 e 3.13, provaram:

- criação sob um pai que concede acesso herdável a `Users` ou `Everyone`;
- DACL protegida com somente usuário atual e `LOCAL SYSTEM`;
- ausência de janela de criação com ACL ampla;
- rejeição de arquivo externo com acesso amplo;
- bootstrap, temporários, locks, journals, logs e configurações sensíveis;
- bootstrap sem solicitar elevação administrativa.

A evidência está registrada no run
[`30866754435`](https://github.com/x86dx2/x86qw/actions/runs/30866754435).
Isso não prova o smoke dos runtimes sob uma conta padrão sem elevação, que
permanece obrigatório no PR 11. Este ADR não autoriza publicação.

## Consequências e riscos residuais

A política de acesso passa a ser explícita, revisável e única entre runtime,
manutenção e bootstrap. Em troca, volumes sem ACL persistente e ambientes que
impedem sua inspeção falham fechados, mesmo quando a operação poderia funcionar
sem privacidade comprovada.

Permanecem fora desta decisão a defesa contra o mesmo usuário, contra
administrador ou kernel comprometido, o apagamento físico seguro de segredos e
a autenticação/expiração dos metadados remotos. A release pública `0.7.1` não
contém esta decisão; uma futura `0.7.2` exige PR, checks verdes e promoção
imutável separados.
