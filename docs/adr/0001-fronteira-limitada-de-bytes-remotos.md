# ADR 0001 — Fronteira limitada de bytes remotos

**Status:** proposto; implementação corretiva em revisão e não publicada

**Data:** 2026-08-03

**Issue:** [#45](https://github.com/x86dx2/x86qw/issues/45)

## Contexto

O instalador e as ferramentas de manutenção possuíam downloaders independentes.
Alguns aceitavam corpo parcial, liam metadados sem limite, repetiam respostas
HTTP permanentes ou promoviam o destino antes da validação integral. Um mirror
que respondesse HTTP 200 com bytes corrompidos também podia impedir o fallback
para um mirror íntegro.

A decisão precisa limitar recursos, preservar o destino anterior e aplicar uma
política de transporte única sem acrescentar dependência Python externa. Essa
proteção não deve ser confundida com autenticação do catálogo ou do upstream.

## Decisão

Downloads HTTP feitos pela aplicação e pelas ferramentas Python passam por
`maintenance/tools/downloader.py`. A fronteira expõe contratos de dados e duas
operações: `download()` para uma origem e `download_mirrors()` para origens
equivalentes sob um único orçamento monotônico.

As operações públicas criam um opener HTTPS e um registro de cancelamento
privados por chamada. Não existe singleton/getter público nem argumento para
injetar transporte, relógio, espera ou aleatoriedade. O zipapp incorpora apenas
os entrypoints selados; seams determinísticos permanecem em helpers privados da
suíte.

### Contratos da API

| Contrato | Campos obrigatórios de segurança | Resultado permitido |
|---|---|---|
| `PinnedArtifact` | URL HTTPS, destino, tamanho esperado, SHA-256 esperado, tamanho máximo, deadline total e política de retry | arquivo validado promovido atomicamente |
| `BoundedMetadata` | URL HTTPS, tamanho máximo, deadline total e política de retry | bytes efêmeros em memória ou headers de `HEAD` |

`PinnedArtifact` é obrigatório para qualquer byte remoto que possa persistir,
tanto na instalação quanto na árvore de distribuição. `BoundedMetadata` atende
catálogo, Hub e descoberta, mas não converte metadados dinâmicos em informação
autenticada nem autoriza seus consumidores a promover a resposta a arquivo
gerenciado.

Uma descoberta de stable, nightly ou nQuake não pode usar o SHA-256 calculado
durante a própria transferência como identidade esperada. O `update` só herda
uma identidade já revisada quando caminho, URL e tamanho coincidem exatamente
com `dist/manifest.json`. Um candidato novo permanece em `review_required` e
precisa entrar por uma definição `add` que declare tamanho e SHA-256 antes de
qualquer download persistente.

Para uma revisão nova do nQuake, a definição também precisa declarar uma
transição `reference` explícita: repositório idêntico ao inventariado, revisão
anterior igual ao estado corrente e nova revisão em hash Git completo. Essa
transição stale-safe só altera a projeção em memória usada pelo intake, precisa
incorporar arquivo sob o novo snapshot e só é promovida se a lista revisada
reconstruir o snapshot consumido completo; o `update` não recebe autoridade
para baixar ou promover o candidato por conta própria.

O pin da definição não é autossuficiente: URL remota exige `managed: true` e
correspondência exata com pelo menos uma autoridade canônica — artefato de uma
release proposta, fonte preservada no registro de upstreams, pacote público
proposto ou referência nQuake fixada. O namespace de
`distribution_component`, o consumidor operacional e a identidade lógica de
`package` também precisam coincidir com essa autoridade. Um caminho já
registrado não pode receber novos bytes ou metadados no lugar, e bundles do
instalador são exclusivos do fluxo de release imutável. Para ezQuake, o caminho
é derivado exatamente de
`clients/ezquake/<canal>/<versão>/<plataforma>-<arquitetura>/<arquivo>` e precisa
coincidir com essas coordenadas no pacote. Fontes locais precisam estar
consumidas pelo BOM proposto e são copiadas por temporário com hash no streaming,
`fsync` e `replace`, evitando que o staging por hardlink modifique a árvore viva
antes da transação.

A política de retry, headers e rótulo são parte de cada contrato. Os contratos
de mirror são validados integralmente antes da primeira conexão e precisam ter o
mesmo tipo, deadline e identidade de destino. Mirrors de artefato também precisam
compartilhar tamanho e SHA-256 esperados.

Catálogo, manifesto, inventário de releases, registro de upstreams e intake
reutilizam o validador HTTPS da fronteira. URLs persistentes rejeitam userinfo,
fragmentos, queries, espaços e controles antes que uma origem seja registrada.

## Deadline, transporte e mirrors

`download()` cria um deadline absoluto para uma origem. Esse mesmo orçamento
cobre conexão, redirects, headers, leituras, tentativas e pausas de retry. Cada
leitura recebe no socket apenas o tempo monotônico restante.

A resolução DNS ocorre em subprocesso Python isolado, com quantidade de
endereços e saída limitadas. O subprocesso recebe `kill` e uma tentativa de
coleta se esgotar o orçamento. O filho aguarda um token em `stdin`; o pai só o libera para
`getaddrinfo` depois de anexar o handle ao controlador. As tentativas TCP usam
sockets não bloqueantes e o mesmo deadline
absoluto. Durante TLS e headers, o controlador registra a conexão; ao cancelar,
encerra o resolver e os sockets pendentes e executa `shutdown` antes do
fechamento. A mesma limpeza ocorre em interrupção do controlador. O worker
recalcula o orçamento imediatamente antes de abrir a conexão, portanto atraso
de agendamento não reutiliza um timeout obsoleto. Orçamentos restantes menores
que 500 ms são recusados antes de criar worker, resolver ou socket, pois não
reservam janela suficiente de cancelamento. Operações aceitas reservam de 250 a
500 ms do próprio deadline para a coleta local. Uma resposta que termine depois do
controlador é fechada pelo worker e nunca pode promover o destino.

`subprocess.Popen` é uma chamada local sem cancelamento fornecido pela biblioteca
padrão. Se a própria criação do resolver bloquear além do deadline, o chamador
retorna no prazo e o filho permanece no gate sem iniciar DNS; assim que a
criação retorna, o estado cancelado mata o processo sem enviar o token. A thread daemon
pode aguardar somente esse retorno local. Eliminar até essa espera exigiria um
supervisor de processo externo e fica fora desta mudança incremental.

Se o sistema operacional demorar além da janela reservada para concluir o reap
de um filho já morto, a thread daemon pode terminar a coleta depois do retorno.
O deadline público continua rígido e não há DNS ou socket novo após o
cancelamento; a janela não é uma garantia absoluta de tempo de reap do SO.

`download_mirrors()` cria um único deadline absoluto antes do primeiro mirror e
o reutiliza em todas as tentativas e origens. Falhas de transporte ou
integridade podem avançar ao próximo mirror. Falhas de protocolo, limite,
armazenamento, política ou deadline são terminais e não são mascaradas por um
fallback remoto. Quando um `Retry-After` não cabe no orçamento daquele mirror,
o controlador pode avançar imediatamente para outra origem sem ultrapassar o
deadline compartilhado.

Um chamador que execute várias chamadas independentes a `download()` não obtém
automaticamente esse orçamento compartilhado. Loops históricos de mirror devem
ser migrados para `download_mirrors()` e permanecer cobertos por testes de
integração; a mera existência da API não prova que todos os consumidores a usam.

## Limites

- artefato persistente: no máximo 512 MiB;
- catálogo do instalador: no máximo 2 MiB;
- resposta do Hub: no máximo 1 MiB;
- descoberta HTTP de manutenção: no máximo 4 MiB;
- árvore recursiva do GitHub: no máximo 4 MiB e 50.000 entradas sob um deadline
  compartilhado entre a resolução da referência, do commit e a leitura da árvore.

O catálogo também rejeita previamente pacotes cujo tamanho declarado ultrapasse
o limite global. Respostas sem `Content-Length` continuam limitadas durante o
streaming.

## Transporte, retry e armazenamento

- URLs inicial, redirecionada e final precisam permanecer em HTTPS;
- redirects entre origens não podem encaminhar headers privados;
- corpos intermediários 301/302/303/307/308 são fechados sem leitura e não
  contornam o limite de bytes;
- `Content-Length`, quando presente, precisa ser único, decimal e coerente;
- SHA-256 e tamanho são calculados durante o streaming, sem segunda leitura;
- o tamanho esperado é ultrapassado por no máximo um byte antes da interrupção;
- o padrão é de três tentativas, backoff exponencial de 0,5 a 8 segundos e
  jitter de 20%;
- apenas rede transitória e HTTP 408, 425, 429, 500, 502, 503 e 504 recebem
  retry;
- `Retry-After`, em segundos ou HTTP-date, é aceito somente quando cabe no
  deadline;
- temporários usam modo `0600` no POSIX;
- validação, `flush`, `fsync` e fechamento antecedem `os.replace`;
- falha de rede, integridade ou armazenamento preserva o destino anterior.

## Bootstraps

Antes de o zipapp existir não é possível importar a fronteira comum. Os
bootstraps mantêm projeções mínimas e isoladas:

- Unix usa `curl --disable` como primeiro argumento para ignorar `.curlrc`,
  seguido de `--proto '=https' --proto-redir '=https'`, timeout de conexão,
  orçamento único para retries e mirrors, limite durante a recepção e validação
  de `Content-Length`, tamanho e SHA-256;
- o comando público Unix grava no máximo 262145 bytes sob `umask 077`, usa
  `pipefail` e só executa o arquivo após confirmar status e tamanho máximo de
  262144 bytes; prefixo produzido por pipeline com falha e stream excessivo sem
  `Content-Length` são rejeitados;
- PowerShell incorpora um downloader Python com HTTPS, limite, deadline,
  `Retry-After`, temporário privado no POSIX e promoção atômica;
- no PowerShell, timeouts de conexão e tentativa usam o orçamento completo
  quando o deadline total ainda oferece a margem posterior de coleta; somente a
  parte faltante dessa margem é reservada antes do limitante;
- as cópias canônica e pública são obrigadas a permanecer idênticas por teste.

No Windows, o temporário criado pelo bootstrap é exclusivo, mas ainda pode
herdar uma DACL ampla do diretório. O modo POSIX `0600` não resolve esse risco;
a DACL privada permanece no PR 4 e na
[issue #47](https://github.com/x86dx2/x86qw/issues/47).

Essas projeções existem somente para obter o zipapp fixado; não autorizam um
novo downloader para clientes, componentes ou metadados do produto.

## Fronteiras externas e riscos aceitos neste ADR

- `BoundedMetadata` limita transporte e memória, mas catálogos ainda não têm
  expiração, proteção contra rollback/freeze, rotação de chaves ou raiz de
  confiança fixada. Esse contrato pertence ao PR 9 e à
  [issue #48](https://github.com/x86dx2/x86qw/issues/48).
- referências, commits, árvores e leituras de releases do GitHub passam por
  `BoundedMetadata`. Árvores recursivas truncadas e entradas com schema, tipo,
  modo, SHA-1, tamanho ou caminho inválido são rejeitadas antes do consumo. Os
  comandos `gh` restantes e o `curl` de upload GitLab são operações de
  publicação de saída, não rotas alternativas de ingestão. Esse `curl` usa
  `--disable` como primeiro argumento, além de HTTPS obrigatório e redirects
  desativados, mas a promoção imutável ainda precisa ser redesenhada no PR 10;
- o `curl` do bootstrap é a projeção limitada descrita acima;
- privacidade por DACL no Windows não é resolvida pelo modo POSIX `0600` e
  permanece no PR 4 e na
  [issue #47](https://github.com/x86dx2/x86qw/issues/47).
- testes portáveis da fronteira não substituem smokes nativos de rede ou dos
  runtimes.

Essas exceções não são rotas alternativas autorizadas para instalar conteúdo
sem identidade prévia. Elas precisam ser eliminadas ou formalmente absorvidas pelos PRs que
tratam trust metadata, automação de release e ACL antes da versão 1.0.0.

## Consequências

Um novo consumidor Python direto de `urlopen` ou `urlretrieve` falha no teste
estático. URLs da fronteira HTTP exibidas em diagnóstico omitem credenciais,
caminho, query e fragmento; a descoberta GitHub aceita somente um repositório
público previamente validado. Erros da fronteira são tipados, o
destino final anterior é preservado até a promoção e a orquestração de mirrors
pode distinguir falha remota de falha local. Em troca, todo novo consumidor
precisa declarar limite, deadline e, para conteúdo persistente, identidade
criptográfica antes de receber bytes.

O gate percorre `.py`, `.pyw`, `.bat`, variantes shell, scripts sem extensão e
launchers. O Python embutido em `$DownloaderSource` também é extraído e
analisado por AST. O scanner resolve concatenação literal, `join`, helpers
simples de argv e imports dinâmicos básicos; argv não resolvido falha salvo uma
supressão exata por arquivo, API, função e expressão. Ele não é análise formal
de fluxo: reflexão geral, `getattr`, `eval`, `exec`, código gerado, extensões
nativas e bibliotecas ainda não inventariadas exigem review e um novo caso
adversarial.
