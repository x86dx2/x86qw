# Estado atual do projeto

## Baseline real

A linha canônica é `origin/main`. A revisão exata de um snapshot deve ser
obtida com `git rev-parse origin/main` no momento da auditoria; este documento
não repete um SHA da própria linha que o contém, porque o merge de qualquer
atualização documental mudaria esse valor. A identidade do candidato oficial —
commit, SHA do `candidate.json` e digest do artifact — é sempre a registrada
pelo próprio workflow e pelo checkpoint do PR, nunca por uma cópia manual neste
documento.

## Versões públicas

A versão estável-fonte continua em `dist/installer/VERSION = 0.7.13` e o
último release estável continua sendo `x86qw-installer-0.7.13`: instalador de
581883 bytes, SHA-256
`114604400e1fd18c4180624314d4bc8ca9b6d4559ed26cfe8d0a767287f2aa32`.

O Release Candidate público é `x86qw-installer-1.0.0-rc.1`, uma prerelease
deliberadamente separada da versão-fonte estável. Ele aponta para o commit de
produto `a8758ee27bebd7c72c24a31dc19335652e260c0a` e foi promovido pelo run
`31752738047`, a partir da linha canônica `main@335d9a062f8ce33b226a9892de82979828a0fd1b`.

Identidade pública do RC:

- instalador: 600431 bytes,
  SHA-256 `9600be7eb2ed14e23b2eeb079bd6aa0e4611f996be0c89741fda12587eb7fed8`;
- `candidate.json`: 14474 bytes,
  SHA-256 `1552a896a0076dd2e347ed5b732b6dd31ba892292e1f9fb8c97fe9111f755bcb`;
- release GitHub: [x86qw-installer-1.0.0-rc.1](https://github.com/x86dx2/x86qw/releases/tag/x86qw-installer-1.0.0-rc.1).

O RC é público e não é GitHub Latest. A imutabilidade host-level da release
GitHub ainda aparece como indisponível (`immutable=false`); o publisher mantém
imutabilidade lógica recusando overwrite, divergência de digest e assets extras.

## Estado de confiança

A root Ed25519 incorporada é validada localmente. A fotografia pública atual
encontrou root v1, timestamp v15, snapshot v15 e targets v15; o timestamp
expira em `2026-08-14T13:11:16Z`. Catálogo, product e cadeia TUF foram
comparados pelos bytes públicos e autenticados pela root incorporada. Isso
confirma a convergência observada nesse instante, mas não constitui evidência
de custódia humana independente nem substitui a cerimônia TUF do candidato.

## Estado local

- downloader, archives, SemVer, launchers, `changes` e `migrate` compartilham
  contratos de runtime;
- fixtures de migração cobrem os instaladores públicos `0.7.0`–`0.7.13`;
- o publisher é build-once e falha fechado para bytes ausentes, mirrors
  divergentes e metadata TUF fora de ordem;
- o candidato carrega o site renderizado e os binários de `dist`, sem depender
  de uma instalação pessoal em `quake-world/`;
- o harness Mac M3 executa plano candidato-owned e registra handoff, smoke
  normalizado e agregado unsigned pendente;
- o catálogo separa `supported`, `conditional` e `preview`: stable macOS
  permanece condicional, nightly e Linux/Windows/macOS Intel permanecem preview
  quando não há evidência nativa do candidato exato;
- a instalação pessoal temporária não é usada pelos testes de release.
- a aceitação pública completa está implementada em
  `maintenance/tools/public_install_smoke.py --full-lifecycle` e no workflow
  M3 manual; execução real e recibo público ainda estão pendentes;
- o harness M3 agora contém migração 0.7.13, Frogbot, lifecycle apply, reparo
  por corrupção e purge; os contratos e testes locais estão verdes, mas isso
  não substitui um run nativo do candidato exato;
- o drill TUF offline está implementado em
  `maintenance/tools/tuf_operation_drill.py`; chaves de produção, custódia e
  execução operacional ainda não foram comprovadas nesta sessão.

## Candidato oficial e promoção

O RC foi construído uma vez, validado por artifacts imutáveis, executado no
runner Apple M3 e promovido sem reconstrução. O fluxo final confirmou:

1. candidato exato e `candidate.json` por digest;
2. evidência M3 assinada e vinculada ao candidato;
3. aprovação protegida e ausência de blockers;
4. publicação GitHub e GitLab com mirrors convergentes;
5. metadata TUF e site implantados por último;
6. verificação pública pós-deploy.

A evidência assinada foi usada pela promoção, mas ainda precisa ser publicada
de forma durável como asset (`release-evidence.json`, `evidence-root.json` e
`release-receipt.json`) para que a prova não dependa da retenção de artifacts de
Actions.

## Gaps e gates restantes

1. o período de uso do RC está registrado em
   `docs/releases/1.0.0-rc.1-soak.md`, mas ainda precisa de diário, issue
   canônica e encerramento explícito;
2. a evidência M3 deste RC ainda depende da retenção de 90 dias dos artifacts até
   que os três assets duráveis sejam publicados;
3. a aceitação pública pós-deploy tem workflow e verificador implementados, mas
   ainda precisa de execução M3 e recibo anexado;
4. a migração real de uma instalação `0.7.13`, Frogbot e mutações reais de
   lifecycle estão implementados no candidato local, mas ainda precisam de run
   nativo do candidato exato;
5. a operação TUF tem monitor e drill offline implementados; ainda faltam
   custódia de produção, renovação observada, alerta, expiração simulada e
   recuperação registrados;
6. Linux, Windows e macOS Intel continuam `preview`; stable macOS continua
   `conditional` enquanto Gatekeeper, notarização e primeira abertura do bundle
   upstream original não forem comprovados.

## Veredito

O RC público é um marco legítimo e está em `GO` para uso operacional. A
promoção de `1.0.0` permanece `NO-GO` até que todos os gates acima tenham
evidência pública e o candidato final seja novo. Nenhum novo `0.7.x` deve ser
publicado salvo regressão crítica.

## Próxima ação

Manter o soak do RC, fechar a aceitação pública e as lacunas M3, publicar a
evidência durável e provar a operação TUF. Depois disso, congelar a linha,
gerar um novo candidato `1.0.0`, repetir todos os gates sobre seus bytes e só
então promover a versão final.
