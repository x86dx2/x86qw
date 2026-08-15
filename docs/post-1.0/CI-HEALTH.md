# CI health e Gate 0A

**Estado:** `RED`; o Gate 0A não está fechado.

## Observação

O run principal `31853649373` falhou somente em
`portable-contract / Windows / Python 3.10`. Linux, macOS e os demais jobs
registrados no snapshot não são a causa deste bloqueio. O diagnóstico de que o
fake não aceita os argumentos usados pelo contrato e de que há um atraso não
controlado de 10 ms é **INFERENCE test-only**; precisa de reprodução com relógio
determinístico antes de qualquer mudança de produto.

Não há autorização para abrir `1.0.1` apenas para documentação/teste. Uma
correção de produto só poderia receber versão corretiva depois de um problema
de bytes/comportamento comprovado e de um candidato novo.

## Gate 0A

Para fechar o gate, o Checker precisa anexar:

- run verde no commit canônico escolhido;
- Windows Python 3.10 e 3.13, além da matriz portátil existente;
- fake com protocolo/argumentos explicitamente testados;
- relógio controlado ou espera virtual, sem `sleep` arbitrário;
- teste de timeout, cancelamento e cleanup;
- diff mínimo restrito ao contrato/testes caso a inferência seja confirmada.

O run vermelho permanece uma limitação pré-existente desta entrega; este
documento não modifica `.github/workflows` nem testes.

## Camadas de validação

| Camada | Prova | Não prova |
| --- | --- | --- |
| unit/contract | protocolo, schemas e relógio | runner Windows real se não executado |
| portable matrix | compatibilidade dos contratos | smoke nativo, rede pública ou Gatekeeper |
| M3 E3 | candidato exato em Apple M3 (`25/25`) | suporte de outras plataformas |
| public acceptance | endpoint, catálogo e lifecycle do escopo | operação TUF sustentável |
| rebuild E4 | bytes reproduzíveis de fonte independente | audiência autorizada |

## Não regressão

Os testes de CI devem continuar separando artefato, suporte e validação. Um job
portátil não pode declarar execução gráfica, rede pública ou performance do
site. A matriz de plataforma deve conservar `preview` até haver evidência
nativa específica.

## Referências

- [baseline](AUDIT-BASELINE.md);
- [master plan](MASTER-PLAN.md);
- [harness M3](../implementation/macos-m3-native-harness.md);
- [runbook de release](../runbooks/release.md);
- [backlog](ISSUE-BACKLOG.md), itens `POST-001` e `POST-002`.
