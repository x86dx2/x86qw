# TUF — SLO e recuperação

## Estado corrente

Em 2026-08-16T18:01:03Z, a cadeia pública autenticou root v1, timestamp v22,
snapshot/targets v19 e 75 pacotes. O timestamp expira em
2026-08-23T17:56:54Z (~7 dias, política owner-only), fora da janela de alerta
de 6 horas. O monitor público retornou saudável. A correção de root v2 para v1
está registrada na [errata TUF](ERRATA-TUF-ROOT-VERSION.md).

A renovação limitada foi executada sem alterar catálogo, root ou targets:

- handoff run: 31962999249; artifact 9267728705;
- renewal run: 31963102361; artifact 9267748878;
- publication run: 31963192991; artifact 9267779828;
- timestamp: v21 → v22;
- renewal report SHA-256:
  94621a3e2083ac7d3e00788814e1080449d4a5ee65d8d98359d1d5d5a2b57aa2.

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
| recuperação de produção | RTO definido e exercitado | ausência de owner/backup | manter #148 aberto |

## Gap operacional residual

O drill técnico/local e o drill protegido provaram o protocolo de expiração,
renovação limitada e recuperação sem publicação indevida. Eles não provam ainda
custódia humana independente, backup custodian, owner de RTO de produção,
cadência recorrente de exercícios ou sucessão. Esses gaps permanecem em #148 e
mantêm external-public=NO-GO.

Os monitores agendados 31893247113 e 31898859941 falharam porque observaram a
lease antiga dentro da janela de 6 horas; isso foi resolvido pela renovação v19.
A publicação corrente foi verificada de forma independente após o deploy.

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
operacional equivalente. O próximo trabalho é sustentabilidade e recuperação
repetível, não novas features.
