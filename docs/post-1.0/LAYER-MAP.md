# Mapa de camadas e autoridades

O mapa separa o que é fonte, o que é bytes, o que é trust e o que é claim de
suporte. Uma camada inferior não pode ampliar a autoridade da superior.

```text
fonte/commit canônico
        │
        ▼
candidato build-once ──► receipt/SBOM/provenance
        │                         │
        ▼                         ▼
assets GitHub/GitLab (E2)   evidência M3 (E3)
        │                         │
        └──────────┬──────────────┘
                   ▼
        catálogo e cadeia TUF (root v2, roles v18)
                   │
                   ▼
       installer/update endpoint público
                   │
       ┌───────────┴───────────┐
       ▼                       ▼
  plataforma/suporte       audiência/governança
       │                       │
       ▼                       ▼
 preview/conditional       owner-only/external-public
```

A indicação histórica de root v2 no diagrama foi corrigida pela
[errata TUF](ERRATA-TUF-ROOT-VERSION.md); os bytes publicados eram root v1.

## Contratos por camada

| Camada | Autoridade | Evidência mínima | Limite |
| --- | --- | --- | --- |
| fonte | `origin/main` e commit de produto | SHA e revisão | não prova bytes publicados |
| candidato | candidate + installer | digest, tamanho, run | sem E4 não há rebuild independente |
| mirrors | GitHub/GitLab | E2 byte equality | não prova disponibilidade contínua |
| native | M3 | E3 `25/25` candidato exato | não promove não-M3 |
| TUF | root incorporada + metadata assinada | versões, hashes, expiry | técnico saudável não prova custódia |
| installer | endpoint/catálogo | aceitação com escopo explícito | não define audiência |
| suporte | matriz por plataforma | E3 específico | CI portátil é insuficiente |
| audiência | decisão do mantenedor + gates | receipt e EP-0 | publicação não autoriza external |
| ecossistema | contrato oficial do provedor | API/OAuth/webhook verificável | QWLeague está BLOCKED_EXTERNAL |

## Fluxo de autoridade

`source → candidate → mirrors → TUF → acceptance → audience` é uma cadeia
ordenada. Se um receipt apontar para RC1 enquanto a release é final, o fluxo
para em `acceptance`; não se “corrige” por copiar um hash em um documento.

## Responsabilidades

- **Maker** produz o artefato na camada autorizada;
- **Checker** verifica a camada e seus consumidores;
- **Release owner** decide se o gate pode avançar;
- **Custodian** mantém trust e recovery fora do checkout;
- **Ecosystem owner** só registra integração após contrato oficial.

## Workstreams L00–L27

| ID | Camada | Resultado desta materialização | Gate/versão | Checker |
| --- | --- | --- | --- | --- |
| L00 | release truth, audiência e baseline | quatro autoridades separadas; drift bloqueado | 0A–0C | Release Truth |
| L01 | produto, posicionamento e personas | jornadas e suporte ainda a definir | após 0C | Product Critic |
| L02 | governança e sustentabilidade | single-maintainer/self-review escalado | 0D | Governance |
| L03 | arquitetura e fronteiras | runtime é fronteira; hotspots mapeados | ADR posterior | Architecture |
| L04 | contratos públicos/core JSON | audience/support ausentes nos consumidores | 0C; 1.1 | Contract |
| L05 | distribuição e composição | catálogo/licenças existem; matriz única pendente | 0D | Distribution |
| L06 | proveniência, licença e marca | ownership/SBOM 87/87 sem classificação | 0C | Licensing |
| L07 | bootstrap/instalação/first run | claims e endpoint precisam reconciliar | 0C | Fresh Machine |
| L08 | download/cache/mirrors/TUF | DNS determinístico e main verdes; custódia/recovery e drift de deployment bloqueiam | 0B–0C | Network/TUF |
| L09 | lifecycle da instalação | migração/preservação externa pendentes | EP-1 | Data Preservation |
| L10 | CLI/TUI | contrato público preservado; tarefas a mapear | 1.1 | Terminal UX |
| L11 | UI local | somente plano read-only, sem servidor nesta fase | 1.1/ADR | Local Web Security |
| L12 | perfis/configuração/identidade local | fronteira profile/cache/personal, backup/restore e library local | 1.1 | User Data |
| L13 | gameplay launch | sem mudança de engine; aceitação posterior | 1.1 | Veteran Player |
| L14 | descoberta/entrada em partidas | hub com fallback para library local; QWLeague não é autoridade | 1.1 | New Player/Network |
| L15 | integrações externas | QWLeague `BLOCKED_EXTERNAL` | 1.3/parceria | External Integration |
| L16 | hospedagem local | presets sem senhas; status/stop/readiness/journal | 1.2 | Server Operator |
| L17 | operação remota/fleet | agentless/SSH somente após ADR | 1.2/ADR | Remote Ops Security |
| L18 | observabilidade/doctor | `doctor` read-only e bundle sanitizado | 1.1 | Supportability |
| L19 | demos/MVD/QWD | parsing seguro e fuzzing ainda não priorizados | 1.3 | Untrusted File |
| L20 | site/documentação/status | source e deployment em drift | 0C | Web/Documentation |
| L21 | plataformas/empacotamento | somente M3 tem E3 do candidato | EP-5 | Native Platform |
| L22 | segurança/privacidade/abuso | settings remotos exigem evidência autenticada | 0D | Adversarial Security |
| L23 | supply chain/release engineering | build-once forte; receipt/SBOM gaps | 0B/0C | Supply Chain |
| L24 | qualidade/determinismo/testes | matriz protegida verde; zero-flake e regressões temporais mantidos | 0A | Test Strategy |
| L25 | performance/confiabilidade | baseline de medição pendente | após 0C | Performance |
| L26 | acessibilidade/i18n | pt-BR/en e gates futuros | 1.1 | Accessibility/I18n |
| L27 | comunidade/sustentabilidade | suporte, bus factor e carga pendentes | 0D/EP-0 | Community |

## Referências

- [release truth](RELEASE-TRUTH.md);
- [dependency graph](DEPENDENCY-GRAPH.md);
- [release train](RELEASE-TRAIN.md);
- [runbook de release](../runbooks/release.md).
