# Componentes e atualização

O x86QW separa **conteúdo instalável** de **origem versionada**. Nem toda pasta
do nQuake corresponde a um projeto com releases: skins, miras, skyboxes, mapas e
outros recursos são coleções curadas. Nesses casos, a versão correta é o commit
imutável do `nQuake/distfiles`, não um número inventado.

Estado verificado em 29 de julho de 2026:

## Layout dos mods versionados

Cada mod externo usa a mesma estrutura sob `dist/mods/<mod>/<versao>/`:

- `upstream/`: pacotes binários oficiais consumidos pelo runtime;
- `source/`: código-fonte ou distribuição original que contém as fontes;
- `x86qw/`: configurações, perfis e overlays mantidos pelo x86QW.

As pastas são criadas somente quando há conteúdo correspondente. Por isso o
Pro-X, cujo código-fonte público não foi localizado, possui `upstream/` e
`x86qw/`, mas não uma pasta `source/` vazia. `dist/mods/x86qw/` é reservado aos
componentes autorais da própria distribuição e não representa um upstream.

| Componente | Versão oferecida | Estratégia | Estado |
| --- | --- | --- | --- |
| Configuração base | `e4cb23d40aa2+x86qw.4` | snapshot nQuake com bootstrap x86QW | aliases temporários e textura máxima coerente com OpenGL 4.1 do Apple Silicon, sobre uma base comum a stable e nightly |
| Interface e recursos visuais | `e4cb23d40aa2` | snapshot nQuake | atual no repositório de referência |
| KTX | `1.47+nquake.e4cb23d40aa2+x86qw.7` | release oficial sobre recursos nQuake e gameplay x86QW | armas ergonômicas, comunicação competitiva, símbolos QVM preservados e carregamento QVM direto no servidor local |
| Skins de jogadores | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Miras | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Skyboxes | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Modelos | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Bandeiras do placar | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Sons | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Texturas externas | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Texturas base | `e4cb23d40aa2` | coleção curada nQuake | atual no repositório de referência |
| Mapas selecionados | `e4cb23d40aa2` | coleção curada nQuake | sem download externo em massa |
| Informações de partidas | `e4cb23d40aa2` | coleção curada nQuake | opcional; fora do perfil recomendado |
| Documentação | `e4cb23d40aa2+x86qw.1` | licenças do snapshot e manuais x86QW | atalhos e readmes históricos não entram no runtime |
| QRP alta resolução | `e4cb23d40aa2+x86qw.1` | snapshot nQuake com manual x86QW UTF-8 | contém mapas 1.00 e itens 0.73; ordem explícita em `pak.lst` |
| Final Arena | `e4cb23d40aa2+x86qw.4` | snapshot nQuake e gameplay x86QW próprio | fila, estatísticas e opções do mod acessíveis |
| Pro-X | `1.1+x86qw.1` | release pública 1.1 e gameplay x86QW | runtime oficial completo; perfil reaplicado pelo `qw_server.cfg`; configuração pessoal antiga migrada com backup |
| Team Fortress | `2.9+nquake.e4cb23d40aa2+x86qw.1` | gamecode e fontes oficiais 2.9 sobre assets nQuake | efeitos e velocidades clássicos liberados por quatro capacidades remotas; binds remotos bloqueados |
| Total Destruction 2 | `2.22+x86qw.5` | pacote independente do upstream e gameplay x86QW | runtime mínimo com magia, especial, runas, votação e áudio completo |

## Contrato de atualização

- `reference-snapshot`: acompanha o commit mais recente aprovado do nQuake;
- `reference-overlay`: preserva assets do nQuake e substitui um runtime por uma
  release independente comprovada;
- `upstream-overlay`: um artefato oficial substitui somente membros declarados
  sobre a base nQuake;
- `upstream-package`: um componente independente é validado e empacotado sem
  fingir que pertence ao snapshot do nQuake;
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
`dist/mods/ktx/1.47/source/`. Ele não é
executado nem substitui o cliente ezQuake. O x86QW ainda não distribui MVDSV.

## Contrato dos perfis de gameplay

Cada mod parte da configuração geral do nQuake. A camada x86QW substitui apenas
binds que conflitam com a mecânica do mod, mantém os recursos coerentes do
nQuake e acrescenta acesso às funções confirmadas no manual, na configuração
original ou no gamecode correspondente. O arquivo pessoal é executado por
último e nunca é sobrescrito:

```text
nQuake -> x86QW do mod -> x86qw-<mod>-user.cfg
```

As teclas de movimento, sensibilidade, rede e preferências visuais continuam
pertencendo ao jogador. Os perfis gerenciados se limitam a armas, ações do mod,
ajustes de compatibilidade do HUD e ajuda contextual. `F10` repete a ajuda
automática mostrada ao carregar cada mod.

O catálogo declara uma única política `common-baseline`: todos os 19 componentes
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
cópia isolada do gamecode pertence a `.install/play-support.*`, enquanto
`td2/x86qw-td2-user.cfg` permanece pessoal e fora do inventário imutável.
O pacote 2.22 omite `saw_down.wav`, embora o gamecode o pré-carregue. O builder
restaura esse nome a partir do `saw.wav` byte-idêntico que já existe no próprio
upstream, validando tamanho e SHA-256 antes de montar o pacote.
