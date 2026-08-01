# Estado v2 e execução sem mutação

> Documento histórico da linha 0.2.x. O bootstrap limpo atual usa `.x86qw/`
> e não converte árvores antigas; consulte `clean-bootstrap-layout.md`.

Esta migração separa a preparação da instalação da execução dos runtimes.

## Estado da instalação

O formato 1 de `.install/state.json` registrava perfil, seleção customizada,
componentes observados e o catálogo conhecido. O formato 2 preserva esses
campos e acrescenta:

- `capabilities`: capacidades explicitamente registradas, inicialmente vazias
  para instalações históricas;
- `component_fingerprint`: impressão determinística dos componentes realmente
  registrados.

`install`, `update`, `upgrade` e `repair` fazem a migração unilateral por
gravação temporária seguida de rename. A seleção `custom`, os componentes
instalados e os identificadores históricos reconhecidos são preservados. Uma
simulação de `update` mostra a migração no plano antes de qualquer gravação.

O rollback operacional consiste em restaurar o `state.json` anterior; nenhum
payload ou arquivo pessoal é removido pela conversão do estado. O instalador
continua aceitando o formato 1 durante esta linha de compatibilidade.

Capacidades técnicas de runtimes permanecem no catálogo declarativo e não são
capacidades selecionáveis da instalação. Como nenhum perfil operacional foi
habilitado nesta linha, o único valor aceito em `state.json` é
`"capabilities": []`; identificadores arbitrários são rejeitados.

## Suporte derivado dos jogos

`play-support` formaliza gamecodes e configurações gerenciadas derivados dos
componentes instalados. Ele é preparado por `install`, `update`, `upgrade` ou
`repair`. `verify` detecta ausência ou divergência e orienta uma dessas ações.

`play` e `host` somente validam esse suporte. Eles não criam recibos, não
alteram `.install/components/`, não reparam executáveis e não escrevem em
configurações pessoais. A única materialização permitida durante `host` é o
conteúdo efêmero de PK3 necessário ao MVDSV, protegido pelo journal da sessão e
reconciliado no encerramento ou na próxima execução.

Arquivos pessoais divergentes nunca são sobrescritos durante reparação. Um
arquivo efêmero não sensível alterado durante a sessão também é preservado e
reportado. Configurações efêmeras classificadas como sensíveis são removidas
mesmo quando modificadas, pois podem conter senhas; o journal registra somente
metadados redigidos.

## Dependências operacionais

MVDSV, QTV e QWFWD são componentes instaláveis independentes. A seleção do
jogo determina o componente exigido pelo `host`; QTV aceita upstream remoto sem
MVDSV local; QWFWD pode operar isoladamente. Os perfis históricos continuam
contendo exatamente as escolhas já publicadas.
