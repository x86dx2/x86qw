# Issue backlog proposto

Esta lista materializa o backlog local da meta-issue #164. A única issue
remota criada nesta execução é a própria #164; os itens abaixo não foram
abertos, atribuídos ou aprovados remotamente.

## Top 20 priorizado

| ID | Classe | Título | Dependências | Versão/audiência |
| --- | --- | --- | --- | --- |
| POST-001 | NOW/P0 | Determinizar o protocolo de deadline DNS | nenhuma | sem release se test-only / owner-only |
| POST-002 | NOW/P0 | Fechar matriz protegida e restaurar main verde | POST-001 | 1.0.x / owner-only |
| POST-003 | NOW/P1 | Ledger único de release e audiência | POST-002, POST-017 | documental / owner-only |
| POST-004 | NOW/P1 | Receipt final bound ao candidato exato | POST-003 | 1.0.x se bytes/receipt exigirem / owner-only |
| POST-005 | NEXT/P1 | Classificar ownership e SBOM | POST-003 | 1.0.x / owner-only |
| POST-006 | NOW/P1 | Resolver contrato de cleanup e dados pessoais | POST-003 | 1.0.1 somente se runtime mudar / owner-only |
| POST-007 | NEXT/P2 | Confirmar controles de segurança e resposta | POST-020 | 1.0.x / owner-only |
| POST-008 | NEXT/P1 | Resolver convergência e redundância de mirror | POST-003, POST-011 | 1.0.x / owner-only |
| POST-009 | NOW/P1 | Nomear custódia TUF e backup independente | POST-011 | 1.0.x / owner-only |
| POST-010 | NOW/P1 | Executar drill de recuperação TUF e medir RTO | POST-009 | 1.0.x / owner-only |
| POST-011 | NOW/P1 | Formalizar SLO, alerta e lease TUF | nenhuma | 1.0.x / owner-only |
| POST-012 | EP-1/P1 | Migrar baseline publicada real | EP-0, POST-003 | external-public candidato exato |
| POST-013 | EP-2/P1 | Soak consecutivo de sete dias | POST-012 | external-public candidato exato |
| POST-014 | EP-4/P1 | Aceitação por usuário externo | POST-013, POST-010 | external-public candidato exato |
| POST-015 | EP-5/P1 | Decisão de plataforma com evidência nativa | POST-014 | external-public por plataforma |
| POST-016 | 1.3/BLOCKED_EXTERNAL | RFC de adapter QWLeague | contrato oficial | 1.3 / opcional |
| POST-017 | NOW/P1 | Reconciliar source, deployment e conteúdo projetado | POST-002 | documental / owner-only |
| POST-018 | NEXT/P1 | Observação operacional owner-only | POST-002, POST-003, POST-011, POST-020 | 1.0.x / owner-only |
| POST-019 | NEXT/P2 | Lint de drift do release-truth ledger | POST-003, POST-017 | 1.0.x / todas as audiências |
| POST-020 | NOW/P1 | Reconciliar backlog RC e governança | POST-002, POST-003 | documental / owner-only |

## Disposição proposta das issues existentes

Nenhuma alteração remota foi feita nesta execução:

- `#143`: preservar como histórico/superseded e abrir soak para o candidato
  external-public exato;
- `#146`: retarget para migração real `0.7.13` → candidato exato;
- `#148`: manter como operação estrutural, com custódia, owner, backup, SLO
  e RTO;
- `#150`: executar somente depois de preservar referências de evidência;
- `#151`: retarget para futura política de hosting;
- `#152`: manter como incidente/SLO e registrar recuperação, não apenas falha.

## Definition of Ready para abrir uma issue concreta

Antes de criar uma issue remota, o mantenedor deve preencher o registro
correspondente em [backlog.json](backlog.json) e confirmar owner, Checker,
dependências, plataforma, segurança, privacidade, testes, rollback, audiência,
versão e critério de parada. Implementação, publicação e promoção de audiência
devem permanecer em issues/PRs separadas.

POST-008 é deliberadamente bifásica: fecha a convergência operacional para
Gate 0C owner-only e, após EP-2, repete igualdade, fallback e disponibilidade
contra o digest exato do candidato external-public em EP-3. Se os bytes
mudassem, o soak e essa revalidação devem reiniciar.

## Definition of Done comum

- evidência ligada ao commit, run, digest ou endpoint correto;
- acceptance criteria reproduzidos por Checker independente;
- nenhum claim de suporte acima da evidência nativa;
- rollback diagnosticável;
- documentação e release truth atualizados somente por projeção verificável;
- nenhum segredo, cookie, token ou dado pessoal em issue/log;
- P0/P1 resolvido, convertido em gate ou deferido com owner e risco aceito.

## Template obrigatório

Cada issue deve conter: título; problema; contexto; outcome; escopo; não
escopo; dependências; acceptance criteria; hard gates; testes; plataforma;
segurança; privacidade; rollback; docs; Maker; Checker; versão alvo; audiência;
labels sugeridas; estado e evidência.
