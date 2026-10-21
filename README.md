<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/hero-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset=".github/assets/hero-light.svg">
    <img alt="x86QW — QuakeWorld, reassembled" src=".github/assets/hero-dark.svg" width="100%">
  </picture>

  <br>

  **Uma distribuição moderna, reproduzível e autocontida de QuakeWorld.**<br>
  Cliente, jogos, servidor dedicado, relay e proxy — instalados por uma única CLI verificável.

  [![Release](https://img.shields.io/github/v/release/x86dx2/x86qw?display_name=tag&sort=semver&style=for-the-badge&label=release&color=ef6a57)](https://github.com/x86dx2/x86qw/releases/latest)
  [![Quality gate](https://img.shields.io/github/actions/workflow/status/x86dx2/x86qw/validate.yml?branch=main&style=for-the-badge&label=quality%20gate&color=4767ff)](https://github.com/x86dx2/x86qw/actions/workflows/validate.yml)
  [![Portal](https://img.shields.io/website?url=https%3A%2F%2Fqw.x86.com.br%2F&style=for-the-badge&label=portal&up_message=online&down_message=offline&color=28a890)](https://qw.x86.com.br/)
  [![Platforms](https://img.shields.io/badge/macOS_·_Linux_·_Windows-catalogued-181b2a?style=for-the-badge)](#compatibilidade)

  [**Instalar**](#instalação) · [O app](#o-app) · [Comandos](#comandos) · [Hospedar](#hospedar) · [Arquitetura](#arquitetura) · [Contribuir](#contribuindo)
</div>

---

## QuakeWorld sem arqueologia

O x86QW transforma um ecossistema histórico em um produto atual: seleciona versões conhecidas, preserva as origens, aplica customizações isoladas e entrega tudo por um catálogo assinado com tamanho e SHA-256. O resultado é uma instalação organizada, atualizável e capaz de jogar localmente, entrar em servidores ou subir sua própria infraestrutura.

<table>
  <tr>
    <td width="33%" valign="top"><h3>⚡ Entre na partida</h3>ezQuake stable ou nightly, conteúdo curado do nQuake e cinco jogos prontos para a mesma instalação.</td>
    <td width="33%" valign="top"><h3>🧩 Monte seu perfil</h3>Escolha entre os perfis essencial, recomendado, completo ou uma seleção própria de componentes.</td>
    <td width="33%" valign="top"><h3>🛡️ Verifique os bytes</h3>Catálogo autenticado por TUF, pacotes imutáveis, recibos locais e reparo explícito sem confundir arquivos pessoais com conteúdo gerenciado.</td>
  </tr>
  <tr>
    <td width="33%" valign="top"><h3>🎯 Domine o KTX</h3>Duel, equipes, CTF, Race, Clan Arena, Wipeout, Frogbots e dezenas de combinações declarativas.</td>
    <td width="33%" valign="top"><h3>📡 Hospede a arena</h3>MVDSV, QTV e QWFWD na mesma pasta, com várias instâncias por sessão, senhas fora da linha de comando e encerramento coordenado.</td>
    <td width="33%" valign="top"><h3>♻️ Atualize com controle</h3>Veja o plano antes da mudança. No terminal a CLI pergunta; em automação, <code>--yes</code> confirma e <code>--dry-run</code> só mostra.</td>
  </tr>
</table>

## Instalação

> [!IMPORTANT]
> O x86QW organiza e distribui software e conteúdo de diferentes origens. Leia [Proveniência e licenças](#proveniência-e-licenças) antes de redistribuir o conjunto.

### macOS e Linux

```sh
curl -fsS https://qw.x86.com.br/install.sh | bash
```

### Windows

No PowerShell ou no Prompt de Comando:

```powershell
powershell -c "irm https://qw.x86.com.br/install.ps1 | iex"
```

O bootstrap baixa a CLI nativa `x86qw` da release `x86qw-cli-2.4.1` no
GitHub, confere o tamanho e o SHA-256 fixados no próprio script e executa
`x86qw setup`. Num terminal, isso abre o instalador do app; ele consulta o
catálogo TUF público e pergunta o que instalar e onde. Nada além do sistema é
necessário: macOS e Linux usam `curl`, e o Windows usa o PowerShell incluído.
No Unix, o corpo do script só roda na última linha, então um download truncado
falha na análise e não executa nada. No Windows, o bootstrap não encerra a
sessão do PowerShell; se a instalação falhar, a mensagem e o código ficam em
`$LASTEXITCODE`.

A pasta padrão é `~/Games/x86qw`. Na seção **Integração** do instalador você
escolhe atalhos, o comando no `PATH` e os tipos de arquivo:

- **Comando no `PATH`:** no macOS e no Linux, um link `~/.local/bin/x86qw`; se
  essa pasta não estiver no seu `PATH`, use o caminho completo
  `~/Games/x86qw/x86qw.sh` ou acrescente-a ao shell. No Windows, a pasta da
  instalação entra no `PATH` do usuário.
- **Atalhos:** Menu Iniciar e Área de Trabalho no Windows, entrada de menu no
  Linux.
- **Links `qw://` e demos:** abrem servidores e demos `.mvd`/`.qwd` no ezQuake
  da instalação, pelo comando `x86qw open`.

Para preparar a pasta de outro sistema a partir de macOS ou Linux:

```sh
curl -fsS https://qw.x86.com.br/install.sh | bash -s -- --platform windows
```

Os valores aceitos são `macos`, `linux` e `windows`. Os binários da CLI também
estão na [release `x86qw-cli-2.4.1`](https://github.com/x86dx2/x86qw/releases/tag/x86qw-cli-2.4.1),
com `SHA256SUMS` e atestado de proveniência do build
(`gh attestation verify ARQUIVO --repo x86dx2/x86qw`).

A audiência continua `owner-only`. Isso não é autorização `external-public`
nem promessa de suporte para usuários externos.

## O que vem no x86QW

### Cinco jogos, uma instalação

| Jogo | Versão | Experiência |
|---|:---:|---|
| **KTX** | 1.47 | Competitivo, equipes, CTF, Race e Frogbots |
| **Final Arena** | 1.20 | Arsenal e regras próprias em arenas QuakeWorld |
| **Pro-X** | 1.1 | Conversão completa preservada e harmonizada |
| **Team Fortress** | 2.9 | Classes, equipes e objetivos clássicos |
| **Total Destruction 2** | 2.22 | Gameplay expandido com customização x86QW isolada |

### Perfis para cada tipo de instalação

| Perfil | Conteúdo | Ideal para |
|---|---|---|
| **Essencial** | Base visual + KTX | Entrar rápido e jogar QuakeWorld competitivo |
| **Recomendado** | Essencial + mapas, skins, modelos, texturas e documentação | A experiência x86QW equilibrada (padrão do instalador) |
| **Completo** | Todos os 21 componentes, cinco jogos, MVDSV, QTV e QWFWD | Jogadores, hosts e exploradores do ecossistema |
| **Personalizado** | Seleção explícita de componentes | Instalações mínimas ou especializadas |

Quem só joga nunca recebe os programas de servidor, mesmo no perfil completo;
eles entram quando a instalação também hospeda.

<details>
<summary><strong>Modos KTX e opções avançadas</strong></summary>

O catálogo cobre 17 usermodes nativos: Duel, 2on2, 3on3, 4on4, 10on10, FFA, CTF, HoonyMode, Blitz 2v2/4v4, 2on2on2, 3on3on3, 4on4on4, XonX, Wipeout, Clan Arena e ThunderWalker ToT. `3on3` identifica duas equipes de três jogadores; `2on2on2`, três equipes de dois. Os aliases `3v3` e `2v2v2` continuam aceitos pela CLI. Também inclui Midair, DMM4, Instagib, LGC, Rocket Arena, Race e Practice.

```sh
x86qw play ktx --mode duel --map dm6 --bots 1 --bot-skill 8 --bot-names x86qw
x86qw play ktx --mode ffa --map dm6 --bots 4 --bot-skill random
x86qw play ktx --mode ctf --ctf-hook smooth --ctf-runes off
x86qw play ktx --mode race --map slide1 --race-style match --race-scoring formula1
```

`x86qw play --help` lista Frogbots, regras de CTF e formatos de Race. A CLI
valida mapas, combinações e limitações do QVM antes de abrir o cliente. Com
bots, só entram mapas com rota Frogbot; CTF e Race cruzam os mapas instalados
com seus ENTs e rotas. Rotas pessoais regulares também são reconhecidas.

Na tela **Jogar** do app, os 24 modos aparecem agrupados (recomendados,
individuais, equipes, arena/alternativos e treino) e cada opção só aparece
quando faz sentido: Race pergunta formato, pontuação, pacemaker e
visibilidade dos corredores; CTF pergunta gancho, runas e spawn; modos
compatíveis oferecem Frogbots, habilidade e nomes. Há quatro perfis de nome
(`--bot-names`): `default` (KTX padrão), os nomes originais; `x86qw`, mapas e
modos da distribuição embaralhados a cada lançamento; `one-piece`, a lista One
Piece embaralhada; e `personal`, uma lista pessoal editável. Veja o
[guia de nomes dos Frogbots](docs/FROGBOTS.md).
Modos de tamanho fixo limitam a seleção às vagas restantes: Duel oferece no
máximo um bot quando há um jogador humano.

Ao entrar no mapa, o console imprime as teclas do modo ativo. Em Duel, `F5`
marca ready, `F6` interrompe e `F11` mostra as regras; com Frogbots, `INS`,
`DEL`, `HOME` e `END` gerenciam bots e habilidade. Os perfis nomeados também
preservam as cores de camisa, calça e equipe definidas pelo KTX.
`--bot-skill random` sorteia uma habilidade independente de 1 a 20 sempre que
um bot entra. `INS` respeita a lotação do modo, `DEL` libera a vaga e
`HOME`/`END` alteram cumulativamente a habilidade dos próximos bots. Arma, vida
e interrupção por morte são opções exclusivas do ToT. `F12` fecha diretamente
o QuakeWorld em todos os cinco jogos.

</details>

## O app

Depois da instalação, `x86qw` — ou `x86qw.sh` na pasta da instalação, e
`x86qw.cmd` no Windows — abre o app num terminal. `x86qw --target PASTA` abre
outra instalação. Sem terminal, `x86qw` só imprime a ajuda.

A tela inicial reúne:

- **Jogar:** jogo, modo, mapa, bots e regras, com a última partida guardada;
- **Servidores:** a lista pública do QuakeWorld Hub, com favoritos primeiro.
  Enter entra, `ctrl+f` marca favorito, `ctrl+w` assiste pelo QTV, `ctrl+o`
  entra como espectador e `ctrl+r` atualiza. Sem o Hub, aparecem os favoritos e
  recentes locais;
- **Hospedar:** inicia e encerra o servidor configurado, com suas instâncias,
  QTV e QWFWD, e mostra o que está rodando;
- **Atualizar**, **Componentes e configuração**, **Reparar e verificar**,
  **Ferramentas** (diagnóstico, verificação, upgrade, mudanças, favoritos,
  perfil, limpeza e o painel HTML) e **Desinstalar**.

O instalador segue a mesma lógica. Primeiro você diz o que quer — **Jogar**,
**Hospedar** ou **Jogar e hospedar** —; depois ajusta componentes, sua
configuração (nick, teclas, mouse, vídeo, som e ruleset), o servidor, a
integração e a pasta. Um pré-voo confere pasta, disco, catálogo e portas antes
de escrever qualquer coisa, e cancelar no meio devolve a pasta ao que era.

A seção **Receita** mostra o comando e o JSON equivalentes ao que você
escolheu, sem senhas. Com eles, a mesma instalação se repete sem o app:

```sh
x86qw setup --recipe receita.json --yes
x86qw setup --intent both --nick jogador --content recommended --servers duel:dm6,4on4:dm3+bots --yes
```

O app segue o idioma do sistema (português ou inglês) e troca de idioma e tema
pelo canto superior. Em terminais sem glifos Unicode, `X86QW_GLYPHS=ascii`
força caracteres simples; `X86QW_GLYPHS=unicode` força os completos.

## Comandos

`x86qw help` lista os comandos por tarefa; `x86qw COMANDO --help` mostra o
uso e as opções de cada um. As opções podem vir antes ou depois da pasta.

| Comando | Faz o quê |
|---|---|
| `play [JOGO]` | Abre uma partida local; sem `--mode` (ou com `--menu`), num terminal, abre a tela Jogar do app; `--channel nightly` usa o ezQuake nightly |
| `open ENDEREÇO\|DEMO` | Abre um servidor `qw://` (inclusive `qw://N@host:porta/qtvplay`) ou uma demo `.mvd`/`.qwd` no ezQuake |
| `hub` | Lista os servidores públicos; sem o Hub, usa favoritos e recentes locais |
| `host [JOGO]` | Hospeda um servidor MVDSV; `--instance` acrescenta servidores à sessão, `--with-qtv` e `--with-proxy` sobem os serviços junto |
| `qtv` | Inicia o relay QTV de um servidor |
| `proxy` | Inicia o proxy QWFWD |
| `status` | Mostra os serviços ativos; `--stop` encerra a stack |
| `setup` | Instala (pelo app, por `--profile` ou por receita) ou adota uma instalação existente |
| `install` | Instala ezQuake e um perfil de conteúdo, como `setup --profile` |
| `update` | Atualiza o ezQuake, os componentes e a CLI instalados |
| `upgrade` | Como `update`, e acrescenta o que o seu perfil ganhou |
| `verify` | Confere cada arquivo gerenciado contra o catálogo e os recibos |
| `repair` | Restaura o que `verify` acusar, sem tocar no que você editou |
| `uninstall` | Remove a instalação e mantém configurações, PAKs e demos; `--purge` apaga tudo |
| `library` | Favoritos e recentes; `--add` e `--remove` |
| `profile` | Mostra configurações, cache e dados pessoais; `--backup` e `--restore` |
| `changes` | Mostra arquivos novos, alterados ou removidos em relação à instalação |
| `ui` | Gera um painel HTML local, só de leitura |
| `cleanup` | Apaga caches; `--downloads` e `--personal-data` vão além |
| `doctor` | Diagnostica a instalação sem alterar arquivos; `--bundle` gera um zip para compartilhar |
| `catalog` | Autentica o catálogo público via TUF e mostra as versões dos papéis |
| `migrate` | Converte metadados de instalações antigas para o formato atual |
| `version` | Mostra a versão |

O que altera ou encerra algo — `update`, `upgrade`, `repair`, `uninstall`,
`status --stop` e as receitas — mostra o que vai fazer e pergunta no terminal.
Sem terminal, exige `--yes` e sai com código 2 se ele faltar. `--dry-run` só
mostra o plano. `verify`, `doctor`, `hub`, `catalog`, `version`, `status` e os
planos de `update`, `upgrade` e `repair` com `--dry-run` aceitam `--json` e
escrevem um envelope JSON numa linha.

Sem `--target` nem pasta, cada comando usa a instalação que contém a CLI em
execução; fora de uma instalação, `~/Games/x86qw`. Os códigos de saída são 0
(sucesso), 1 (falha), 2 (uso incorreto ou confirmação faltando) e 130
(interrompido).

## Hospedar

```sh
x86qw host ktx --mode 4on4 --map dm3
x86qw host ktx --mode duel --map dm6 --instance ffa:dm4:28502:bots --with-qtv
x86qw host team-fortress --map 2fort5r
x86qw host td2 --map dm6 --bind 0.0.0.0
x86qw qtv --upstream 127.0.0.1:28501
x86qw status --stop
```

Há dois pontos de partida, com padrões diferentes:

- **`x86qw host` direto:** escuta em `127.0.0.1:28501`, QTV em
  `127.0.0.1:28000` e QWFWD em `127.0.0.1:30000`, e acompanha o terminal. A
  rede só entra com `--bind` explícito; `--background` destaca a sessão.
- **O servidor que o instalador monta** (intenção Hospedar ou Jogar e
  hospedar, ou `setup --intent host`): começa na porta 27500, com uma porta por
  instância, QTV na 28000 e QWFWD na 30000, e aceita jogadores da rede em todas
  as interfaces. Escolha **Só nesta máquina** no instalador, ou `--local` na
  receita, para restringir a `127.0.0.1`. Ele sobe em segundo plano a partir do
  app ou do comando que o instalador mostra no fim.

Senhas podem vir de prompt sem eco ou de arquivo privado; a CLI não as coloca na
linha de comando dos processos filhos nem nos logs. Locks e journals impedem
manutenção concorrente, coordenam o encerramento e permitem recuperação
conservadora após crash. Veja o [guia de hospedagem](docs/HOSTING.md).

A CLI inspeciona ZIP e PK3 antes de qualquer extração, com limites, modos
canônicos e promoção atômica. No Windows, o estado privado da instalação nasce
com DACL protegida, limitada ao usuário atual e a `LOCAL SYSTEM`; arquivos de
senha externos só são aceitos com essa mesma restrição e nunca são reescritos.
Consulte os [ADR 0002](docs/adr/0002-fronteira-unica-de-arquivos.md) e
[ADR 0003](docs/adr/0003-dacl-privada-windows.md).

## Arquitetura

<a href="docs/diagrams/x86qw-platform.html">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/architecture-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset=".github/assets/architecture-light.svg">
    <img alt="Fluxo da plataforma x86QW: fontes fixadas, distribuição, catálogo, instalador e runtimes" src=".github/assets/architecture-dark.svg" width="100%">
  </picture>
</a>

O repositório é a fonte canônica. `dist/` preserva os inputs; inventários declaram consumidores e dependências; o catálogo projeta os pacotes públicos e é assinado por TUF; a CLI materializa apenas o perfil e a plataforma escolhidos. GitHub Releases é o canal principal dos pacotes e GitLab Generic Packages mantém o mirror de contingência.

Todo o código vive em `go/`: a CLI `x86qw` (app, instalação, jogo, hub,
serviços e manutenção da instalação), `x86qw-content` (verificação,
atualização, build e publicação do `dist/`) e `x86qw-release` (release da CLI,
assinatura TUF, montagem do site e monitor). O repositório não contém Python.

```text
dist/           produto canônico, upstreams preservados e customizações x86QW
go/             CLI x86qw e ferramentas de manutenção
maintenance/    inventários, receitas e trust root
site/           portal, API pública do catálogo e deploy Cloudflare
docs/           arquitetura, hospedagem, runbooks e decisões
```

[Arquitetura detalhada](docs/architecture.md) · [Diagrama interativo](docs/diagrams/x86qw-platform.html) · [Proveniência](maintenance/docs/provenance.md) · [Roadmap](docs/ROADMAP.md) · [Status](docs/PROJECT-STATUS.md)

## Estado do projeto

A CLI atual é a da release `x86qw-cli-2.4.1`, em modo `owner-only`: um usuário,
o próprio mantenedor, que a usa num Mac Apple M3. Os binários de macOS, Windows
e Linux estão no catálogo e na release; os testes de CI rodam em Linux e
Windows, mas não há registro versionado de smoke nativo das versões 2.x, então
Windows e Linux seguem em preview. Consulte o
[status operacional](docs/PROJECT-STATUS.md).

## Compatibilidade

| Componente | macOS | Linux | Windows |
|---|:---:|:---:|:---:|
| ezQuake stable + nightly | Universal · condicional/preview | x86-64 · preview | x64 · preview |
| MVDSV | arm64 · preview | amd64 · preview | x64 · preview |
| QTV | arm64 · preview | amd64 · preview | x64 · preview |
| QWFWD | arm64 · preview | amd64 · preview | x64 · preview |
| CLI `x86qw` | arm64 + amd64 | amd64 + arm64 | x64 |

> [!NOTE]
> A CI obrigatória roda os testes Go em Linux (com o race detector, staticcheck e govulncheck) e em Windows, compila a CLI para as cinco plataformas e valida distribuição e site. Um e2e noturno instala de verdade em Linux, macOS e Windows. Isso não equivale a uma validação de uso em cada sistema.

O ezQuake stable de macOS preserva o bundle upstream sem alterar `Info.plist`,
sandbox, entitlements ou assinatura. Ele não tem Developer ID nem notarização
comprovada, por isso continua condicional; consulte o
[ADR 0004](docs/adr/0004-preservar-bundle-upstream-ezquake-stable-macos.md).

## Desenvolvendo

Quem clonou o repositório usa as fontes locais:

```sh
git lfs pull
go -C go run ./cmd/x86qw help
go -C go run ./cmd/x86qw play --help
```

A validação integral:

```sh
go -C go test ./...
go -C go run ./cmd/x86qw-content verify
cd site && npm test && npm run deploy:dry-run
```

Para visualizar o portal localmente:

```sh
cd site
npm run dev
```

Abra `http://127.0.0.1:8787`. A configuração versionada de produção não contém
um diretório de assets: o workflow **Deploy site** monta o site e o publica a
cada merge que muda `site/public`, e `wrangler deploy` sem `--assets` falha
fechado. A release da CLI e a operação TUF estão no
[runbook de release](docs/runbooks/release.md); a manutenção do conteúdo, em
[maintenance/README.md](maintenance/README.md); a operação do site, em
[site/README.md](site/README.md).

## Contribuindo

Contribuições são bem-vindas quando preservam proveniência, reprodutibilidade e a fronteira entre upstream e customização x86QW. Antes de abrir um PR:

1. leia o [guia de contribuição](.github/CONTRIBUTING.md);
2. mantenha cada mudança pequena e rastreável;
3. execute `go -C go test ./...` e `go -C go run ./cmd/x86qw-content verify`;
4. explique a origem e o consumidor de qualquer novo arquivo distribuído.

[Reportar um bug](https://github.com/x86dx2/x86qw/issues/new?template=bug-report.yml) · [Propor uma melhoria](https://github.com/x86dx2/x86qw/issues/new?template=feature-request.yml) · [Obter suporte](.github/SUPPORT.md) · [Política de segurança](.github/SECURITY.md)

## Proveniência e licenças

x86QW é uma distribuição composta, não uma relicença única de todos os seus componentes. Clientes, engines, mods, ferramentas e conteúdo mantêm suas licenças e termos de origem; customizações próprias ficam separadas dos bytes upstream. Os dados-base registrados são tratados como componente independente.

Consulte [proveniência e licenças por componente](maintenance/docs/provenance.md) antes de copiar, publicar ou redistribuir artefatos. QuakeWorld, Quake e marcas relacionadas pertencem aos seus respectivos titulares; este projeto comunitário não é afiliado nem endossado por eles.

---

<div align="center">
  <strong>READY // CONNECT // FRAG</strong><br>
  <sub>Feito para preservar QuakeWorld — e continuar jogando.</sub><br><br>
  <a href="https://qw.x86.com.br/">Portal</a> ·
  <a href="https://github.com/x86dx2/x86qw/releases">Releases</a> ·
  <a href="https://github.com/x86dx2/x86qw/issues">Issues</a>
</div>
