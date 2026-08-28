# TUF — SLO e recuperação

## Estado corrente

Em 2026-08-28T02:32:21Z, a cadeia pública autenticou root v1, timestamp v30,
snapshot/targets v29 e 75 pacotes. O timestamp expira em
2026-09-27T02:15:12Z, fora da janela de alerta de 6 horas. O snapshot expira
depois do timestamp, em 2026-11-26T02:07:54Z. O monitor público retornou
saudável nos domínios canônico e alias. A correção histórica da versão da
root está registrada na [errata TUF](ERRATA-TUF-ROOT-VERSION.md).

A cerimônia owner-only v29/v30 renovou snapshot e timestamp sem alterar
catálogo, root ou targets:

- handoff/source run: 33135065604; artifact 9671745396;
- renewal run: 33135314707; artifact 9671800710;
- projection verification run: 33136179763; artifact 9672118367;
- timestamp: v29 → v30;
- snapshot/targets: v29;
- renewal report SHA-256:
  fe90b29ca4aa49f3b3c5a33897edd67b7069685d1622f3ae8d85f348a172e7cb.

O recovery drill técnico foi registrado no run 31900793093, artifact 9251029392,
com status drill-passed, expiry failure detected e recovery verified. A
publicação timestamp-only passou no run 31900914825, artifact 9251063517, e a
verificação pública confirmou TUF, bootstraps e product. A issue #152 foi
encerrada como alerta operacional resolvido.

No modo owner-only o timestamp vive 30 dias e o snapshot 90 dias, para o
snapshot sobreviver ao timestamp. O alerta de 6 horas e o fail-closed para
mutação remota não mudam. Antes de catálogo `external-public` a política
volta a snapshot 7 dias e timestamp 24 horas, com chaves separadas.

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
