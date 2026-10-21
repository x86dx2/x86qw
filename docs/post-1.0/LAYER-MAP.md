# Map of layers and authorities

The map separates what is source, what is bytes, what is trust and what is a
support claim. A lower layer cannot widen the authority of the one above it.

```text
canonical source/commit
        │
        ▼
build-once candidate ──► receipt/SBOM/provenance
        │                         │
        ▼                         ▼
GitHub/GitLab assets (E2)   M3 evidence (E3)
        │                         │
        └──────────┬──────────────┘
                   ▼
        catalog and TUF chain (root v2, roles v18)
                   │
                   ▼
       public installer/update endpoint
                   │
       ┌───────────┴───────────┐
       ▼                       ▼
  platform/support         audience/governance
       │                       │
       ▼                       ▼
 preview/conditional       owner-only/external-public
```

The historical mention of root v2 in the diagram was corrected by the
[TUF erratum](ERRATA-TUF-ROOT-VERSION.md); the published bytes were root v1.

## Contracts per layer

| Layer | Authority | Minimum evidence | Limit |
| --- | --- | --- | --- |
| source | `origin/main` and the product commit | SHA and review | does not prove the published bytes |
| candidate | candidate + installer | digest, size, run | without E4 there is no independent rebuild |
| mirrors | GitHub/GitLab | E2 byte equality | does not prove continuous availability |
| native | M3 | E3 `25/25` of the exact candidate | does not promote non-M3 |
| TUF | embedded root + signed metadata | versions, hashes, expiry | technically healthy does not prove custody |
| installer | endpoint/catalog | acceptance with an explicit scope | does not define the audience |
| support | per-platform matrix | specific E3 | portable CI is not enough |
| audience | maintainer decision + gates | receipt and EP-0 | publication does not authorize external |
| ecosystem | the provider's official contract | verifiable API/OAuth/webhook | QWLeague is BLOCKED_EXTERNAL |

## Flow of authority

`source → candidate → mirrors → TUF → acceptance → audience` is an ordered
chain. If a receipt points at RC1 while the release is final, the flow stops at
`acceptance`; it is not “fixed” by copying a hash into a document.

## Responsibilities

- **Maker** produces the artifact in the authorized layer;
- **Checker** verifies the layer and its consumers;
- **Release owner** decides whether the gate may advance;
- **Custodian** keeps trust and recovery outside the checkout;
- **Ecosystem owner** records an integration only after an official contract.

## Workstreams L00–L27

| ID | Layer | Result of this materialization | Gate/version | Checker |
| --- | --- | --- | --- | --- |
| L00 | release truth, audience and baseline | four separate authorities; drift blocked | 0A–0C | Release Truth |
| L01 | product, positioning and personas | journeys and support still to be defined | after 0C | Product Critic |
| L02 | governance and sustainability | single-maintainer/self-review escalated | 0D | Governance |
| L03 | architecture and boundaries | the runtime is a boundary; hotspots mapped | later ADR | Architecture |
| L04 | public contracts/core JSON | audience/support missing in the consumers | 0C; 1.1 | Contract |
| L05 | distribution and composition | catalog/licenses exist; single matrix pending | 0D | Distribution |
| L06 | provenance, license and brand | ownership/SBOM 87/87 unclassified | 0C | Licensing |
| L07 | bootstrap/installation/first run | claims and endpoint must be reconciled | 0C | Fresh Machine |
| L08 | download/cache/mirrors/TUF | deterministic DNS and main green; custody/recovery and deployment drift block | 0B–0C | Network/TUF |
| L09 | installation lifecycle | external migration/preservation pending | EP-1 | Data Preservation |
| L10 | CLI/TUI | public contract preserved; tasks to be mapped | 1.1 | Terminal UX |
| L11 | local UI | read-only plan only, no server in this phase | 1.1/ADR | Local Web Security |
| L12 | profiles/configuration/local identity | profile/cache/personal boundary, backup/restore and local library | 1.1 | User Data |
| L13 | gameplay launch | no engine change; later acceptance | 1.1 | Veteran Player |
| L14 | match discovery/joining | hub with a fallback to the local library; QWLeague is not an authority | 1.1 | New Player/Network |
| L15 | external integrations | QWLeague `BLOCKED_EXTERNAL` | 1.3/partnership | External Integration |
| L16 | local hosting | presets without passwords; status/stop/readiness/journal | 1.2 | Server Operator |
| L17 | remote operation/fleet | agentless/SSH only after an ADR | 1.2/ADR | Remote Ops Security |
| L18 | observability/doctor | read-only `doctor` and sanitized bundle | 1.1 | Supportability |
| L19 | demos/MVD/QWD | safe parsing and fuzzing not yet prioritized | 1.3 | Untrusted File |
| L20 | site/documentation/status | source and deployment drifting | 0C | Web/Documentation |
| L21 | platforms/packaging | only the M3 has E3 of the candidate | EP-5 | Native Platform |
| L22 | security/privacy/abuse | remote settings need authenticated evidence | 0D | Adversarial Security |
| L23 | supply chain/release engineering | strong build-once; receipt/SBOM gaps | 0B/0C | Supply Chain |
| L24 | quality/determinism/tests | protected matrix green; zero-flake and time regressions kept | 0A | Test Strategy |
| L25 | performance/reliability | measurement baseline pending | after 0C | Performance |
| L26 | accessibility/i18n | pt-BR/en and future gates | 1.1 | Accessibility/I18n |
| L27 | community/sustainability | support, bus factor and load pending | 0D/EP-0 | Community |

## References

- [release truth](RELEASE-TRUTH.md);
- [dependency graph](DEPENDENCY-GRAPH.md);
- [release train](RELEASE-TRAIN.md);
- [release runbook](../runbooks/release.md).
