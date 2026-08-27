# Baseline de auditoria pós-1.0

**Estado:** VERIFIED FACT para os valores observados; INFERENCE para
classificações que ainda dependem de confirmação; BLOCKED para gates sem
evidência; PROPOSAL apenas para o que ainda será decidido.

## Escopo e fotografia

Esta é a fotografia aprovada para a issue #164. A auditoria ocorreu em UTC
entre `2026-08-15T01:28:37Z` e `2026-08-15T01:55:48Z`, sobre o commit
`dc080acacba7d70ced0cb311f08a81259bf5a9bd`. O snapshot é datado: não substitui
uma verificação nova da rede, dos runners ou dos releases.

A alegação histórica de root v2 abaixo foi corrigida pela
[errata TUF](ERRATA-TUF-ROOT-VERSION.md), sem reescrever o snapshot datado.

| Área | Estado observado | Evidência | Leitura permitida |
| --- | --- | --- | --- |
| Linha canônica | `origin/main`, snapshot `dc080ac...` | git e auditoria | VERIFIED FACT; revalidar antes de uma promoção |
| Main/CI | `RED` | run `31853649373` | BLOCKED para o gate 0A |
| TUF | `HEALTHY` no instante da auditoria | root v2; timestamp/snapshot/targets v18; validade até `2026-08-15T21:09:01Z` | VERIFIED FACT técnico, com aviso de lease de 6 h |
| Reachability TUF | incidente transitório observado | endpoint público canônico | INFERENCE sobre disponibilidade; não afirmar headers/CWV |
| Custódia/recuperação | ausente para produção | auditoria | BLOCKED para operação sustentável |
| `1.0.0` owner-only | `AT-RISK` na auditoria | conflitos e P1s abaixo | não reclassificar como GO sem fechar a verdade de release |
| `external-public` | `NO-GO` | gates 0A–EP-5 | VERIFIED DECISION |
| Feature work | bloqueado | dependência de gates | não iniciar implementação de produto |

## Identidade do release observada

- tag: `x86qw-installer-1.0.0`;
- commit de produto: `e12ed081b968f820f47200e4be954a4f444056a1`;
- instalador: `600825` bytes, SHA-256
  `d3274e6aa2f1e3078ac5000ffae8b97c9efd329f3c2a87499bf1c57e5f388cb8`;
- `candidate.json`: `17405` bytes, SHA-256
  `0bde0550895cab24abf8a3ee974da011e031fea11279148a41635e173cbdcc21`;
- comparação GitHub/GitLab do instalador: `E2`, igualdade byte a byte;
- rebuild independente `E4`: não executado nesta fotografia;
- candidato exato em Apple M3: `E3`, `25/25` casos.

As referências duráveis existentes são o
[registro de publicação owner-only](../releases/1.0.0-owner-only-publication-2026-08-15.md)
e a [aceitação M3](../releases/1.0.0-owner-only-public-acceptance-m3-2026-08-15.json).
Elas são fontes datadas, não uma autorização para mudar a audiência.

## Limitações e conflitos conhecidos

1. O único vermelho do run `31853649373` é Windows/Python 3.10 no contrato
   portátil. A explicação de que o fake não aceita argumentos e de que existe
   um `sleep` não controlado de 10 ms é **INFERENCE test-only**, até que o
   protocolo seja reproduzido com relógio determinístico. O Gate 0A exige
   Windows Python 3.10 e 3.13 verdes.
2. A receipt final ainda aponta `public_acceptance` para RC1, enquanto a
   evidência exata final está separada. Essa é uma contradição de autoridade,
   não uma prova de que os bytes finais são diferentes.
3. Ownership/SBOM reporta `87/87` itens como `unclassified`/`NOASSERTION`.
4. A promessa de `cleanup --personal-data` inclui `qw`/demos, mas a limpeza
   observada não cobre esses caminhos.
5. Há um único mantenedor e self-review; controles de segurança do GitHub são
   os registrados na auditoria e precisam de verificação independente.
6. O espelho de pacote observado é único; não há redundância comprovada.
7. Linux, Windows, macOS Intel, nightly e qualquer execução nativa que não
   seja o candidato exato no M3 devem permanecer `preview`. Não declarar
   smoke, headers, Core Web Vitals ou suporte nativo não observado.
8. A avaliação QWLeague está `BLOCKED_EXTERNAL`: só home/sitemap públicos
   foram observados; não há contrato oficial verificado de API, OAuth ou
   webhook.

## Baseline do executor

Antes da materialização desta documentação, o checkout estava na branch
`docs/164-post-1-0-master-plan`, rastreando o remoto homônimo, em
`dc080acacba7d70ced0cb311f08a81259bf5a9bd`. Não havia mudanças rastreadas
fora deste escopo; o `git status --short --untracked-files=all` atual não
encontra `quake-world/`, e nenhum arquivo desse caminho foi criado ou
alterado por esta frente. `docs/post-1.0/` não existia antes desta frente. O
`git diff --check` prévio terminou com código `0`.

Essa baseline de execução é distinta da fotografia de auditoria: a primeira
descreve o checkout antes deste diff; a segunda descreve o estado de release
observado no intervalo UTC acima.

## Níveis de evidência

| Nível | Significado |
| --- | --- |
| `E0` | proposta ou ausência de observação |
| `E1` | fonte documental, fixture ou verificação local |
| `E2` | comparação independente de fontes/bytes, sem rebuild |
| `E3` | execução nativa/aceitação do candidato exato no M3 |
| `E4` | rebuild independente demonstrando os mesmos bytes; não observado |

## Referências de operação

- [PROJECT-STATUS](../PROJECT-STATUS.md) — fonte operacional anterior, a ser
  reconciliada com esta fotografia quando houver conflito;
- [runbook de release](../runbooks/release.md);
- [operação TUF](../runbooks/tuf-operation.md);
- [ADR 0008 — owner-only](../adr/0008-owner-only-release-gates.md);
- [release truth machine-readable](release-truth.json);
- [registro de riscos](risk-register.json).
