# Master Plan pós-1.0 — M3-first

MAIN: GREEN
TUF: HEALTHY
1.0.0 OWNER-ONLY: VALID_FOR_SINGLE_USER_M3
EXTERNAL-PUBLIC: NO-GO
FEATURE WORK: ALLOWED_AFTER_S0_M3

## Decisão de escopo

**VERIFIED FACT:** existe um único usuário e o único laboratório nativo disponível é o Apple M3 do mantenedor. A aceitação protegida do candidato 1.0.0 no M3 passou 25/25 casos. A main passou os sete contextos protegidos após a correção determinística do resolver DNS.

**DECISION:** o objetivo corrente não é preparar external-public. É manter uma linha owner-only estável no M3 e liberar capacidade funcional em incrementos pequenos. Migração de instalações de terceiros, soak de sete dias, aceite por usuário externo, custódia independente e suporte de outras plataformas deixam de bloquear o trabalho owner-only.

**PROPOSAL:** a futura primeira publicação aberta poderá começar com uma baseline nova e limpa. Migração só volta a ser requisito se a comunicação prometer upgrade de uma instalação histórica. Não apagar o histórico agora: reset de histórico é uma operação destrutiva separada, com backup, export de evidências e rollback.

## Estado e autoridades

- `source`: baseline current `1.0.6`; `1.0.5`, `1.0.4`, `1.0.3`, `1.0.2` e `0.7.13` permanecem históricas no catálogo.
- `candidate/release`: bytes imutáveis de 1.0.0, installer SHA-256 `d3274e6a...`, audiência `owner-only`.
- `deployment`: site, bootstraps, product, catálogo e TUF servidos; deve convergir com o candidato exato.
- `development`: HEAD atual e seus checks; a main verde é pré-condição de qualquer mudança.

A projeção machine-readable é a autoridade para audiência, suporte, estado de CI e estado funcional. README, site, product, catálogo, CLI e PROJECT-STATUS são projeções testadas, não cópias manuais.

## S0 — estabilidade owner-only no M3

S0 é o único gate que bloqueia funcionalidade:

1. `main` verde, sete contextos protegidos, sem depender de rerun;
2. teste DNS determinístico com relógio controlado nos dois branches de timeout;
3. candidato exato aceito no M3, incluindo install, update, verify, changes, repair, cleanup e uninstall;
4. TUF técnico autenticado, timestamp fora da janela de alerta e renovação executável;
5. deployment convergente: bootstraps, product, catálogo, trust e release-truth respondem pelos mesmos bytes e audiência;
6. nenhum claim de suporte maior que a evidência M3.

**Saída:** `owner-only` válido para o mantenedor, feature work permitido, external-public ainda `NO-GO`.

A observação owner-only continua como operação leve e recorrente. Ela não precisa ser um soak externo nem impedir o próximo incremento funcional quando S0 permanece verde.

## Gates condicionais external-public

Estes gates ficam estacionados até existir decisão explícita de abrir para terceiros:

| Gate | Quando reabre | Critério | Estado atual |
| --- | --- | --- | --- |
| EP-0 decisão de audiência | intenção de publicar para terceiros | ADR com baseline, suporte, contato e SLO | DEFERRED |
| EP-1 migração | promessa de upgrade da baseline antiga | preservação byte a byte e rollback | NOT REQUIRED FOR FRESH BASELINE |
| EP-2 soak | candidato que será external-public | sete dias do digest exato | DEFERRED |
| EP-3 TUF sustentável | audiência externa | custódia, backup, RTO e recuperação | EXTERNAL_ONLY |
| EP-4 aceite externo | audiência externa | usuário não mantenedor em máquina limpa | DEFERRED |
| EP-5 plataformas | cada plataforma promovida | evidência nativa do candidato exato | M3 ONLY |

QWLeague permanece `BLOCKED_EXTERNAL`: sem contato, autorização ou contrato público não existe integração nativa.

## Trem de entrega owner-only

### O0 — estabilização imediata

