# Visão de longo prazo do ecossistema QuakeWorld

Este documento descreve para onde o ecossistema pode evoluir depois da
estabilização da distribuição. Ele não é uma segunda autoridade de status: a
sequência até `1.0.0` está em [ROADMAP.md](ROADMAP.md), o estado presente em
[PROJECT-STATUS.md](PROJECT-STATUS.md) e os gates executáveis no
[plano de estabilização](implementation/stabilization-1.0-plan.md).

## Princípios de evolução

- preservar bytes upstream e customizações x86QW em camadas identificáveis;
- tratar catálogo, contrato, artefato, suporte e validação como dimensões
  diferentes;
- manter uma instalação verificável, com lifecycle reversível e sem mutação
  durante execução;
- exigir origem, licença, hash, ownership, migração e evidência de plataforma
  para cada expansão;
- manter o fluxo operacional local **sem gates nativos** quando a tarefa só
  exigir contratos portáveis; a ausência de smoke nativo deve continuar
  explícita, nunca implícita.

## Distribuição e runtime

A arquitetura desejada é uma fonte canônica de inventários e contratos que
projeta catálogos, bundles e documentação sem duplicar fatos. O runtime deve
continuar independente de `maintenance`, `dist` e fachadas de entrada, com
fronteiras únicas para downloads, arquivos, estado, receipts, migrações,
transações, UI, plataforma e supervisor.

As próximas melhorias arquiteturais devem privilegiar:

- contratos versionados e envelopes JSON estáveis;
- migração unilateral de estados publicados, com ownership de arquivos pessoais
  preservado;
- candidatos construídos uma vez, com checksums, SBOM, provenance e mirrors
  convergentes;
- trust metadata com custódia, rotação e expiração aprovadas;
- suporte por plataforma declarado somente conforme a evidência do candidato.

## Experiência QuakeWorld

O ecossistema deve continuar oferecendo uma instalação coerente para os jogos e
modos já catalogados, sem transformar um novo modo em motivo para misturar
conteúdo upstream, runtime ou serviço. A evolução pode explorar:

- seleção declarativa de jogos, modos, mapas e perfis;
- treinamento e partidas assistidas como contratos explícitos e opt-in;
- ferramentas de análise de demos e validação formal de MVD/QWD;
- compatibilidade de clientes, servidores e relays sem confundir catálogo com
  execução nativa;
- documentação operacional que mantenha comandos, defaults e reversão
  compreensíveis.

Qualquer mudança de gameplay, KTX, Frogbot, host, supervisor ou downloader deve
ter issue própria. Ela não pode ser extraída incidentalmente do checkpoint nem
entrar em uma PR de release.

## Serviços e rede

MVDSV, QTV e QWFWD devem permanecer componentes independentes, seguros por
default e compostos somente quando o operador solicitar. A visão futura inclui:

- readiness e preflight formais para cada serviço;
- testes isolados de protocolo, geração de MVD e encaminhamento UDP;
- exposição externa sempre explícita, com credenciais fora da linha de comando;
- recuperação journalizada e ownership claro entre launcher, guardião e filhos;
- serviços persistentes do sistema somente após uma proposta de lifecycle,
  instalação, atualização, desinstalação e rollback.

## Plataforma e publicação

A compatibilidade de Linux, Windows, macOS Intel e nightly deve continuar
visível no catálogo sem ser promovida a suporte por inferência. A evolução de
plataforma exige harness não bloqueante para contratos portáveis e evidência
nativa do candidato exato quando o estado mudar para `supported` ou
`conditional`.

O pipeline de publicação deve manter preparação, trust, evidência, promoção e
metadata como etapas separadas. Ações, fontes e dependências precisam ser
fixadas por identidade imutável; nenhum mirror divergente pode ser aceito por
fallback silencioso.

## Backlog pós-1.0

Os itens abaixo são intenções de longo prazo, não trabalho corrente:

- central de demos, análise de MVD/QWD e treinamento como comando próprio;
- novos clientes, engines, jogos, mods e mapas externos;
- perfis operacionais adicionais sobre capacidades instaladas;
- serviços persistentes do sistema e integração de observabilidade;
- ferramentas visuais de arquitetura, mapas do ecossistema e documentação
  navegável;
- sincronização ou uso do mirror GitLab somente depois de decidir sua função e
  contrato de autoridade.

Cada item precisa de uma proposta separada com issue, contrato, artefato,
licença, migração, testes, validação de plataforma, gates de segurança e PR de
release própria. O ecossistema não deve crescer como efeito colateral de
`play`, `host`, `update` ou `upgrade`.
