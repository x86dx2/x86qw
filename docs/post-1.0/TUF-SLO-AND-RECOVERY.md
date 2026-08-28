# TUF — SLO e recuperação

## Estado corrente

Em 2026-08-27T23:18:37Z, a cadeia pública autenticou root v1, timestamp v28,
snapshot/targets v27 e 75 pacotes. O timestamp expira em
2026-09-03T19:15:09Z, fora da janela de alerta de 6 horas. O monitor público
retornou saudável nos domínios canônico e alias. A correção histórica da
versão da root está registrada na [errata TUF](ERRATA-TUF-ROOT-VERSION.md).

A renovação limitada foi executada sem alterar catálogo, root ou targets:

- handoff/source run: 33107300472; artifact 9661014418;
- renewal run: 33107505069; artifact 9661074451;
- projection verification run: 33125534974; artifact 9668294484;
- timestamp: v27 → v28;
- renewal report SHA-256:
  eb68c1448e6fe6a0d2aa8df6ca9531710927fd4498b37fc854fb43db122be811.

O recovery drill técnico foi registrado no run 31900793093, artifact 9251029392,
com status drill-passed, expiry failure detected e recovery verified. A
publicação timestamp-only passou no run 31900914825, artifact 9251063517, e a
verificação pública confirmou TUF, bootstraps e product. A issue #152 foi
encerrada como alerta operacional resolvido.

No modo owner-only a expiração máxima de timestamp é 7 dias (emenda do ADR
0006 em 2026-08-16). O alerta de 6 horas e o fail-closed para mutação remota
não mudam. O teto de 24 horas volta a valer antes de catálogo público.

## SLO vigente/proposto

Os números abaixo continuam PROPOSAL até decisão operacional formal:

| Sinal | Meta proposta | Alerta | Ação |
| --- | --- | --- | --- |
| timestamp dentro da validade | 100% das verificações | 6 h antes do expiry | abrir incidente e congelar promoção |
| cadeia autenticada | 100% por consulta válida | qualquer falha | fail closed, comparar mirror e root |
| reachability do endpoint | medir por vantage independente | falha transitória | observar, registrar e usar fallback previsto |
| recovery | exercício periódico com custódia independente | ausência de evidência | manter external-public=NO-GO |
| divergência de bytes | zero | primeira divergência | não sobrescrever; preservar receipts |
| recuperação de produção | RTO definido e exercitado | ausência de owner/backup | manter external-public=NO-GO |

## Gap operacional residual

O drill técnico/local e o drill protegido provaram o protocolo de expiração,
renovação limitada e recuperação sem publicação indevida. Eles não provam ainda
custódia humana independente, backup custodian, owner de RTO de produção,
cadência recorrente de exercícios ou sucessão. Esses gaps continuam sendo
requisitos de EP-3 e mantêm external-public=NO-GO; o issue #148 foi encerrado
após registrar a capacidade técnica, sem provar custódia independente ou
autorização para abrir a audiência.

Os monitores que observaram leases antigas dentro da janela de 6 horas ficam
arquivados como histórico. A lease v28 atual e a projeção corrente foram
verificadas de forma independente após o deploy.

## Runbook resumido

1. Confirmar alerta em uma rede independente e salvar timestamp, endpoint, root
   e hora UTC.
2. Colocar promoção/publicação em freeze; não trocar URL nem desabilitar TUF.
3. Comparar timestamp, snapshot, targets e root com o receipt custodiado.
4. Se apenas a lease expirou, executar o caminho limitado aprovado para
   timestamp; se root/targets divergirem, parar e exigir cerimônia de rotação.
5. Restaurar metadata a partir do artefato custodiado, verificar assinaturas,
   versões e expirações antes de publicar.
6. Repetir a verificação pública, registrar Checker, incidente e RTO.
7. Manter external-public=NO-GO até a revisão pós-incidente e a custódia
   independente fecharem as causas.

Nenhuma renovação deve ser executada fora do workflow protegido sem autorização
operacional equivalente. Sustentabilidade e recuperação repetível continuam
gates external-public; o trabalho owner-only pode avançar enquanto S0-M3
permanecer verde.
