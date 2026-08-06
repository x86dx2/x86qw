# Migração de estado para 1.0

Este documento registra o contrato de migração que pertence ao runtime. Ele
não publica uma versão nem autoriza a alteração de conteúdo do jogo.

## Caminho suportado

O destino canônico é `1.0.0`. A fronteira aceita a família `0.7.x`; os
fixtures `0.7.0`, `0.7.1`, `0.7.2` e `0.7.3` são respaldados pelos ZIPs
exatos das releases públicas correspondentes e trazem a origem da tag, do
commit, do arquivo, do tamanho e do SHA-256 no `manifest.json`. Cada fixture
mantém o arquivo ZIP e uma árvore `bundle/` extraída pela fronteira canônica
de arquivos. O layout legado de metadata continua determinístico e não inclui
PAKs, runtime instalado ou dados pessoais reais; os smokes escrevem bytes
temporários de payload somente para comprovar preservação.

Não existe release pública x86QW `0.8.x` ou `0.9.x` neste repositório. Esses
diretórios aparecem somente como fixtures contratuais `prospective`, marcados
`synthetic-contract-only`, e o plano os bloqueia com `prospective-source`.
Assim que uma dessas linhas existir, o manifesto e o fixture da tag precisam
ser adicionados antes de habilitar a migração; uma árvore criada manualmente
não é evidência de release. A decisão de publicar releases reais 0.8/0.9 ou
reduzir formalmente o aceite para as versões publicadas é uma aprovação de
produto, não uma inferência deste código.

Cada manifesto público declara `source_tag`, `source_commit`, `source_path`,
`source_version`, `payload_kind`, `layout` e `source_archive` com `url`,
`path`, `size`, `sha256` e `extracted_path`. O teste compara o arquivo local
com o digest fechado e valida cada membro materializado contra o `ArchivePlan`;
nenhuma rede é necessária durante a suíte normal. Os manifestos prospectivos declaram
a família, `payload_kind: synthetic-contract-only`,
`public_release_available: false` e o motivo do bloqueio. O teste de
fixture compara os campos públicos com a tag e o `VERSION` reais do checkout.

## Estado e recibos

`x86qw_runtime.state` continua aceitando os formatos persistidos 1 e 2. A
leitura é pura e limitada; um plano de migração normaliza a semântica de ambos
os formatos, incluindo IDs legados, fingerprint e aliases históricos. O marcador opcional
`installation_version` identifica a versão do contrato sem invalidar estados
históricos que não o possuíam. Booleanos não são aceitos como números de
formato.

`x86qw_runtime.receipts` mantém os codecs históricos e adiciona
`inspect_receipt`, `receipt_sha256` e `validate_receipt_inventory`. Um arquivo
é considerado gerenciado apenas quando o recibo, o inventário, a identidade e
o hash exato do par são válidos. Nome de arquivo isolado nunca prova ownership.

## Plano e execução

`plan_migration(root, source_version=..., target_version="1.0.0")` não escreve.
O argumento opcional `source_version` só serve para fixtures sem metadados
históricos; quando existe um recibo CLI ou marcador de estado válido, ele é
comparado e qualquer divergência bloqueia o plano. Um marcador de estado
`1.0.x` continua aceitando o argumento antigo para que o replanejamento
idempotente de uma instalação já migrada não fique bloqueado.
O resultado é um `MigrationPlan` com operações, preservações, snapshot e
conflitos. `MigrationPhase` expõe as fases `preflight`, `stage`, `verify`,
`commit` e `finalize`. `migrate_installation(..., dry_run=True)` é o atalho para
obter o mesmo plano.

