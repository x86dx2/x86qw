# TUF — SLO e recuperação

## Estado observado

No intervalo da auditoria, a cadeia pública autenticada estava tecnicamente
`HEALTHY`:

| Role | Versão | Expiração/observação |
| --- | ---: | --- |
| root | 2 | root ancorada; nenhuma rotação nova afirmada |
| timestamp | 18 | lease observada até `2026-08-15T21:09:01Z` |
| snapshot | 18 | cadeia consistente no snapshot |
| targets | 18 | targets consistentes no snapshot |

Houve incidente transitório de reachability e um aviso de seis horas para a
lease. Custódia independente e recuperação de produção não estão comprovadas;
logo `0B` permanece `BLOCKED`. O endpoint canônico observado é
`https://x86qw.x86.com.br/api/v1/trust/metadata/`; esta referência não é uma
declaração de headers, CWV ou disponibilidade contínua.

## SLO proposto

Os números abaixo são `PROPOSAL` e precisam de aprovação antes de virarem
política operacional:

| Sinal | Meta proposta | Alerta | Ação |
| --- | --- | --- | --- |
| timestamp dentro da validade | 100% das verificações | 6 h antes do expiry | abrir incidente e congelar promoção |
| cadeia autenticada | 100% por consulta válida | qualquer falha | fail closed, comparar mirror e root |
| reachability do endpoint | disponibilidade a definir com dados | incidente transitório já observado | observar por rede independente |
| recovery | exercício trimestral | ausência de evidência | manter `external-public=NO-GO` |
| divergência de bytes | zero | primeira divergência | não sobrescrever; preservar ambos os receipts |

Não inventar percentuais de uptime, HTTP status, latência ou CWV sem uma
janela de medição reproduzível.

## Papéis e custódia

- **Maker:** prepara metadata ou o relatório de renovação no ambiente
  autorizado;
- **Checker:** valida root, versões, expiração, hashes, ordem metadata-last e
  ausência de mudanças fora da role permitida;
- **Custodian:** mantém chaves e recuperação fora do checkout;
- **Operator on-call:** responde a alertas e registra tempo até contenção.

Uma única pessoa não deve ser Maker, Checker e Custodian para uma promoção
external. Enquanto não houver substituto/custódia documentados, a falha é
`BLOCKED`, ainda que a criptografia passe.

## Runbook de recuperação

1. Confirmar o incidente em uma rede independente e salvar timestamp, endpoint,
   root usada e hora UTC.
2. Colocar promoção/publicação em freeze; não trocar URL ou desabilitar TUF.
3. Comparar timestamp, snapshot, targets e root com o último receipt conhecido.
4. Se apenas a lease expirou, executar o caminho limitado aprovado para
   timestamp; se root/targets divergirem, parar e exigir cerimônia de rotação.
5. Restaurar metadata a partir do artefato custodiado, verificar assinaturas,
   versões, expirações e consistência antes de publicar.
6. Repetir a verificação pública e registrar o relatório assinado, o Checker,
   o incidente e o tempo de recuperação.
7. Manter `NO-GO` até a revisão pós-incidente fechar causas, exposição e
   prevenção. Nunca usar catálogo legado ou bypass de URL.

## Critérios de fechamento de 0B

- custódia e recuperação independentes com responsáveis nomeados;
- drill online/offline reproduzível sem expor chaves;
- alertas de 6 h e expiry verificados;
- lease sustentada por janela aprovada;
- endpoint e mirrors observados sem divergência;
- receipt ligado à release e à audiência corretas.

Esses critérios são pré-condição de 0C e da observação owner-only. O gate
EP-3, após o soak EP-2, repete a verificação com o candidato external-public
exato; essa revalidação não posterga o fechamento inicial de 0B.

Ver também [runbook TUF](../runbooks/tuf-operation.md),
[key management](../runbooks/key-management.md) e os itens `POST-009`–
`POST-011` do [backlog](ISSUE-BACKLOG.md).
