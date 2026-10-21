# Release da CLI e operação TUF

Tudo aqui roda com as ferramentas Go do repositório. Comandos partem da raiz
do checkout.

## O que é automático

| O quê | Quando | Workflow |
| --- | --- | --- |
| Deploy do site | a cada merge em `main` que toca `site/public` | `site-deploy.yml` |
| Renovação do timestamp (lease de 30 dias) | segunda, 06:41 UTC | `tuf-timestamp-auto-renewal.yml` |
| Monitor da cadeia pública | de hora em hora | `tuf-monitor.yml` |

- O deploy publica o que está em `main`, nunca volta o timestamp TUF para uma
  versão anterior e recusa uma versão já publicada com outros bytes. Quando
  passa a servir uma CLI nova, marca a release dela como *latest*.
- A renovação usa a chave online da role timestamp, publica, confere e abre um
  PR que registra o timestamp novo em `main`. Recusa rodar se produção e `main`
  divergem. Toda falha abre uma issue P0.
- O monitor falha (e abre uma issue P0) quando o timestamp tem menos de 72
  horas, snapshot ou targets menos de 14 dias, a root menos de 60 dias, quando
  produção não serve `main` há mais de uma hora ou quando ele mesmo parou.

## Publicar uma versão da CLI

1. Publique os binários:

   ```sh
   gh workflow run go-release.yml --ref main -f version=X.Y.Z
   ```

   O ambiente `cli-release` pede aprovação (revisor `x86dx2`). O workflow
   testa, compila os cinco binários, gera `SHA256SUMS`, atesta a proveniência
   e publica `x86qw-cli-X.Y.Z` como *prerelease*.

2. Prepare o checkout, no Mac que guarda as chaves offline:

   ```sh
   git switch -c release/cli-X.Y.Z origin/main
   go -C go run ./cmd/x86qw-release prepare --version X.Y.Z
   ```

   O `prepare` baixa os binários e confere cada um contra o `SHA256SUMS`,
   regrava o catálogo (com `generated_at` novo), o `product.json`, os
   bootstraps `install.sh` e `install.ps1` e a versão citada no `README.md` e no
   `index.html`. Depois assina a próxima geração TUF (a maior entre o checkout
   e a produção, mais um), remove a anterior do checkout e roda
   `x86qw-content verify`.

3. Revise o diff, faça o commit, abra o PR e espere a CI.
4. Faça o merge (squash). O deploy roda sozinho, e a release vira *latest*.
5. Confira: `go -C go run ./cmd/x86qw-release monitor --checkout`.

O teste `TestCheckoutNamesOneRelease` falha se o catálogo, os bootstraps, o
`README.md` e a página citarem versões diferentes da CLI.

## Publicar conteúdo

Depois de `x86qw-content update`, `add` ou `build --register` e de
`x86qw-content publish`, o catálogo mudou e precisa de uma geração TUF nova
antes do PR:

```sh
go -C go run ./cmd/x86qw-release sign
go -C go run ./cmd/x86qw-content verify
```

Sem isso, o `verify` acusa "catálogo local diverge do target TUF".

## Renovar snapshot e targets

Snapshot e targets valem 90 dias a partir da assinatura e só se renovam com as
chaves offline. Toda release da CLI ou de conteúdo já os renova. Sem release,
no mesmo Mac:

```sh
git switch -c ops/tuf-renew origin/main
go -C go run ./cmd/x86qw-release sign
go -C go run ./cmd/x86qw-content verify
```

Depois disso: PR, merge e deploy automático. O monitor avisa 14 dias antes do
vencimento.

## Root

A root v1 vence em 2027-08-10, e o monitor avisa 60 dias antes. Renovar a
root exige as chaves root offline e uma `2.root.json` assinada pela root atual
e pela nova; o cliente segue a cadeia de roots publicadas a partir da que traz
embutida (`go/internal/trust/root.json`). Ainda não há comando para isso:
planeje o trabalho antes de junho de 2027.

## Quando algo falha

- **A renovação recusou porque produção diverge de `main`.** Rode
  `gh workflow run site-deploy.yml` e, quando terminar,
  `gh workflow run tuf-timestamp-auto-renewal.yml`.
- **Um deploy falhou e `main` tem mudanças do site que não estão no ar.** A
  próxima renovação também publica o `site/public` de `main`, mas não espere
  por ela: rode `gh workflow run site-deploy.yml`.
- **O deploy recusou: "produção serve o timestamp N e main o M".** A renovação
  publicou e o PR que registra o timestamp ainda não entrou. Espere o merge
  dele; o deploy roda de novo.
- **O PR da renovação (`bot/tuf-timestamp-vN`) falhou na CI e ficou aberto.**
  Produção segue à frente de `main` até ele entrar. Corrija o que a CI apontou
  no próprio branch e faça o merge; o deploy e o monitor voltam ao normal.
- **O deploy recusou: "timestamps N diferentes".** Um PR assinou a mesma
  versão que a renovação publicou. No branch do PR, rode
  `go -C go run ./cmd/x86qw-release sign` de novo e atualize o PR.
- **O timestamp de produção está para vencer.** Rode a renovação manualmente.
  Se a chave online falhar, `x86qw-release sign` gera uma geração nova com as
  chaves offline.
- **O monitor diz que produção não serve `main`.** Veja o último run do
  *Deploy site*; rode-o de novo pelo `workflow_dispatch`.
- **Chave comprometida.** Siga o [runbook de comprometimento](trust-compromise.md).

## Chaves e ambientes

- As chaves offline (root, targets, snapshot, timestamp) ficam em
  `.tuf-production/keys`, só no Mac do mantenedor e fora do Git.
- A chave online da role timestamp é o segredo `TUF_TIMESTAMP_KEY_B64` do
  repositório. Ela não assina root, targets nem snapshot.
- O ambiente `cli-release` exige aprovação e só aceita branches protegidos; ele
  publica binários.
- O ambiente `release` não pede aprovação, também só aceita branches
  protegidos e guarda `CLOUDFLARE_API_TOKEN` e `RELEASE_GH_TOKEN`; o deploy e a
  renovação usam esse ambiente.
