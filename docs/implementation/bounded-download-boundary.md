# Fronteira limitada de downloads — implementação corretiva

**Baseline inicial:** `afb4f666095e37fe262b87b49339e18d25738522`

**Versão pública preservada:** `0.7.1`

**Estado:** implementação corretiva em revisão; não publicada

Esta nota registra a implementação da
[issue #45](https://github.com/x86dx2/x86qw/issues/45). Ela não altera versão,
catálogo `current`, bundle, tag ou release já publicados.

## Defeitos reproduzidos

- `Content-Length: 100` com corpo de três bytes era aceito;
- uma conexão parcial podia deixar o destino incompleto;
- uma resposta de catálogo com 2 MiB era lida integralmente, sem limite;
- HTTP 404 recebia retry como falha temporária;
- HTTP 200 corrompido no primeiro mirror impedia fallback íntegro no bootstrap;
- a manutenção podia calcular o SHA-256 de um candidato stable, nightly ou
  nQuake durante a própria transferência e promovê-lo como referência;
- `add --dry-run` retornava antes da validação semântica e imprimia a definição
  bruta, permitindo sucesso sem pins e exposição de credenciais de URL;
- o timeout do controlador podia terminar enquanto DNS ou leitura de headers
  ainda mantinham trabalho residual;
- runtime e manutenção possuíam implementações diretas e divergentes de
  `urlopen`.

## Implementação

`maintenance/tools/downloader.py` passou a ser a fronteira Python para entrada
HTTP. A API pública desta unidade é composta por:

- `download(contract)`: uma URL e um deadline total;
- `download_mirrors(contracts)`: URLs equivalentes, validadas antes da rede e
  submetidas ao mesmo deadline absoluto;
- erros tipados para política, redirect, protocolo, limite, integridade,
  deadline, armazenamento, HTTP permanente e falha transitória.

O opener e seu registro de sockets são privados e novos em cada operação. Não
existe getter público nem argumento para injetar transporte, relógio, espera ou
aleatoriedade nos entrypoints de produção; esses seams ficam em helpers privados
usados somente pela suíte focal.

Os contratos são:

| Contrato | Uso | Identidade | Persistência |
|---|---|---|---|
| `PinnedArtifact` | bundle, cliente, componente e arquivo remoto declarado | tamanho e SHA-256 prévios | promoção atômica |
| `BoundedMetadata` | catálogo, Hub e descoberta | limite e TLS; sem identidade versionada | memória ou headers de `HEAD` |

`PinnedArtifact` exige URL HTTPS, destino, tamanho esperado, SHA-256 esperado,
tamanho máximo, deadline e política de retry. Ele é o único contrato autorizado
a gravar um resultado remoto persistente. Metadados dinâmicos sempre exigem
limite e deadline, ficam em memória e não podem ser apresentados como conteúdo
autenticado.

As URLs persistidas no catálogo, manifesto, inventário de releases, registro de
upstreams e intake passam pelo mesmo parser HTTPS estrito antes de serem
aceitas: userinfo, fragmentos, queries, espaços e controles são rejeitados sem
ecoar credenciais na exceção.

Descobertas atuais só recebem o SHA-256 já revisado de `dist/manifest.json`
quando caminho, URL e tamanho coincidem exatamente. Candidatos novos ficam
bloqueados antes de confirmação, staging, download ou mutação e exigem intake
pinado por `maintenance/manage.py add`.

O intake também é estrito no modo `add --dry-run`: ele valida HTTPS, tamanho,
SHA-256, destino portável, origem local sem symlink intermediário, colisões de
caixa/Unicode, owner, consumer, package, inventários propostos e a
correspondência do pacote público, mas não baixa bytes, não cria staging e não
altera a árvore. URLs persistentes não aceitam query. O resumo usa somente
origens redigidas; a definição JSON nunca é ecoada.

Todo arquivo remoto do intake declara `managed: true` e fica vinculado
exatamente a pelo menos uma autoridade: artefato de release proposta, fonte
preservada no registro de upstreams, pacote público proposto ou referência
nQuake fixada. O namespace de `distribution_component`, o consumidor e a
identidade lógica de `package` precisam concordar com essa autoridade. Entradas
já presentes no manifesto são imutáveis no mesmo caminho, inclusive owner,
consumer e package; um novo bundle do instalador só pode nascer no fluxo próprio
de release. Pacotes ezQuake precisam usar exatamente
`clients/ezquake/<canal>/<versão>/<plataforma>-<arquitetura>/<arquivo>` e suas
coordenadas de componente, canal, versão, plataforma e arquitetura são validadas
contra o catálogo proposto. Fontes locais só podem ocupar um `project_*` exato
do BOM proposto. Sua cópia usa um temporário exclusivo, calcula o hash no
streaming contra o plano já aprovado, executa `flush`/`fsync` e promove com
`replace`, quebrando o hardlink de staging sem escrever no `dist/` vivo. O modo
`0600` protege esse temporário no POSIX; DACL privada no Windows permanece fora
desta correção.

Uma revisão nova do snapshot nQuake só pode ser autorizada pelo próprio intake.
A definição declara `reference` com `repository`, `previous_revision` e
`revision`; o repositório precisa ser exatamente o já inventariado, a revisão
anterior precisa coincidir com o estado corrente e ambas as revisões são hashes
Git completos. A transição é aplicada apenas aos inventários em memória antes
da validação, precisa incorporar arquivo sob o novo snapshot e só pode ser
promovida quando a lista revisada reconstrói o snapshot consumido completo. Ela
não faz o `update` baixar um candidato ainda não revisado.

Consumidores migrados:

- `dist/installer/bin/manager.py`;
- `maintenance/manage.py`;
- `maintenance/tools/build_package.py`;
- `maintenance/tools/check_component_updates.py`;
- `maintenance/tools/public_upstreams.py`;
- `maintenance/tools/publish_gitlab_packages.py`;
- `maintenance/tools/sync_distribution.py`.

O builder incorpora o módulo no `x86qw.pyz`. Os bootstraps canônicos e públicos
aplicam uma projeção mínima antes de o zipapp existir e revalidam tamanho e
SHA-256 dentro do loop de mirrors. O entrypoint público Unix usa `umask 077`,
`pipefail` e `head -c 262145`, verifica o status e recusa mais de 262144 bytes
antes de executar o script; nem um prefixo produzido por pipeline com falha nem
um stream excessivo sem `Content-Length` chega ao Bash.

## Deadline e fallback

O deadline é monotônico e absoluto. Em `download()`, ele abrange conexão,
redirects, headers, streaming, retries e respectivas pausas. Em
`download_mirrors()`, o instante final é calculado uma vez antes do primeiro
mirror e compartilhado por todas as origens e tentativas.

O worker recalcula o orçamento imediatamente antes da abertura. DNS é resolvido
por subprocesso isolado, com saída limitada e `kill` seguido de coleta no
timeout. O resolver começa bloqueado em um gate de `stdin` e só chama
`getaddrinfo` depois que o controlador registra o handle e envia o token de
início; as conexões TCP são não bloqueantes e compartilham o deadline absoluto.
Se o prazo expirar em TLS ou headers, o controlador executa `shutdown` no socket
registrado antes de fechá-lo. Resolver e sockets pendentes pertencem ao mesmo
controlador e são encerrados também em `KeyboardInterrupt`; o orçamento reserva
de 250 a 500 ms, dentro do deadline total, como janela de coleta local.
Respostas tardias são fechadas e nunca promovem o temporário. A API pública não
aceita callback de transporte, opener, relógio, espera ou aleatoriedade
alternativos e cada operação constrói seu próprio transporte privado. Callbacks
de observabilidade para progresso e retry permanecem no contrato público.

Se restarem menos de 500 ms para a fase de abertura, ela é recusada antes de
criar thread, subprocesso ou socket. Os deadlines de produção são de 60 a 300
segundos; o piso impede que um orçamento impraticável comece atividade remota
sem reservar uma janela para cancelamento e coleta. Ele não promete que um
reap patologicamente lento do sistema operacional termine antes do retorno.

A biblioteca padrão não oferece cancelamento de uma chamada local a
`subprocess.Popen` que esteja bloqueada dentro do sistema operacional. Nesse
caso excepcional, o chamador ainda retorna no deadline e o controlador impede
que o processo tardio seja anexado: quando `Popen` finalmente retorna, o filho é
encerrado antes de resolver DNS ou abrir socket. A thread daemon pode permanecer
apenas até essa criação local retornar. Isso é uma limitação de disponibilidade
local; garantir ausência absoluta dessa thread exigiria um supervisor externo,
não um aumento arbitrário do timeout.

De forma análoga, depois de `kill`, o sistema operacional pode levar mais que a
janela reservada para devolver o status do filho. O chamador preserva o deadline
duro; nesse intervalo excepcional a thread daemon pode concluir a coleta depois
do retorno, mas o token de DNS não é enviado e o cancelamento já interrompeu
sockets e atividade remota.

Todos os contratos de um conjunto de mirrors são validados antes da primeira
conexão. Tipo, deadline, destino e identidade precisam ser compatíveis. Falhas
remotas de transporte ou integridade permitem fallback; falhas de protocolo,
limite, armazenamento, política ou deadline encerram a operação. Um
`Retry-After` que não caiba no orçamento do mirror atual não bloqueia a tentativa
de uma origem equivalente dentro do mesmo deadline.

A API compartilhada não corrige automaticamente loops históricos que ainda
chamem `download()` uma vez por URL. A migração dos consumidores para
`download_mirrors()` precisa ser comprovada por testes de integração e pelo gate
que proíbe rotas paralelas de entrada HTTP.

## Limites e política

- artefatos persistentes: 512 MiB;
- catálogo: 2 MiB;
- Hub: 1 MiB;
- descoberta HTTP: 4 MiB;
- árvore recursiva do GitHub: 4 MiB, 50.000 entradas e um deadline de 60
  segundos compartilhado com a resolução da referência e do commit;
- três tentativas por padrão;
- retry somente para rede transitória e HTTP 408, 425, 429, 500, 502, 503 e 504;
- backoff exponencial de 0,5 a 8 segundos, jitter de 20% e `Retry-After` em
  segundos ou HTTP-date;
- deadline monotônico total por contrato ou conjunto de mirrors;
- timeout aplicado também ao socket de leitura;
- temporário `0600` no POSIX e ordem `flush` → `fsync` → fechamento →
  `os.replace`.

## Testes adversariais

A suíte dedicada cobre:

- resposta sem fim e catálogo acima do limite, com e sem `Content-Length`;
- `Content-Length` ausente, inválido, duplicado ou divergente;
- resposta parcial, tamanho excedido e SHA-256 incorreto;
- timeout de conexão/headers, leitura bloqueante com `socketpair` e deadline
  entre chunks;
- resolver DNS bloqueado recebendo `kill` e sendo coletado no caminho normal,
  com saída e candidatos limitados;
- gate de início do resolver sem token quando `Popen` só devolve o handle depois
  do deadline, impedindo DNS tardio;
- download completo e bootstrap PowerShell cancelando resolver, sockets e
  respostas tardias sem atividade remota residual;
- `KeyboardInterrupt` durante DNS sem subprocesso órfão;
- cancelamento de headers comprovado com `shutdown` real em `socketpair`;
- expiração entre a criação do worker e a espera do controlador, com
  cancelamento obrigatório da conexão;
- atraso de agendamento sem reaproveitar orçamento de conexão obsoleto;
- orçamentos de 10 ms e 500 ms nominais rejeitados antes de iniciar transporte
  quando o orçamento restante já está abaixo do piso;
- um deadline compartilhado entre retries e mirrors;
- redirects para HTTP e headers privados entre origens;
- corpos intermediários dos cinco status de redirect sem leitura ilimitada e
  compatibilidade de HTTP 308 com Python 3.10;
- matriz de HTTP transitório e permanente;
- intake `add --dry-run` sem rede ou mutação, com pins, ownership e URLs
  persistentes sem query, redigidas inclusive em entradas adversariais;
- gate estático cobrindo Python embutido no PowerShell, `.bat`, variantes shell,
  shebangs, helpers de argv e imports dinâmicos simples;
- `Retry-After` numérico, HTTP-date, inválido e maior que o deadline;
- backoff e jitter determinísticos;
- `EACCES`, `ENOSPC`, escrita curta e falhas de fechamento, `flush`, `fsync` e
  `replace`;
- ordem da promoção e preservação do destino e dos temporários em falhas.

Os bootstraps possuem regressões próprias para HTTP 200 corrompido no primeiro
mirror, transferência parcial e `IncompleteRead`, `Content-Length` divergente,
orçamento compartilhado, timeout real de conexão sem consumir antecipadamente
a margem total de limpeza, limite do comando Unix mesmo sem `Content-Length`,
prefixo de pipeline com falha nunca executado, redirects sem drenar corpo,
flags HTTPS, tamanho fixado, preservação da sessão PowerShell e equivalência
entre as cópias canônica e pública.

## Contratos preservados

- biblioteca padrão do Python;
- mirrors GitHub e GitLab atuais;
- progresso e mensagens em português no instalador;
- bundles e releases já publicados imutáveis;
- fallback entre mirrors;
- nenhuma mudança de runtime ou gameplay.

## Limitações restantes

Limitar bytes e exigir HTTPS não autentica catálogo, stable ou nightly dinâmico,
nem impede rollback ou freeze. Um candidato sem digest previamente revisado é
somente descoberto: `update` não baixa nem promove seus bytes. A autenticação
versionada pertence ao PR 9 e à issue #48.

Referências, commits, árvores recursivas e leituras de releases GitHub usam
`BoundedMetadata`; árvores truncadas e entradas com schema inseguro são
rejeitadas integralmente. `gh` e o `curl` restantes publicam bytes para os
mirrors. O `curl` de upload GitLab começa com `--disable`, exige HTTPS e não
segue redirects; sua substituição pela promoção imutável pertence ao PR 10.
As projeções de bootstrap continuam necessárias antes da existência do zipapp;
o Unix passa `--disable` como primeiro argumento do `curl` para ignorar
configuração `.curlrc` herdada antes de restringir origem e redirects a HTTPS.

O scanner estático inventaria Python, Python GUI (`.pyw`), `.bat`, PowerShell,
variantes shell e scripts com shebang. O Python contido em
`$DownloaderSource` nos bootstraps também é extraído e analisado por AST. O gate
resolve concatenação/`join`, helpers simples com parâmetros, atribuições de argv
e imports dinâmicos básicos; argv não resolvido falha. Allowlists são limitadas
a funções Python explícitas, expressões exatas de dispatch ou padrões de linha
com quantidade fixa nos bootstraps.

Ele deliberadamente não interpreta fluxo de dados arbitrário, reflexão por
`getattr`, `eval`/`exec`, código gerado, extensões nativas ou bibliotecas de rede
ainda não inventariadas. Mudanças desse tipo continuam exigindo revisão humana e
novo caso adversarial no gate; o teste não deve ser descrito como prova formal de
ausência de qualquer comunicação possível. Operações externas de publicação
também não se tornam consumidoras da API HTTP por causa do scanner. DACL privada
no Windows — inclusive para o temporário criado pelo bootstrap PowerShell em um
diretório com herança ampla — pertence ao PR 4 e à
[issue #47](https://github.com/x86dx2/x86qw/issues/47). `mkstemp` é exclusivo,
mas não substitui esse controle de acesso. Testes portáveis não substituem
smokes nativos dos clientes, serviços ou transporte público.

## Validação

Esta nota descreve código corretivo ainda não publicado. A implementação foi
validada com 565 testes de manutenção e cinco testes do site; os oito skips
locais são sete verificações exclusivas do runner Windows e um smoke de rede
opt-in. A matriz do PR passou em Ubuntu, macOS e Windows com Python 3.10 e 3.13.
A evidência do PR registra separadamente:

- suíte dedicada do downloader;
- suíte integral de manutenção e site;
- `maintenance/manage.py verify`, `git diff --check` e `git lfs fsck`;
- dry-run do Worker;
- sete jobs verdes: gate de qualidade e Ubuntu, macOS e Windows com Python 3.10
  e 3.13;
- testes comportamentais dos dois bootstraps.

Nenhum desses testes é apresentado como smoke nativo dos runtimes. A versão
`0.7.1`, seus bundles e seus ponteiros públicos permanecem imutáveis enquanto
esta implementação estiver em revisão.
