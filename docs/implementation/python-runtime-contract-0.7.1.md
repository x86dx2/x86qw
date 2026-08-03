# Contrato do runtime Python — correção 0.7.1

Esta nota registra a implementação da issue
[#44](https://github.com/x86dx2/x86qw/issues/44). Ela não autoriza a publicação
da `0.7.1` antes de todos os checks obrigatórios.

> Estado: implementação candidata na branch corretiva. A `0.7.0` permanece
> `current`; nenhum comportamento descrito como candidato deve ser anunciado
> como disponível no bootstrap público antes da promoção.

## Baseline e defeito

- baseline: `57c7a06745d3d760a2ace70112beb6bbd391d633`;
- release pública: `x86qw-installer-0.7.0`;
- o PowerShell aceitava `py -3`, `python3` ou `python`, mas `x86qw.cmd` usava
  somente `py -3`;
- Unix usava somente `python3` e não rejeitava Python 3.9 antes do download;
- o bootstrap Unix carregava o bundle inteiro para calcular SHA-256.

## Decisão

O contrato mínimo está em `dist/installer/bin/python_runtime.py`:

- versão mínima `(3, 10)`;
- probe por `sys.version_info`, sem parsing de texto localizado;
- geração dos launchers com o `sys.executable` que executou a instalação;
- rejeição de caminho com caracteres de controle;
- quoting específico de shell e CMD.

Os wrappers precisam localizar Python antes de poder importar esse módulo. Por
isso implementam projeções mínimas do mesmo contrato:

- Unix: `python3`, depois `python`;
- Windows: `py -3`, `python3`, depois `python`;
- o runtime persistido é tentado primeiro e precisa passar novamente no probe;
- nenhum candidato compatível significa falha antes de rede ou mutação.

O handoff de `update`/`upgrade` continua usando uma lista de argumentos com
`sys.executable`, sem shell. O processo final instala a nova CLI e gera os
launchers; não existe liberação/reaquisição no meio da mutação.

## Isolamento da sessão PowerShell

O bootstrap candidato é avaliado por `irm ... | iex` dentro da sessão atual,
mas encapsula seu corpo em um escopo de script próprio. Não há processo filho
nem nova janela. Variáveis internas, inclusive `$ErrorActionPreference`, não
devem escapar desse escopo; as codificações de console e pipeline são
restauradas em `finally`. O único efeito global intencional é
`$global:LASTEXITCODE`, que recebe exatamente o código do instalador Python.

O erro continua visível no terminal e o bootstrap não executa `exit`. A
evidência planejada precisa invocar o arquivo por `Invoke-Expression` em um
harness que já possua estado próprio e comprovar:

- a sessão chamadora continua viva após sucesso e falha;
- `$ErrorActionPreference` e as codificações permanecem como antes;
- nenhuma variável interna, como `InstallerVersion`, vaza para o chamador;
- `$global:LASTEXITCODE` preserva o código do Python;
- o diagnóstico de falha permanece visível e não expõe saída descartada do
  probe.

Essa evidência deve passar no Windows PowerShell 5.1 do runner Windows e em
PowerShell 7 quando disponível. A execução local em PowerShell fora do Windows
é complementar e não substitui a matriz nativa.

## Contratos preservados

- comandos e códigos de saída existentes;
- argumentos ilimitados no CMD;
- paths com espaços e Unicode;
- instalação sem privilégios administrativos;
- stable e nightly lado a lado;
- bundles e tags publicados, inclusive `0.7.0`, byte a byte imutáveis.

## Contratos candidatos

- Python abaixo de 3.10 falha antes de download ou mutação;
- launchers deixam de depender de um único nome global de executável;
- SHA-256 do bootstrap Unix é calculado em blocos de 1 MiB;
- o zipapp possui uma guarda de versão antes de importar o gerenciador.

## Evidência planejada e exigida antes da publicação

- testes com somente `python`, somente `python3`, somente `py`, todos presentes
  e runtime persistido removido;
- Python 3.9 rejeitado antes do downloader;
- Python 3.10 e 3.13 nos três sistemas;
- paths com espaços/Unicode, comandos públicos e argumentos longos;
- `git diff --check`, Git LFS, `manage.py verify` e sete jobs do Actions.

## Riscos residuais

- a resolução precisa continuar duplicada nos wrappers até existir um runtime
  Python que possa carregar o módulo canônico;
- o hardening de transporte e limites do download pertence à issue #45;
- a `0.7.1` permanece não publicada até promoção explícita após os checks.
