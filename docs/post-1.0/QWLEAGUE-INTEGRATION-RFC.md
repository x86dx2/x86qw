# RFC de integração QWLeague

**Estado:** `BLOCKED_EXTERNAL` — discovery read-only; não há autorização para
fila, conta, token, registro de protocolo, OAuth, webhook ou mutação remota.

## Fatos observados

- somente a home e o sitemap públicos foram usados como fonte nesta avaliação;
- não há contrato oficial verificado de API, OAuth ou webhook;
- portanto não é possível declarar provider, disponibilidade, autenticação ou
  SLA para x86QW;
- a integração não é dependência de `external-public` e não desbloqueia
  feature work.

## Proposta de fronteira

Se o mantenedor priorizar a fase `1.3 ecosystem`, o primeiro corte deve ser
read-only: descobrir partidas/servidores e mostrar dados públicos apenas
quando um contrato oficial, versão, limites e autorização forem fornecidos.
Nenhuma ação deve iniciar jogo, entrar em fila ou persistir credenciais sem uma
decisão própria.

| Área | Proposta | Bloqueio |
| --- | --- | --- |
| descoberta | endpoint oficial e schema versionado | contrato/API ausente |
| autenticação | OAuth/documentação do provedor | nenhuma criação ou token nesta fase |
| atualização | polling/stream com timeout e backoff | SLA/rate limit ausente |
| launch | resumo, validação host/port e confirmação humana | protocolo oficial ausente |
| demos/QTV | link público somente | permissão, retenção e privacidade não definidas |
| telemetria | mínima, opt-in, sem token | DPIA/política não definida |

## Critérios para sair de `BLOCKED_EXTERNAL`

1. contrato oficial publicado e versionado;
2. autorização escrita do provedor para o uso pretendido;
3. modelo de dados, rate limit, erro, disponibilidade e privacidade;
4. teste com fixture pública, sem credenciais reais;
5. ameaça, validação de host/port e rejeição de metacaracteres;
6. ADR próprio em `2.0` se o uso alterar a fronteira do produto;
7. Checker independente e rollback documentado.

## Não-escopo

Não criar conta, entrar em queue, obter token, registrar webhook, reutilizar
cookie/API interna, publicar no QWLeague ou prometer integração funcional. Não
há issue remota criada por esta RFC.

## Dependências

Esta RFC depende de gates 0A–0D apenas para governança documental, mas não
depende de EP-1–EP-5. A execução de qualquer código pertence ao backlog local
`POST-016` e à fase `1.3`.
