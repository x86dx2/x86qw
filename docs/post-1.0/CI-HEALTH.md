# CI health e Gate 0A

**Estado corrente:** `MAIN=GREEN`; Gate 0A permanece fechado. O Validate da
linha `main` passou no run
[33124929611](https://github.com/x86dx2/x86qw/actions/runs/33124929611),
no commit `adf83f9f01a8601ed22676525d215b2d00f86592`, com 5/5 jobs exigidos
verdes; o job de preview foi omitido sem ser usado como suporte.

## Diagnóstico fechado

O run histórico 31853649373 falhou somente em
portable-contract / Windows / Python 3.10. A correção foi restrita a
maintenance/tests/test_downloader.py: os dois ramos temporais usam relógio
controlado, o protocolo do processo fake é explícito e os cenários de kill,
reap limitado, detach e timeout são regressões determinísticas.

Verificação do Checker:

- módulo downloader: 87 testes OK (1 skip);
- 30.000 execuções repetidas dos três casos novos: zero falha;
- casos adversariais de kill, wait, TimeoutExpired e output parcial
  preservaram o orçamento e o erro esperado;
- manage.py verify --no-tests e git diff --check: OK;
- Windows/Python 3.10 e 3.13 e a matriz protegida: verdes no run
  33124929611.

A classificação é **test-only**. Não há evidência de mudança necessária no
runtime/package nem motivo para 1.0.1 por este finding. Se uma futura
reprodução de produto alterar bytes/comportamento, a decisão deverá ser
reaberta com candidato, receipt e observação novos.
