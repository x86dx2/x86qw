# Componentes e atualização

O x86QW separa **conteúdo instalável** de **origem versionada**. Nem toda pasta
do nQuake corresponde a um projeto com releases: skins, miras, skyboxes, mapas e
outros recursos são coleções curadas. Nesses casos, a versão correta é o commit
imutável do `nQuake/distfiles`, não um número inventado.

Estado verificado em 31 de julho de 2026:

## Layout dos mods versionados

Cada mod externo usa a mesma estrutura sob `dist/mods/<mod>/<versao>/`:

- `upstream/`: pacotes binários oficiais consumidos pelo runtime;
- `source/`: código-fonte ou distribuição original que contém as fontes;
- `x86qw/`: configurações, perfis e overlays mantidos pelo x86QW.

As pastas são criadas somente quando há conteúdo correspondente. Por isso o
Pro-X, cujo código-fonte público não foi localizado, possui `upstream/` e
`x86qw/`, mas não uma pasta `source/` vazia. `dist/mods/x86qw/` é reservado aos
componentes autorais da própria distribuição e não representa um upstream.

## Layout dos serviços versionados

Os serviços usam o mesmo contrato de proveniência sob
`dist/services/<serviço>/<versão>/`. A forma da árvore registra a origem de cada
artefato, e não precisa ser visualmente idêntica entre componentes:

```text
<serviço>/<versão>/
├── source/                         # fonte upstream preservada
├── upstream/<plataforma>/          # binário oficial, quando existir
└── x86qw/
    ├── runtime/<plataforma>/       # binário compilado pelo x86QW
    ├── BUILD.json                  # origem, ferramenta e hash de cada runtime
    └── *.cfg                       # configuração mantida pelo x86QW
```

- `source/` contém o código-fonte ou arquivo-fonte original fixado por hash;
- `upstream/` contém somente binários publicados pelo projeto de origem e
  preservados sem alteração;
- `x86qw/runtime/` contém somente binários produzidos pelo x86QW a partir da
  fonte registrada;
- `x86qw/BUILD.json` declara, por plataforma, se o runtime é oficial ou foi
  reproduzido pelo projeto, além de registrar ferramenta, comando e checksum
  quando aplicável;
- `x86qw/*.cfg` contém a configuração operacional do x86QW e nunca é
  apresentada como arquivo do upstream.

Diretórios vazios não são criados. QTV não possui builds oficiais por
plataforma na revisão fixada: Linux, Windows e macOS são builds reproduzidos e,
por isso, ficam todos em `x86qw/runtime/`; não existe `upstream/`. QWFWD 1.30
fornece binários oficiais para Linux e Windows, preservados em `upstream/`,
enquanto o runtime macOS arm64 é produzido pelo x86QW e fica em
`x86qw/runtime/`.

A pasta de versão também preserva a identidade imutável disponível no projeto
de origem: QWFWD usa a tag `1.30`; QTV, que não possui release numerada, usa a
abreviação `025ca949aca0` do commit completo registrado no inventário e no
`BUILD.json`. Portanto, a diferença observada entre essas duas árvores é
intencional e auditável, não um segundo padrão de organização.

