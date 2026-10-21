# Contribuindo com o x86QW

Obrigado por ajudar a manter QuakeWorld jogável, verificável e compreensível. O x86QW é simultaneamente distribuição, CLI, catálogo e portal; por isso, uma mudança pequena pode atravessar mais de um contrato.

## Antes de começar

- procure uma [issue existente](https://github.com/x86dx2/x86qw/issues);
- para novos runtimes, jogos, mods ou mudanças de arquitetura, abra primeiro uma proposta;
- vulnerabilidades seguem a [política de segurança](SECURITY.md), nunca uma issue pública;
- não use a presença de um artefato no catálogo como prova de que ele foi testado naquela plataforma.

## Princípios do repositório

1. **Origem comprovável.** Todo byte incorporado precisa de origem, versão e consumidor declarados.
2. **Upstream intacto.** Arquivos originais e customizações x86QW permanecem em camadas separadas.
3. **Reprodução antes de conveniência.** Pacotes publicados são imutáveis e verificados por tamanho e SHA-256.
4. **Estado pessoal é pessoal.** Configurações e saves do jogador não são tratados como conteúdo gerenciado.
5. **Rede explícita.** `x86qw host` escuta em loopback até você pedir outra interface; o servidor do instalador diz que aceita a rede e oferece "Só nesta máquina".

## Fluxo recomendado

```sh
git clone https://github.com/x86dx2/x86qw.git
cd x86qw
git lfs pull
git switch -c tipo/descricao-curta
```

Faça uma mudança por contexto. Antes de enviar:

```sh
go -C go test ./...
go -C go run ./cmd/x86qw-content verify
go -C go run ./cmd/x86qw help
cd site && npm test && npm run deploy:dry-run
```

`x86qw-content verify` valida distribuição, inventários, receitas, catálogo, TUF e Git LFS. Em cada PR, a CI roda os testes Go em Linux (com race detector, staticcheck e govulncheck) e em Windows, compila a CLI para as cinco plataformas, verifica a distribuição e valida o site. Um e2e noturno instala de verdade em Linux, macOS e Windows. Nada disso substitui usar a mudança numa máquina real; diga no PR em que sistema você a usou.

## Onde alterar

| Intenção | Diretório principal | Contrato relacionado |
|---|---|---|
| Cliente, mod ou conteúdo distribuído | `dist/` | `maintenance/inventory/` |
| Origem, versão ou receita | `maintenance/` | `dist/manifest.json` |
| CLI, app ou bootstrap | `go/` | catálogo e testes Go |
| Portal ou API pública | `site/` | `site/tests/` |
| Arquitetura e operação | `docs/` | fatos públicos do produto |

Não edite pacotes publicados para “corrigir” uma versão existente. Gere uma nova versão por meio do fluxo de manutenção. Uma mudança no catálogo precisa de uma geração TUF nova (`go -C go run ./cmd/x86qw-release sign`), o que só o mantenedor faz; veja o [runbook de release](../docs/runbooks/release.md).

## Adicionando conteúdo

Uma proposta de conteúdo precisa responder:

- qual é o upstream e a versão exata;
- qual licença ou termo permite o uso pretendido;
- qual componente consome cada arquivo;
- como a atualização futura será detectada;
- em quais plataformas o resultado foi usado;
- se há conflito com conteúdo já preservado.

Veja [manutenção da distribuição](../maintenance/README.md), [proveniência](../maintenance/docs/provenance.md) e [arquitetura](../docs/architecture.md).

## Pull requests

Um bom PR contém:

- problema e resultado em linguagem direta;
- o que ficou de fora, quando relevante;
- os comandos executados e o que deu;
- origem e licença de novos assets;
- capturas quando houver mudança visual;
- notas de compatibilidade quando aplicável.

Os PRs entram por squash, com commits assinados e os checks obrigatórios verdes. Merges que mexem em `site/public` vão ao ar na hora.

## Commits

Prefira mensagens imperativas e específicas:

```text
Keep QTV on loopback by default
Show the profile summary before installing
```

Não inclua caches, builds locais, diretórios de instalação ou credenciais. Compile binários de teste fora do repositório (`go build -o /tmp/x86qw ./cmd/x86qw`).
