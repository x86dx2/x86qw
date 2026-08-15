# Audiência e claims de suporte

Audiência é uma decisão de autorização, não um sinônimo de bytes publicados.
O candidato `x86qw-installer-1.0.0` existe, mas a fotografia de auditoria
classifica o owner-only como `AT-RISK` enquanto a cadeia de verdade e os P1s
não forem reconciliados.

## Matriz atual

| Audiência | Estado | O que pode ser dito | O que não pode ser dito |
| --- | --- | --- | --- |
| `owner-only` | AT-RISK | existe publicação final e E3 M3 documentado | que todos os gates operacionais estão fechados |
| `external-public` | NO-GO | é uma intenção futura sujeita a EP-1–EP-5 | que a release está pronta para usuários externos |
| `preview` por plataforma | VERIFIED POLICY | artefato/contrato pode ser consultado | que houve execução nativa do candidato |
| `supported` | BLOCKED onde não há E3 | somente após evidência nativa do candidato exato | inferir por CI portátil ou presença do ZIP |

## Owner-only limitado

O escopo owner-only permite um mantenedor, instalação limpa descartável e
aceitação no Apple M3. A aceitação pública final registrada usa `single-user`;
ela não comprova migração histórica nem experiência de usuários externos. A
observação 0E deve registrar versão, catálogo/TUF, lifecycle, update, repair,
uninstall/purge e qualquer incidente sem alterar os bytes já publicados.

## Transição para `external-public`

O mantenedor precisa registrar uma decisão EP-0 explícita. Sem ela, as
capacidades externas ficam estacionadas. Com ela, a ordem obrigatória é:

1. EP-1: migração real com fixtures `0.7.0–0.7.13`, preservação e rollback;
2. EP-2: soak exato de sete dias, com uma referência HTTPS e hardware por dia;
3. EP-3: custódia, renovação e recuperação TUF de produção;
4. EP-4: aceitação por usuário externo no endpoint e candidato exatos;
5. EP-5: decisão de plataforma, mantendo `preview` até haver E3 específico.

O resumo de cada etapa deve conter commit, digests, run, artefacto, endpoint
observado, timestamp e Checker. Falha ou evidência ausente mantém `NO-GO`.

## Matriz de evidência nativa

| Plataforma | Artefato | Contrato portátil | Evidência nativa do candidato exato | Estado |
| --- | --- | --- | --- | --- |
| macOS arm64/M3 | sim | sim | E3, 25/25 | conditional |
| macOS Intel | sim | sim | não observada | preview |
| Linux x86_64 | sim | sim | não observada | preview |
| Windows x64 | sim | sim | não observada; contrato 3.10 ainda vermelho | preview |
| macOS nightly | sim | sim | insuficiente | preview |

Disponibilidade do artefato não é suporte. Cada promoção exige issue, owner,
Checker, candidato/digest e execução nativa no ambiente declarado.

## Claims proibidos nesta fase

- não afirmar headers de site, Core Web Vitals ou latência sem captura
  reproduzível;
- não chamar Linux, Windows, macOS Intel ou nightly de `supported` por causa do
  instalador físico;
- não chamar um mirror de pacote de operação redundante;
- não tratar QWLeague como integração oficial sem contrato/API/OAuth/webhook
  verificado;
- não apresentar o RC como evidência final quando a receipt aponta para ele.

## Fontes

- [release truth](RELEASE-TRUTH.md);
- [readiness external-public](EXTERNAL-PUBLIC-READINESS.md);
- [política de plataformas](../PROJECT-STATUS.md);
- [ADR owner-only](../adr/0008-owner-only-release-gates.md).
