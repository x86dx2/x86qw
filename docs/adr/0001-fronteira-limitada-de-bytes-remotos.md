# ADR 0001 — Fronteira limitada de bytes remotos

**Status:** aceito no código corretivo; não publicado

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

### Contratos da API

| Contrato | Campos obrigatórios de segurança | Resultado permitido |
|---|---|---|
| `PinnedArtifact` | URL HTTPS, destino, tamanho esperado, SHA-256 esperado, tamanho máximo, deadline total e política de retry | arquivo validado promovido atomicamente |
| `BoundedMetadata` | URL HTTPS, tamanho máximo, deadline total e política de retry | bytes efêmeros em memória ou headers de `HEAD` |
| `BoundedPayload` | URL HTTPS, destino de staging, tamanho esperado obtido antes da transferência, tamanho máximo, deadline total e política de retry | payload limitado para manutenção, ainda não confiável |

`PinnedArtifact` é obrigatório para artefatos persistentes da instalação.
`BoundedMetadata` atende catálogo, Hub e descoberta, mas não converte metadados
dinâmicos em informação autenticada. `BoundedPayload` é uma exceção exclusiva
da manutenção: sua promoção depende de identidade independente, quando existir,
ou de uma transação revisada; ele não pode substituir `PinnedArtifact` no
instalador público.

A política de retry, headers e rótulo são parte de cada contrato. Os contratos
de mirror são validados integralmente antes da primeira conexão e precisam ter o
mesmo tipo, deadline e identidade de destino. Mirrors de artefato também precisam
compartilhar tamanho e SHA-256 esperados.

## Deadline e mirrors

`download()` cria um deadline absoluto para uma origem. Esse mesmo orçamento
cobre conexão, redirects, headers, leituras, tentativas e pausas de retry. Cada
leitura recebe no socket apenas o tempo monotônico restante.

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

- artefato ou payload: no máximo 512 MiB;
- catálogo do instalador: no máximo 2 MiB;
- resposta do Hub: no máximo 1 MiB;
- descoberta HTTP de manutenção: no máximo 4 MiB;
- Git nativo: stdout 32 MiB, stderr 1 MiB, workspace temporário 128 MiB e
  deadlines de 60 segundos para consultas ou 300 segundos para árvores.

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

- Unix usa `curl --proto '=https' --proto-redir '=https'`, timeout de conexão,
  orçamento único para retries e mirrors, limite durante a recepção e validação
  de `Content-Length`, tamanho e SHA-256;
- PowerShell incorpora um downloader Python com HTTPS, limite, deadline,
  `Retry-After`, temporário privado no POSIX e promoção atômica;
- as cópias canônica e pública são obrigadas a permanecer idênticas por teste.

Essas projeções existem somente para obter o zipapp fixado; não autorizam um
novo downloader para clientes, componentes ou metadados do produto.

## Fronteiras externas e riscos aceitos neste ADR

- `BoundedMetadata` limita transporte e memória, mas catálogos ainda não têm
  expiração, proteção contra rollback/freeze, rotação de chaves ou raiz de
  confiança fixada. Esse contrato pertence ao PR 9 e à
  [issue #48](https://github.com/x86dx2/x86qw/issues/48).
- a descoberta por Git nativo não usa a API HTTP: ela possui adaptador próprio
  com HTTPS obrigatório, redirects desativados, configuração de TLS isolada,
  deadline absoluto, limites de stdout/stderr e cota do clone temporário. O
  grupo inteiro é encerrado no POSIX; no Windows o processo nasce suspenso e é
  associado a Job Object antes de executar;
- leituras da API de releases GitHub passam por `BoundedMetadata`. Os comandos
  `gh` restantes e o `curl` de upload GitLab são operações de publicação de
  saída, não rotas alternativas de ingestão; ainda precisam ser redesenhados
  como promoção imutável no PR 10;
- o `curl` do bootstrap é a projeção limitada descrita acima;
- privacidade por DACL no Windows não é resolvida pelo modo POSIX `0600` e
  permanece no trabalho específico de ACL.
- testes portáveis da fronteira não substituem smokes nativos de rede ou dos
  runtimes.

Essas exceções não são rotas alternativas autorizadas para instalar payload não
fixado. Elas precisam ser eliminadas ou formalmente absorvidas pelos PRs que
tratam trust metadata, automação de release e ACL antes da versão 1.0.0.

## Consequências

Um novo consumidor Python direto de `urlopen` ou `urlretrieve` falha no teste
estático. URLs da fronteira HTTP exibidas em diagnóstico omitem credenciais,
caminho, query e fragmento; o adaptador Git pode identificar o caminho do
repositório público previamente validado. Erros da fronteira são tipados, o
destino final anterior é preservado até a promoção e a orquestração de mirrors
pode distinguir falha remota de falha local. Em troca, todo novo consumidor
precisa declarar limite, deadline e, para conteúdo persistente, identidade
criptográfica antes de receber bytes.
