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
- runtime e manutenção possuíam implementações diretas e divergentes de
  `urlopen`.

## Implementação

`maintenance/tools/downloader.py` passou a ser a fronteira Python para entrada
HTTP. A API pública desta unidade é composta por:

- `download(contract)`: uma URL e um deadline total;
- `download_mirrors(contracts)`: URLs equivalentes, validadas antes da rede e
  submetidas ao mesmo deadline absoluto;
- `build_https_opener()`: transporte HTTPS com cancelamento da fase de conexão e
  headers;
- erros tipados para política, redirect, protocolo, limite, integridade,
  deadline, armazenamento, HTTP permanente e falha transitória.

Os contratos são:

| Contrato | Uso | Identidade | Persistência |
|---|---|---|---|
| `PinnedArtifact` | bundle, cliente, componente e arquivo remoto declarado | tamanho e SHA-256 prévios | promoção atômica |
| `BoundedMetadata` | catálogo, Hub e descoberta | limite e TLS; sem identidade versionada | memória ou headers de `HEAD` |
| `BoundedPayload` | intake exclusivo da manutenção | tamanho descoberto antes da transferência e identidade independente quando disponível; caso contrário, transação e revisão explícitas | staging antes da validação do chamador |

`PinnedArtifact` exige URL HTTPS, destino, tamanho esperado, SHA-256 esperado,
tamanho máximo, deadline e política de retry. Os contratos dinâmicos não exigem
um hash que o upstream não fornece, mas sempre exigem limite e deadline e não
podem ser apresentados como conteúdo autenticado.

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
SHA-256 dentro do loop de mirrors.

## Deadline e fallback

O deadline é monotônico e absoluto. Em `download()`, ele abrange conexão,
redirects, headers, streaming, retries e respectivas pausas. Em
`download_mirrors()`, o instante final é calculado uma vez antes do primeiro
mirror e compartilhado por todas as origens e tentativas.

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

- artefatos e payloads: 512 MiB;
- catálogo: 2 MiB;
- Hub: 1 MiB;
- descoberta HTTP: 4 MiB;
- Git nativo: 32 MiB de stdout, 1 MiB de stderr, 128 MiB de workspace e
  deadline de 60/300 segundos conforme a operação;
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
- um deadline compartilhado entre retries e mirrors;
- redirects para HTTP e headers privados entre origens;
- corpos intermediários dos cinco status de redirect sem leitura ilimitada e
  compatibilidade de HTTP 308 com Python 3.10;
- matriz de HTTP transitório e permanente;
- `Retry-After` numérico, HTTP-date, inválido e maior que o deadline;
- backoff e jitter determinísticos;
- `EACCES`, `ENOSPC`, escrita curta e falhas de fechamento, `flush`, `fsync` e
  `replace`;
- ordem da promoção e preservação do destino e dos temporários em falhas.

Os bootstraps possuem regressões próprias para HTTP 200 corrompido no primeiro
mirror, transferência parcial e `IncompleteRead`, `Content-Length` divergente,
orçamento compartilhado, timeout real de conexão, redirects sem drenar corpo,
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
nem impede rollback ou freeze. Intake sem digest oficial continua exigindo
confirmação, validação e revisão humana antes do commit. A autenticação
versionada pertence ao PR 9 e à issue #48.

Git nativo permanece fora da API HTTP, mas não sem limites: seu adaptador exige
HTTPS, neutraliza configuração herdada capaz de reduzir TLS, limita pipes e
workspace, compartilha deadline e encerra a árvore de processos. Leituras da API
GitHub foram migradas para `BoundedMetadata`. `gh` e o `curl` restantes publicam
bytes para os mirrors; sua substituição pela promoção imutável pertence ao PR 10.
As projeções de bootstrap continuam necessárias antes da existência do zipapp.

O scanner estático reduz bypasses Python diretos, mas não transforma operações
externas de publicação em consumidores da API. DACL privada no Windows pertence
ao trabalho específico de ACL. Testes portáveis também não substituem smokes
nativos dos clientes, serviços ou transporte público.

## Validação

Esta nota descreve código corretivo ainda não publicado. A evidência final do PR
deve registrar separadamente:

- suíte dedicada do downloader;
- suíte integral de manutenção e site;
- `maintenance/manage.py verify`, `git diff --check` e `git lfs fsck`;
- dry-run do Worker;
- jobs Ubuntu, macOS e Windows com Python 3.10 e 3.13;
- testes comportamentais dos dois bootstraps.

Nenhum desses testes é apresentado como smoke nativo dos runtimes. A versão
`0.7.1`, seus bundles e seus ponteiros públicos permanecem imutáveis enquanto
esta implementação estiver em revisão.