| Componente | Versão oferecida | Estratégia | Estado |
| --- | --- | --- | --- |
| Configuração base | `e4cb23d40aa2+x86qw.2` | snapshot nQuake com bootstrap x86QW | aliases temporários sob demanda e textura máxima coerente com OpenGL 4.1 do Apple Silicon, sobre uma base comum a stable e nightly |
| Interface e recursos visuais | `e4cb23d40aa2` | snapshot nQuake | atual no repositório de referência |
| KTX | `1.47+x86qw.12` | release oficial sobre recursos nQuake e integração x86QW | 17 usermodes e sete variações oficiais, 77 rotas Frogbot, 54 rotas Race, opções completas de CTF/Race e ajuda F10 contextual alinhada; QVM e símbolos preservados |
| Skins de jogadores | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Miras | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Skyboxes | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Modelos | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Bandeiras do placar | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Texturas externas | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Texturas base | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Mapas selecionados | `e4cb23d40aa2` | coleção curada nQuake | sem download externo em massa |
| Informações de partidas | `e4cb23d40aa2` | coleção curada nQuake | opcional; fora do perfil recomendado |
| Documentação | `e4cb23d40aa2+x86qw.1` | licenças do snapshot e manuais x86QW | atalhos e readmes históricos não entram no runtime |
| QRP alta resolução | `e4cb23d40aa2+x86qw.1` | snapshot nQuake com manual x86QW UTF-8 | contém mapas 1.00 e itens 0.73; ordem explícita em `pak.lst` |
| Final Arena | `1.20+nquake.e4cb23d40aa2+x86qw.2` | snapshot nQuake e gameplay x86QW próprio | fila, estatísticas e opções do mod acessíveis; originais 1.20 e fonte-base preservados; ajuda somente sob demanda |
| Pro-X | `1.1+x86qw.3` | release pública 1.1 e gameplay x86QW | runtime oficial completo; ENT corrige somente quatro campos obsoletos; configuração pessoal antiga migrada com backup; ajuda somente sob demanda |
| Team Fortress | `2.9+nquake.e4cb23d40aa2+x86qw.4` | gamecode 2.9 recompilado sobre assets nQuake | gamecode 2.8 removido do `misc.pak`; LOCs, mapas e mídia nQuake preservados; controles remotos forçados removidos; ajuda somente sob demanda |
| Total Destruction 2 | `2.22+x86qw.3` | pacote independente recompilado do upstream | runtime com magia, especial, runas, votação e áudio completo; efeitos preservados sem gravação, binds, gamma ou ajuda automática |
| MVDSV | `1.11+x86qw.3` | release oficial e build reproduzido por plataforma | servidor dedicado KTX para macOS arm64, Linux amd64 e Windows x64; patch mínimo corrige passagem de argumentos QVM em 64 bits no Apple Silicon |
| QTV | `0+025ca949aca0+x86qw.2` | commit upstream imutável e builds reproduzidos | relay HTTP/MVD opcional, loopback e upload desativado por padrão |
| QWFWD | `1.30+x86qw.3` | release oficial e builds por plataforma | proxy opcional, sem consulta automática a masters no perfil gerenciado |

## Contrato de atualização

- `reference-snapshot`: acompanha o payload mais recente aprovado do nQuake;
- `reference-payload-current`: o conteúdo consumido continua atual mesmo quando
  o branch observado avançou apenas em arquivos fora da distribuição;
- `reference-overlay`: preserva assets do nQuake e substitui um runtime por uma
  release independente comprovada;
- `upstream-overlay`: um artefato oficial substitui somente membros declarados
  sobre a base nQuake;
- `upstream-package`: um componente independente é validado e empacotado sem
  fingir que pertence ao snapshot do nQuake;
- `upstream-composed`: uma base curada do nQuake é mesclada membro a membro com
  uma release oficial completa mediante política de conflitos versionada;
- todo download possui tamanho e SHA-256 antes de entrar no acervo;
- o pacote interno registra origem, membros substituídos e hashes resultantes;
- o catálogo mantém somente a versão atual, enquanto releases antigas continuam
  imutáveis nos mirrors de entrega;
- `maintenance/manage.py check` detecta novidades; `update` prepara apenas as
  atualizações seguras e `publish` permanece uma ação separada.

O bootstrap preserva o snapshot nQuake original, troca seu padrão
`gl_max_size` de 32768 para 16384 e instala um `autoexec.cfg` x86QW com aliases
temporários. A base comum conserva opções reconhecidas por stable e nightly; ao atualizar uma
instalação, o instalador migra o mesmo valor apenas quando a configuração
pessoal ainda contém exatamente o padrão antigo. O arquivo global
`qw/x86qw-user.cfg` é carregado por último e nunca é sobrescrito.

O KTX é um projeto independente e um mod de servidor. O pacote x86QW combina o
`ktx.pk3` encontrado no snapshot nQuake com o `qwprogs.qvm` e o mapa de símbolos
`qwprogs.map` oficiais da versão 1.47, mas o artefato do upstream é preservado
em `dist/mods/ktx/1.47/upstream/` e as fontes em
`dist/mods/ktx/1.47/source/`. Ele não é executado nem substitui o cliente
ezQuake. O servidor dedicado MVDSV é descrito separadamente no ecossistema.

## Composição característica de cada mod

Todos os mods herdam a configuração geral do jogador, mas não fingem possuir a
mesma proveniência. O pipeline é declarado conforme o conteúdo real:

