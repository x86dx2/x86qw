# Roadmap do x86QW

## Onde estamos

A linha atual é a CLI 2.x em Go, em modo `owner-only`
([ADR 0008](adr/0008-owner-only-release-gates.md)). O estado de hoje está em
[PROJECT-STATUS.md](PROJECT-STATUS.md). A jornada até a 1.0 e os planos
pós-1.0 da linha Python continuam registrados em [releases/](releases/),
[post-1.0/](post-1.0/) e [implementation/](implementation/), como histórico.

## Próximos passos

1. **Validação nativa registrada:** um registro versionado de instalação e uso
   em Linux e Windows, para tirar essas plataformas de preview.
2. **Root TUF:** um comando no `x86qw-release` para preparar e assinar uma root
   nova, antes de a v1 vencer em 2027-08-10.
3. **Custódia das chaves:** separar root e targets em mídia offline própria,
   com backup, antes de qualquer abertura para terceiros.
4. **macOS:** decidir entre manter o bundle upstream do ezQuake stable
   (condicional) ou buscar um bundle assinado e notarizado.

## Abertura para terceiros

`external-public` só entra com decisão explícita do mantenedor. Antes dela:

- validação nativa registrada nas plataformas anunciadas;
- custódia das chaves separada da máquina de uso;
- suporte e política de correção definidos para usuários externos;
- textos públicos revistos para não prometer mais do que foi validado.

## Fora do escopo atual

Central de demos, validação formal de MVD/QWD, treinamento como comando,
clientes ou engines novos, mods e mapas externos, serviços persistentes do
sistema e perfis operacionais adicionais exigem propostas próprias, contratos,
testes e evidência de plataforma. Eles não entram como efeito colateral de
`play`, `host`, `update` ou `upgrade`.

Para a visão de longo prazo do ecossistema, consulte
[ROADMAP-QUAKE-ECOSYSTEM.md](ROADMAP-QUAKE-ECOSYSTEM.md).
