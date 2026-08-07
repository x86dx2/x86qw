# Contribuindo com o x86QW

Obrigado por ajudar a manter QuakeWorld jogável, verificável e compreensível. O x86QW é simultaneamente distribuição, instalador, catálogo e portal; por isso, uma mudança pequena pode atravessar mais de um contrato.

## Antes de começar

- procure uma [issue existente](https://github.com/x86dx2/x86qw/issues);
- vincule a branch à issue antes de começar a implementação;
- para novos runtimes, jogos, mods ou mudanças de arquitetura, abra primeiro uma proposta;
- vulnerabilidades seguem a [política de segurança](SECURITY.md), nunca uma issue pública.

## Limites de trabalho

- mantenha no máximo uma frente estrutural em andamento por vez;
- mantenha o WIP limitado: uma branch deve ter uma intenção verificável e um
  gate explícito, não uma coleção de correções incidentais;
- separe a PR de implementação da PR de release, promoção ou publicação;
- atualize [PROJECT-STATUS.md](../docs/PROJECT-STATUS.md) e
  [ROADMAP.md](../docs/ROADMAP.md) somente quando o estado evidenciado mudar;
- não use a presença de um artefato no catálogo como prova de smoke nativo.

## Princípios do repositório

1. **Origem comprovável.** Todo byte incorporado precisa de origem, versão e consumidor declarados.
2. **Upstream intacto.** Arquivos originais e customizações x86QW permanecem em camadas separadas.
3. **Reprodução antes de conveniência.** Pacotes publicados são imutáveis e verificados por tamanho e SHA-256.
4. **Estado pessoal é pessoal.** Configurações e saves do jogador não são tratados como payload gerenciado.
5. **Rede segura por padrão.** Serviços usam loopback até uma exposição explícita.

## Fluxo recomendado

```sh
git clone https://github.com/x86dx2/x86qw.git
cd x86qw
git lfs pull
# crie a branch somente depois de existir uma issue vinculada
git switch -c tipo/descricao-curta
```

Faça uma mudança por contexto. Antes de enviar:

```sh
./maintenance/manage.py verify
./dist/installer/bin/manager.py --help
./dist/installer/bin/manager.py play --help
cd site && npx --yes wrangler@4.114.0 deploy --dry-run
```

O primeiro comando valida distribuição, inventários, receitas, catálogos, instalador e testes. A CI repete contratos portáveis em macOS, Linux e Windows com Python 3.10 e 3.13.

A validação operacional da implementação é registrada diretamente no Mac; Linux e Windows permanecem nos contratos de compatibilidade até haver evidência nativa do candidato.

## Onde alterar

| Intenção | Diretório principal | Contrato relacionado |
|---|---|---|
| Cliente, mod ou conteúdo distribuído | `dist/` | `maintenance/inventory/` |
| Origem, versão ou receita | `maintenance/` | `dist/manifest.json` |
| CLI ou bootstrap | `dist/installer/` | catálogo e testes do instalador |
| Portal ou API pública | `site/` | `site/tests/` |
| Arquitetura e operação | `docs/` | fatos públicos do produto |

Não edite pacotes publicados para “corrigir” uma versão existente. Gere uma nova versão por meio do fluxo de manutenção.

## Adicionando conteúdo

Uma proposta de conteúdo precisa responder:

- qual é o upstream e a versão exata;
- qual licença ou termo permite o uso pretendido;
- qual componente consome cada arquivo;
- como a atualização futura será detectada;
- quais plataformas e smokes cobrem o resultado;
- se há conflito com conteúdo já preservado.

Veja [manutenção da distribuição](../maintenance/README.md), [proveniência](../maintenance/docs/provenance.md) e [arquitetura](../docs/architecture.md).

## Pull requests

Um bom PR contém:

- problema e resultado em linguagem direta;
- escopo explícito e itens deliberadamente fora dele;
- evidência dos comandos executados;
- origem e licença de novos assets;
- capturas quando houver mudança visual;
- notas de compatibilidade ou migração quando aplicável.

Toda PR deve declarar o baseline, a issue concluída, a próxima issue
desbloqueada e as plataformas não executadas no bloco obrigatório do template.
Uma mudança de release, promoção ou publicação abre uma PR separada depois que
a implementação tiver seu gate aprovado. O mantenedor pode pedir que o WIP
seja dividido quando houver mais de uma frente estrutural.

Mantenedores podem pedir que uma mudança seja dividida se misturar produto, atualização de upstream e publicação. A etapa de release é protegida e separada da revisão do código.

## Commits

Prefira mensagens imperativas e específicas:

```text
feat(installer): add explicit profile summary
fix(host): keep QTV on loopback by default
docs(readme): clarify native smoke coverage
```

Não inclua caches, builds locais, diretórios de instalação ou credenciais.