| Mod | Referência nQuake | Atualização independente | Harmonização x86QW |
| --- | --- | --- | --- |
| KTX | `ktx.pk3`, incluindo conteúdo exclusivo e sons de Clan Arena | recursos, QVM e símbolos oficiais 1.47 substituem conflitos declarados | compatibilidade do servidor local, gameplay e configuração pessoal |
| Final Arena | pacote integral Final Arena 1.20 com mapas, mídia e gamecode | não há release mais nova comprovada no acervo | servidor local, binds, fila, estatísticas e ajuda |
| Pro-X | 0.8b é mantido apenas como referência histórica, sem entrar no runtime | o pacote completo 1.1 substitui a versão do nQuake | `qw_server.cfg`, servidor local, binds e configuração pessoal |
| Team Fortress | mapas, modelos, sons e 34 LOCs; o gamecode 2.8 é retirado do `misc.pak` montado e o `detpack.wav` idêntico permanece apenas no `pak1.pak` | `qwprogs.dat` oficial 2.9 é extraído e validado diretamente de `tf29qw.zip` | capacidades compatíveis, servidor local, binds e configuração pessoal |
| Total Destruction 2 | não usa conteúdo do nQuake | distribuição completa 2.22; `saw_down.wav` ausente é recomposto do som idêntico validado | servidor local, armas, magias, runas, votação e configuração pessoal |

Arquivos byte-idênticos só são eliminados quando têm também a mesma função e o
mesmo caminho de runtime. Sons parecidos dentro de `arena.pk3`, `prox/` e
`ktx.pk3` permanecem quando o gamecode de cada gamedir os procura em namespaces
distintos. Foi por isso que os WAVs soltos `qw/sound/ca` puderam ser removidos,
mas a mídia interna de Final Arena e Pro-X não foi descartada.

## Contrato dos perfis de gameplay

A ordem de configuração é invariável e aplicada separadamente ao mod escolhido:

```text
nQuake -> upstream do mod selecionado -> x86QW do mesmo mod -> configuração pessoal do mesmo mod
```

O launcher executa somente o perfil correspondente ao jogo selecionado. O
perfil KTX não é carregado por Final Arena, Pro-X, Team Fortress ou TD2, assim
como nenhum perfil desses mods é carregado pelo KTX. A base nQuake fornece a
configuração geral do cliente; o upstream define a semântica própria do mod; a
camada x86QW harmoniza somente essa combinação; e o arquivo pessoal daquele mod
é executado por último e nunca é sobrescrito.

A camada x86QW substitui apenas binds que conflitam com a mecânica do mod,
mantém os recursos coerentes da composição acima e acrescenta acesso às funções
confirmadas no manual, na configuração original ou no gamecode correspondente.

As teclas de movimento, sensibilidade, rede e preferências visuais continuam
pertencendo ao jogador. Os perfis gerenciados se limitam a armas, ações do mod,
ajustes de compatibilidade do HUD e ajuda contextual. `F10` repete a ajuda
automática mostrada ao carregar cada mod.

O catálogo declara uma única política `common-baseline`: os componentes de
gameplay
usam arquivos de configuração comuns comprovados tanto com ezQuake stable 3.6.9
quanto com a nightly `20260616-101233_a86996a`. Nenhum comando exclusivo da
nightly é gravado globalmente; futuras otimizações específicas deverão entrar
como overlay de cliente explícito, nunca como alteração silenciosa da base.

Final Arena usa o subdiretório correspondente do snapshot nQuake. Pro-X usa a
release pública 1.1; o subdiretório histórico 0.8b é mantido apenas como origem
de migração. Ambos geram pacotes, versões, inventários e recibos independentes.

O TD2 possui duas numerações públicas que não formam uma cronologia simples. A
página ativa do Arena Camper documenta a linha `2.12`, de 2023, mas o ZIP dessa
versão não está mais disponível. O pacote incorporado é a distribuição
QuakeWorld completa `2.22`, de Spinal com patch de Vegetous, ainda disponível
com `qwprogs.dat`, modelos, sons, documentação e fontes. Ele é opcional, entra
no perfil `completo` e não baixa mapas fora do acervo já aprovado do x86QW. A
distribuição original completa permanece preservada em
`dist/mods/td2/2.22/source/`; o runtime recebe somente gamecode, modelos, sons e os
perfis x86QW. Binds, HUD e parâmetros do servidor vêm da camada versionada em
`dist/mods/td2/2.22/x86qw/` e entram no próprio pacote do componente. Somente a
cópia isolada do gamecode pertence a `.x86qw/components/play-support/`, enquanto
`td2/x86qw-td2-user.cfg` permanece pessoal e fora do inventário imutável.
O pacote 2.22 omite `saw_down.wav`, embora o gamecode o pré-carregue. O builder
restaura esse nome a partir do `saw.wav` byte-idêntico que já existe no próprio
upstream, validando tamanho e SHA-256 antes de montar o pacote.
