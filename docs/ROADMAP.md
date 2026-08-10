# Roadmap do x86QW

Este é o índice estratégico da jornada da release pública `0.7.5` até
`1.0.0`. O estado factual está em
[PROJECT-STATUS.md](PROJECT-STATUS.md); os contratos, dependências e gates
detalhados estão em
[implementation/stabilization-1.0-plan.md](implementation/stabilization-1.0-plan.md).
Nenhum documento autoriza publicação por si só.

## Autoridade e baseline

O `main` remoto inclui atualmente o merge da PR #74,
`adf6f158b24a3f576884013a2a12b20cafeb94c0`. A release `0.7.4` permanece
pública até o corte do hotfix `0.7.5`; seus bundles e o histórico `0.7.3`
permanecem imutáveis.

O candidato local `1.0.0` foi preparado e verificado, mas não é uma release
nem um RC publicado. A seleção de plataforma e a execução nativa devem
continuar separadas da matriz `portable-contract`.

## Estado das frentes

1. **A — verdade de plataforma:** PR #66 mesclada; a release corretiva
   `0.7.4` foi publicada separadamente pela PR #71.
2. **B — governança:** PR #55 mesclada; os runbooks e a fronteira de aprovação
   continuam vigentes.
3. **C — contratos:** PR #53 mesclada; SemVer, schemas, JSON e receipts estão
   congelados para a linha 1.0.
4. **D — migração:** PR #46 mesclada; somente estados publicados são fontes de
   migração.
5. **E1/E2 — trust:** PR #48 registrou a arquitetura e PR #69 materializou as
   dependências/runtime fail-closed. O hotfix `0.7.5` acrescenta a root e a
   cadeia produtiva; publicação e verificação pública permanecem como corte.
6. **F — candidato imutável:** PR #51 mesclada; preparação e promoção continuam
   separadas e falham fechado sem evidência.
7. **G — Mac M3/arm64:** PR #54 mesclada; PR #73 corrigiu a detecção do Apple
   M3 e PR #74 corrigiu o handoff determinístico. A evidência M3 do candidato
   exato ainda não foi produzida.
8. **RC e H:** `1.0.0-rc.1` ainda não foi publicada nem passou por período de
   uso. H não deve ser aberta antes de trust, evidência M3, RC e mirrors
   convergentes.

## Gates restantes

- publicar e verificar root, targets, snapshot e timestamp TUF assinados, sem
  transportar chaves privadas para Git, CI ou bundle;
- construir o RC a partir do candidato exato, preservar seus hashes e iniciar
  o período de uso sem alegar `1.0.0`;
- executar o lifecycle nativo no host M3 e produzir `release-evidence.json`
  autenticado para os mesmos bytes;
- validar cada mirror sem overwrite, conferir convergência e promover
  metadata por último;
- abrir H somente com aprovação humana explícita e documentação coerente.

O waiver solo-maintainer do ADR 0007 registra uma exceção de governança; não
deve ser descrito como revisão criptográfica independente nem reduz os gates
de custódia, metadata, evidência, RC e mirrors.

## Próxima sequência

1. Concluir a cerimônia E2 e preservar a ata/fingerprints sem segredos.
2. Publicar a cadeia TUF e incorporar somente a root de produção aprovada no
   candidato correspondente.
3. Preparar e testar `1.0.0-rc.1`; iniciar e observar o período de uso.
4. Reexecutar H no Mac M3, verificar bytes, evidência e mirrors.
5. Abrir, revisar e promover H para `1.0.0`; publicar metadata por último.

Até que essa sequência seja comprovada, o catálogo legado pode continuar
servindo o portal, mas não é uma autoridade de atualização autenticada da CLI.

## Depois de 1.0

Central de demos, validação formal de MVD/QWD, treinamento como comando,
clientes ou engines novos, mods/mapas externos, serviços persistentes do
sistema e perfis operacionais adicionais exigem propostas próprias, artefatos,
contratos, migração, testes, evidência de plataforma e aprovação de release.
Eles não entram como efeito colateral de `play`, `host`, `update` ou
`upgrade`.

Para a visão de longo prazo do ecossistema, consulte
[ROADMAP-QUAKE-ECOSYSTEM.md](ROADMAP-QUAKE-ECOSYSTEM.md).
