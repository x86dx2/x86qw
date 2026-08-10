# Roadmap do x86QW

Este é o índice estratégico da jornada da release pública `0.7.6` até
`1.0.0`. O estado factual está em
[PROJECT-STATUS.md](PROJECT-STATUS.md); os contratos, dependências e gates
detalhados estão em
[implementation/stabilization-1.0-plan.md](implementation/stabilization-1.0-plan.md).
Nenhum documento autoriza publicação por si só.

## Autoridade e baseline

O `main` remoto inclui o hotfix 0.7.6 da PR #78,
`a9680e9cc5d0eb728d3d84203d0966f0d1167592`. A release `0.7.6` é pública;
os bundles 0.7.5, 0.7.4 e 0.7.3 permanecem históricos e imutáveis.

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
5. **E1/E2 — trust:** PR #48 registrou a arquitetura, PR #69 materializou as
   dependências/runtime fail-closed e PRs #77/#78 publicaram e verificaram a
   root e a cadeia produtiva. O 0.7.6 corrigiu o fim da rotação por HTTP 404.
6. **F — candidato imutável:** PR #51 mesclada; preparação e promoção continuam
   separadas e falham fechado sem evidência.
7. **G — Mac M3/arm64:** PR #54 mesclada; PR #73 corrigiu a detecção do Apple
   M3 e PR #74 corrigiu o handoff determinístico. A evidência M3 do candidato
   exato ainda não foi produzida.
8. **RC e H:** `1.0.0-rc.1` ainda não foi publicada nem passou por período de
   uso. H não deve ser aberta antes de trust, evidência M3, RC e mirrors
   convergentes.

## Gates restantes

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

1. Preparar um candidato novo a partir do baseline 0.7.6 e congelar seus hashes.
2. Publicar e testar `1.0.0-rc.1`; iniciar e observar o período de uso.
3. Reexecutar H no Mac M3, verificar bytes, evidência e mirrors.
4. Abrir, revisar e promover H para `1.0.0`; publicar metadata por último.

O catálogo TUF 0.7.6 é a autoridade autenticada da CLI. Ele não antecipa nem
substitui os gates próprios dos futuros bytes de RC e 1.0.

## Depois de 1.0

Central de demos, validação formal de MVD/QWD, treinamento como comando,
clientes ou engines novos, mods/mapas externos, serviços persistentes do
sistema e perfis operacionais adicionais exigem propostas próprias, artefatos,
contratos, migração, testes, evidência de plataforma e aprovação de release.
Eles não entram como efeito colateral de `play`, `host`, `update` ou
`upgrade`.

Para a visão de longo prazo do ecossistema, consulte
[ROADMAP-QUAKE-ECOSYSTEM.md](ROADMAP-QUAKE-ECOSYSTEM.md).