- manter a main verde e não reabrir a falha temporal;
- concluir e registrar a correção da projeção live (PASS, receipt 33136179763,
  artifact 9672118367);
- executar uma aceitação M3 após a projeção convergir;
- manter TUF renovável e monitorado tecnicamente;
- fechar drift de audiência e de bytes;
- preservar releases, recibos e histórico até decisão destrutiva separada.

### F1 — diagnostics e first-run

Primeira capacidade funcional após S0:

- `x86qw doctor` read-only;
- bundle de diagnóstico sanitizado, revisável e sem segredos;
- status de instalação, catálogo, TUF, runtime, rede, disco e permissões;
- mensagens de first-run que explicam owner-only sem ritual desnecessário.

### F2 — perfis e descoberta local

- perfis user-owned separados de defaults gerenciados, cache e dados efêmeros;
- favoritos e recentes locais;
- busca e entrada em partidas usando contratos do runtime;
- fallback offline e origem/freshness do dado;
- nenhuma dependência obrigatória de QWLeague.

### F3 — hospedagem local

- presets declarativos sem segredos versionados;
- readiness, logs, stop, restart e crash recovery;
- MVDSV, QTV e QWFWD conforme evidência M3;
- rollback e preservação de dados pessoais.

### F4 — expansão opcional

Somente após uso real justificar: biblioteca de demos, treinamento, UI local read-only, adapters externos oficiais e evidência nativa adicional. Daemon persistente, conta online, telemetria, marketplace e fleet control plane não entram por entusiasmo.

## Regras de versão

- teste, documentação ou projeção apenas: sem release artificial;
- correção de runtime, pacote ou bytes compatível: 1.0.x;
- nova capacidade compatível nesta fase: 1.0.x (1.1 aposentado até nova decisão);
- alteração incompatível comprovada: 2.0;
- reset de histórico: decisão operacional destrutiva fora do SemVer do produto.

## Definition of Done de cada funcionalidade

- issue separa outcome, não escopo, dependências, segurança, privacidade, rollback e docs;
- runtime concentra semântica reutilizável; CLI, TUI e UI não duplicam regras;
- teste determinístico cobre sucesso, timeout, interrupção e recuperação;
- execução real no M3 registra versão, candidato, digest e resultado;
- dados pessoais permanecem fora de managed defaults e caches;
- feature indisponível não impede instalação, jogo local ou hospedagem;
- documentação não eleva preview a supported;
- main permanece verde.

## Prioridades

**NOW:** S0 observação owner-only e lease TUF no M3.

**NEXT:** F4 só com uso real (demos, adapters). FUNC-008 (UI local read-only) já está na main.

**LATER:** demos, adapters externos e novas plataformas.

**NOT PLANNED agora:** migração histórica para terceiros, QWLeague transacional, daemon persistente, contas online, telemetria obrigatória e reset destrutivo sem backup.

## Métricas mínimas sem telemetria de usuário

Medir em harness e recibos locais: fresh install success, lifecycle success, update/repair success, preservation incidents, time to first verified match, host readiness, diagnostic resolution, native evidence coverage, release reproducibility, mirror convergence, TUF lease reliability e minutos humanos por release.

## Riscos aceitos e controles

- **Processo grande demais:** S0 é curto; gates externos são condicionais.
- **Custo de mantenedor único:** releases pequenas, rollback explícito e issue templates.
- **TUF sem sucessor:** operação técnica continua agora; custódia independente é requisito apenas antes de external-public.
- **Reset destrutivo:** preservar backup e recibos antes de apagar qualquer histórico.
- **Integração externa indisponível:** adapters opcionais e fallback local completo.

## Próxima ação

A fonte current é **1.0.6**; `1.0.5`, `1.0.4`, `1.0.3`, `1.0.2`, `1.0.1` e `0.7.13` estão históricas. F1–F3 e o
conserto de `host`/launcher ficam em 1.0.x. Manter a lease TUF fora da janela
de 6 h. F4 e EP só com uso real ou decisão de audiência.
