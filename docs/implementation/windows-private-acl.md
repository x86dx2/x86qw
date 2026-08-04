# DACL privada no Windows — implementação corretiva

**Baseline inicial:** `b746d577fab5ffd95f170feb28b8a4bd4d4ce76c`

**Versão pública preservada:** `0.7.1`

**Issue:** [#47](https://github.com/x86dx2/x86qw/issues/47)

**Pull request:** [#60](https://github.com/x86dx2/x86qw/pull/60)

**Estado:** casos Win32 nativos aprovados; não publicada

Esta nota registra o código corretivo da PR 4. Ela não altera versão, catálogo
`current`, bootstrap público, bundle, tag ou release já publicados.

## Problema e risco

Modos POSIX como `0600` e criação exclusiva não impediam que um objeto Windows
herdasse ACEs de `Users` ou `Everyone`. Locks, journals, logs, configurações
sensíveis, staging, downloads e o bootstrap poderiam ficar legíveis ou
alteráveis por outro usuário local entre a criação e um endurecimento tardio.

A revisão adversarial também reproduziu disputas de `DELETE_CHILD`, promoção
por pathname depois de fechar o temporário, mutex preexistente hostil, recovery
de journals legados e uma janela em que o processo Windows poderia executar
antes da associação ao Job Object.

## Contratos preservados

- biblioteca padrão, sem dependência externa;
- instalação sem solicitação de elevação;
- leitura de locks formatos 1 e 2;
- journal formato 1, recibos, estado e configurações pessoais;
- prompts e arquivos externos de senha, sem reescrever seu owner, DACL, nome ou
  conteúdo;
- semântica conservadora de recovery e cleanup;
- runtimes, jogos, mods, mapas, serviços e gameplay existentes;
- bytes, hashes, tag, release e ponteiros públicos da `0.7.1`.

## Contratos alterados

- objetos privados gerenciados no Windows nascem com DACL protegida, limitada
  ao usuário atual e a `LOCAL SYSTEM`;
- um arquivo externo de senha precisa ter owner e DACL privados comprovados
  pelo mesmo handle usado para leitura;
- lock novo usa formato 3 com `private_filesystem`; journal formato 1 recebe
  extensão aditiva para registrar a fronteira privada;
- falha, ambiguidade, reparse point, herança ou principal adicional interrompem
  a operação antes de consumir o objeto;
- um processo Windows nasce suspenso, entra no Job Object e só então executa.

## Implementação

`x86qw_runtime/platform/windows_acl.py` concentra o adaptador Win32 e declara
assinaturas `ctypes` explícitas. Criação usa `SECURITY_ATTRIBUTES` com descritor
canônico desde a primeira visibilidade. Validação e leitura permanecem ligadas
ao mesmo handle, e leases sem compartilhamento de delete bloqueiam rename e
remoção concorrentes.

A promoção de downloads usa `SetFileInformationByHandle` com
`FILE_RENAME_INFO`, mantendo o temporário aberto até a substituição. A ABI usa
o `BOOLEAN` de um byte exigido pela API e um caminho absoluto no mesmo volume.
Nenhum shell participa da operação.

O plano de controle mantém o lease da raiz somente depois de adquirir o lock.
Observação e reclamação são serializadas por `flock` no POSIX e mutex global
privado no Windows. Objetos legados limpos podem migrar de forma conservadora;
objetos interrompidos ou inconclusivos são preservados ou quarentenados sem
`kill` ou `unlink` por inferência.

O bootstrap PowerShell cria `WorkDir`, helper e bundle sob a mesma DACL antes
da primeira escrita. O supervisor inicia processos Windows suspensos, associa
ao Job Object com `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` e retoma a thread apenas
depois da associação bem-sucedida.

## Ciclo TDD e evidência

Os testes adversariais foram introduzidos antes das correções. O run
[`30864723157`](https://github.com/x86dx2/x86qw/actions/runs/30864723157)
registrou o RED das disputas restantes. Runs intermediários expuseram o guard
aplicado ao handle errado, ABI incompatível de `FILE_RENAME_INFO` e destino
relativo em outro volume. Cada caso recebeu uma regressão focal antes da menor
correção correspondente.

| Validação | Resultado | Evidência |
|---|---|---|
| fronteira privada, downloader e delete guards | 107 testes aprovados; 19 skips nativos esperados fora do Windows | execução local focal |
| Windows / Python 3.10 | 745 testes de manutenção e cinco do site aprovados | [run 30866754435](https://github.com/x86dx2/x86qw/actions/runs/30866754435) |
| Windows / Python 3.13 | 745 testes de manutenção e cinco do site aprovados | [run 30866754435](https://github.com/x86dx2/x86qw/actions/runs/30866754435) |
| `maintenance/manage.py verify` | 745 testes de manutenção e cinco do site aprovados | execução local integral |
| suítes explícitas de manutenção e site | 745 + 5 aprovados; 35 skips locais explícitos | execução local integral |
| Git LFS, diff e Worker | `git lfs fsck`, `git diff --check` e dry-run aprovados | execução local integral |

Os jobs Windows cobrem criação sob pai com herança ampla, DACL canônica,
reparse points, arquivo externo inseguro, bootstrap, lock, journal, log,
configuração sensível, staging, download, rename/delete guards, mutex hostil,
migração legada e Job Object.

## Riscos residuais

- os testes nativos Win32 não equivalem a instalar e executar os runtimes sob
  uma conta Windows padrão sem elevação; isso permanece no PR 11;
- ezQuake, MVDSV, QTV e QWFWD reais não foram executados por este PR;
- administrador, kernel comprometido e código executando como o mesmo usuário
  permanecem fora do limite de ameaça;
- filesystem sem ACL persistente ou API inconclusiva falha fechado;
- esta evidência é de código corretivo, não de um candidato imutável de release.

## Próximo item desbloqueado

Após review e CI integral verdes, a PR 5 pode remover a assinatura ad hoc do
cliente stable macOS em issue e branch separadas. Este documento não autoriza
publicação.
