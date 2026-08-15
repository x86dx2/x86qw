# TUF — SLO e recuperação

## Estado corrente

Em 2026-08-15T15:38:43Z, a cadeia pública autenticou root v2,
timestamp/snapshot/targets v18 e 75 pacotes. O timestamp expira em
2026-08-15T21:09:01Z (aproximadamente 5,5 horas no momento da observação).
Isso é **TUF=WARNING**, porque o workflow e o monitor usam janela de alerta de
6 horas; a cadeia técnica ainda está íntegra. Uma consulta com limiar de 1 hora
retornou saudável.

O alerta não foi renovado automaticamente. Nenhum signer, secret, workflow de
publicação, endpoint, catálogo ou TUF foi alterado pela auditoria. A renovação
precisa do operador/custódia autorizados.

## SLO proposto

Os números abaixo continuam PROPOSAL até decisão operacional:

| Sinal | Meta proposta | Alerta | Ação |
| --- | --- | --- | --- |
| timestamp dentro da validade | 100% das verificações | 6 h antes do expiry | abrir incidente e congelar promoção |
| cadeia autenticada | 100% por consulta válida | qualquer falha | fail closed, comparar mirror e root |
| reachability do endpoint | medir por vantage independente | falha transitória | observar, registrar e usar fallback previsto |
| recovery | exercício periódico com custódia independente | ausência de evidência | manter external-public=NO-GO |
| divergência de bytes | zero | primeira divergência | não sobrescrever; preservar receipts |

## Gap operacional

Existe um drill técnico/local com chaves efêmeras e cinco testes aprovados
(maintenance.tests.test_tuf_operation_drill). Isso não prova drill de
produção, custódia independente, owner de backup ou RTO. O workflow
tuf-operation-drill ainda não tem execução registrada. O monitor agendado
teve última execução observada com sucesso, mas a issue #152 continua aberta e
não registra recuperação como estado.

## Runbook

1. Confirmar o alerta em uma rede independente e salvar timestamp, endpoint,
   root usada e hora UTC.
2. Colocar promoção/publicação em freeze; não trocar URL nem desabilitar TUF.
3. Comparar timestamp, snapshot, targets e root com o receipt custodiado.
4. Se apenas a lease expirou, executar o caminho limitado aprovado para
   timestamp; se root/targets divergirem, parar e exigir cerimônia de rotação.
5. Restaurar metadata a partir do artefato custodiado, verificar assinaturas,
   versões e expirações antes de publicar.
6. Repetir a verificação pública, registrar Checker, incidente e RTO.
7. Manter NO-GO até a revisão pós-incidente fechar causas e prevenção.

**Não execute a renovação a partir desta auditoria.** O próximo passo operacional
é um operador autorizado executar a cerimônia de renovação e recovery, ou
retirar explicitamente o install claim antes do expiry se a operação não puder
ser sustentada.
