## Baseline atual

`main@3bbc7a01faf8d472c5ccbab9233e05e9abadc379` em `origin/main` é a base
canônica pública. O checkpoint `codex/stabilize-1.0@30e9d5b` permanece separado
e serve somente para consulta e extração seletiva.

## Versão pública

`0.7.3` continua sendo a versão `current`. Nenhum bundle público foi alterado
ou reconstruído nesta materialização.

## Próximo marco

PR A — verdade de plataforma, em revisão como [PR #66](https://github.com/x86dx2/x86qw/pull/66).
Depois do merge, a release corretiva `0.7.4` deverá ser aberta em uma PR separada.

## PR ativo

[PR #66](https://github.com/x86dx2/x86qw/pull/66), branch
`codex/pr-a-platform-truth`, baseada em `origin/main@3bbc7a0`.

## Issue ativa

Nenhuma issue independente está registrada como ativa; o work item corrente é a
[PR #66](https://github.com/x86dx2/x86qw/pull/66). A issue de acompanhamento
da fase A ainda precisa ser vinculada antes de declarar o gate encerrado.

## Gates

Base exata em `origin/main`; distinção entre artefato, suporte e validação;
workflows preservados; `git diff --check`; 1.200 testes de manutenção e 5 de
site; `PYTHONDONTWRITEBYTECODE=1 ./maintenance/manage.py verify --no-tests`;
nenhuma evidência nativa Mac M3 nem promoção de suporte foi alegada. A `1.0.0`
não foi preparada, promovida nem publicada.

## Riscos

O checkpoint não é aprovação nem merge; Linux, Windows, macOS Intel e nightly
começam como `preview`; o stable macOS pode continuar `conditional` por
assinatura/notarização; trust de produção, evidência M3, mirrors e período de
uso ainda não existem como gates concluídos.

## Próxima ação

Obter revisão independente da PR A; após o merge, abrir a PR de release `0.7.4`
separada da implementação antes de desbloquear a PR B.
