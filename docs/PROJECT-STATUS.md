# Estado atual do projeto

## Modo operacional

O projeto está em `owner-only`: um único usuário, o próprio mantenedor, com
reinstalação limpa permitida. A decisão está no
[ADR 0008](adr/0008-owner-only-release-gates.md). A audiência
`external-public` não foi autorizada; abri-la exige decidir de novo migração de
instalações antigas, período de uso por terceiros e custódia das chaves.

## O que está publicado

- **CLI:** em Go, publicada como `x86qw-cli-X.Y.Z` para macOS (arm64 e
  amd64), Linux (amd64 e arm64) e Windows (x64). A versão atual é a que o
  [README](../README.md#instalação) e o
  [catálogo do produto](https://qw.x86.com.br/api/v1/product.json) citam; o
  bootstrap do site a fixa por tamanho e SHA-256.
- **Conteúdo:** 21 componentes em 28 pacotes, cinco jogos, ezQuake stable e
  nightly, MVDSV, QTV e QWFWD.
- **Confiança:** catálogo autenticado por TUF. Root v1 (vence em 2027-08-10),
  snapshot e targets assinados offline (vencem em 2027-01-19) e timestamp
  renovado toda semana com lease de 30 dias.

Os instaladores Python `0.x` e `1.0.x` foram aposentados com a CLI 2.0 e
continuam históricos nos mirrors; os registros deles ficam em
[releases/](releases/).

## Como conferir o estado vivo

Nada neste documento substitui a consulta ao que está no ar:

```sh
go -C go run ./cmd/x86qw-release monitor --checkout   # cadeia TUF pública e produção = main
x86qw catalog                                          # o catálogo que a CLI autentica
```

O workflow `tuf-monitor.yml` faz a primeira consulta de hora em hora e abre uma
issue P0 quando um papel entra na janela de alerta (timestamp 72 horas,
snapshot e targets 14 dias, root 60 dias), quando produção deixa de servir
`main` por mais de uma hora ou quando ele mesmo para de rodar.

## Operação

- **Site:** cada merge que muda `site/public` vai ao ar pelo workflow
  `site-deploy.yml`, que recusa voltar o timestamp TUF.
- **Renovação TUF:** `tuf-timestamp-auto-renewal.yml` roda toda segunda-feira
  sem aprovação manual e abre uma issue quando falha.
- **Release da CLI:** `go-release.yml` publica os binários como prerelease,
  com aprovação no ambiente `cli-release`; `x86qw-release prepare` leva a
  release para o checkout. O passo a passo está no
  [runbook de release](runbooks/release.md).
- **CI:** testes Go em Linux (com race detector, staticcheck e govulncheck) e
  em Windows, verificação da distribuição e do site em todo PR; e2e noturno
  instalando de verdade em Linux, macOS e Windows; paridade e corrupção do
  conteúdo toda semana.

## Limites conhecidos

- O uso real é do mantenedor num Mac Apple M3. Não há registro versionado de
  validação nativa das versões 2.x em Linux ou Windows; essas plataformas
  seguem em preview.
- O ezQuake stable de macOS preserva o bundle upstream, sem Developer ID nem
  notarização comprovada; continua condicional.
- As chaves offline ficam numa única máquina. Custódia separada e backup são
  requisitos de uma abertura para terceiros.
- A renovação da root (antes de 2027-08-10) ainda não tem comando próprio no
  `x86qw-release`.

## Histórico

Os documentos em [releases/](releases/), [post-1.0/](post-1.0/),
[implementation/](implementation/) e [contracts/1.0.md](contracts/1.0.md)
registram a linha Python 0.x/1.0 e suas decisões. Eles não descrevem a CLI
atual.