Na execução, os bytes são copiados para staging privado, verificados pelo
tamanho e SHA-256, promovidos por escrita atômica sem substituir um destino
desconhecido e só depois os nomes legados são recolhidos. Falha ou crash em
qualquer fase levanta `MigrationExecutionError` e restaura os bytes anteriores
quando possível. O `MigrationResult` retém a inversa até a verificação final e
oferece `rollback()`. Um destino com bytes coincidentes, mas sem um journal de
ownership pendente, continua sendo colisão: bytes iguais não autenticam uma
execução anterior. A execução não-dry-run recupera o journal pendente sob o
lock exclusivo antes de reconstruir o plano.

Commit e limpeza são fronteiras distintas: depois que os destinos foram
promovidos e os nomes legados finalizados, o journal persiste
`cleanup-pending`. Uma falha ao remover a árvore privada não reverte bytes já
commitados nem alega `rolled_back`; a pendência permanece visível para uma
retomada que só remove staging/backup autenticados. Troca da árvore por
symlink, diretório ou bytes de outra origem bloqueia essa limpeza e preserva o
material para inspeção. A retomada usa uma allowlist fechada derivada do
journal (`journal.json`, os backups declarados e os stages numerados), confere
hash/tamanho e captura as identidades das folhas e dos diretórios antes de
remover. Arquivos, diretórios, symlinks ou paths extras nunca são descobertos
recursivamente nem apagados; uma troca de identidade entre a validação e a
remoção falha fechado. Se uma falha deixar parte da árvore removida, o journal
continua sendo aceito para que a próxima tentativa apenas finalize os objetos
autenticados que ainda existirem.

O layout legado de recibos de componentes (`.x86qw/<id>.receipt` e
`.inventory`), da CLI (`.x86qw/cli.receipt`) e dos clientes ezQuake
(`.x86qw/ezquake-<plataforma>-<canal>.receipt`) é convertido para os caminhos
contextuais. Stable e nightly são operações independentes. O par legado
`nquake-ktx` é normalizado para `ktx`: a identidade interna do recibo, o
caminho de inventário `qw/ktx.pk3`, o hash de binding e o destino contextual
precisam concordar. Um par `nquake-ktx` e `ktx` só é deduplicado quando os bytes
normalizados são iguais; divergência bloqueia antes da primeira escrita. O par
agregado legado `.x86qw/nquake.receipt` + `.x86qw/nquake.inventory` não é
movido, mas é validado (hash, formato, ownership e caminhos portáveis) antes
de ser preservado; par parcial ou corrompido bloqueia. O componente aposentado
`nquake-sounds` é retirado do estado ativo, mas seu par validado permanece no
caminho legado para diagnóstico e disposição explícita;
`MigrationPlan.retired_components` registra essa condição.

## Preservação e bloqueios

O plano nunca toca PAKs, runtimes, configurações pessoais, demos, logs ou
arquivos sem ownership comprovado. Uma colisão pessoal em qualquer destino
gerenciado, symlink/reparse point (inclusive a raiz canônica
`.x86qw/components`), par de metadados parcial, estado corrompido, hash
divergente ou alteração concorrente bloqueia o plano antes da primeira escrita.
Não há inferência por nome, remoção automática, downgrade ou limpeza de dados
pessoais.

## Validação executada

O contrato é exercitado por `maintenance/tests/test_migration_1_0.py`, incluindo
fixtures públicos byte a byte, stable/nightly coexistentes, perfil customizado, dry-run sem
escrita, colisão pessoal, symlink, recibo corrompido, disco cheio, crash em
cada fase, rollback byte a byte, pares legados normalizados, colisão de bytes
idênticos sem journal, recuperação de crash pela CLI e reexecução idempotente.

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest maintenance.tests.test_migration_1_0
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest maintenance.tests.test_migration_manager
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest maintenance.tests.test_runtime_state maintenance.tests.test_runtime_receipts
```

O comando CLI já adquire o lock de manutenção antes de recuperar journals e
reconstruir o plano em uma execução real; `--dry-run` continua sem lock e sem
recuperação. Smokes nativos e fixtures reais de `0.8.x`/`0.9.x` permanecem
gates separados; não são alegados por este contrato.
