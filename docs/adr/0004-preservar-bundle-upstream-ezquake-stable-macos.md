# ADR 0004 — Preservar o bundle upstream do ezQuake stable no macOS

- **Estado:** aceita no código corretivo da PR 5; não publicada
- **Data:** 2026-08-03
- **Issue:** [#50](https://github.com/x86dx2/x86qw/issues/50)
- **Escopo:** ezQuake stable `3.6.9`, variante `macos-universal`

## Contexto

Até a release pública `0.7.1`, instalação, atualização e reparo do cliente
stable alteravam `Contents/Info.plist` e executavam
`codesign --force --deep --sign -`. Essa transformação removia o App Sandbox e
o hardened runtime do bundle recebido, criava uma nova assinatura ad hoc local
e fazia o recibo registrar o executável modificado.

Uma assinatura ad hoc comprova apenas a consistência do conteúdo que foi
assinado localmente; ela não autentica o publicador. A Apple exige Developer ID,
hardened runtime e outros requisitos para o fluxo normal de notarização e
explica que alterar um bundle depois de assinado invalida sua assinatura:

- [Notarizing macOS software before distribution](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution);
- [Resolving common notarization issues](https://developer.apple.com/documentation/security/resolving-common-notarization-issues);
- [Technical Note TN2206: macOS Code Signing In Depth](https://developer.apple.com/library/archive/technotes/tn2206/_index.html).

## Evidência do artefato fixado

O ZIP oficial preservado em
`dist/clients/ezquake/stable/3.6.9/macos-universal/` tem SHA-256
`2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed`.
Sua inspeção no macOS produziu:

| Evidência | Upstream stable 3.6.9 | Instalação transformada pela 0.7.1 |
|---|---|---|
| Executável | `14633b5d4201e9460250ad236fde2e4ad579a6ddbaf81301830099d8cf004f33` | `e24524761d8ff10c57a8ecbb2fdc7ce29d1bd78641cfaecf49644d8881e2422a` |
| `Info.plist` | `0600a1a09231ec9168fba74821af9c3ba8b6d1d97165c2ac566b4438c60deaf7` | `26988ec42a27b6a4427c13feca5df9ede8606c9dcd1529a89398334189679d34` |
| `CodeResources` | `a696d8b764e4c61aa6269b1dd931dab4e408786aa6c1fc25890dd56c942fd87d` | mesmo hash |
| Arquiteturas | `arm64` e `x86_64` | `arm64` e `x86_64` |
| Assinatura | ad hoc | ad hoc refeita localmente |
| Hardened runtime | presente | removido |
| App Sandbox | presente | removido |
| Team Identifier | ausente | ausente |
| Ticket stapled | ausente | ausente |
| `spctl` | rejeitado | rejeitado |
| `NSPrefersDisplaySafeAreaCompatibilityMode` | ausente | `false` |

`codesign --verify` confirma somente a integridade interna da assinatura
observada. A ausência de Team ID, ticket e aceitação por `spctl` impede afirmar
Developer ID ou notarização.

Os valores da tabela foram obtidos sem alterar os bundles. Para repetir a
inspeção, defina `UPSTREAM_APP` para o `ezQuake.app` extraído do ZIP fixado e
`LEGACY_APP` para o `ezQuake Stable.app` instalado pela 0.7.1, então execute:

```sh
shasum -a 256 \
  "$UPSTREAM_APP/Contents/MacOS/ezQuake" \
  "$UPSTREAM_APP/Contents/Info.plist" \
  "$UPSTREAM_APP/Contents/_CodeSignature/CodeResources" \
  "$LEGACY_APP/Contents/MacOS/ezQuake" \
  "$LEGACY_APP/Contents/Info.plist" \
  "$LEGACY_APP/Contents/_CodeSignature/CodeResources"
codesign -d --verbose=4 --entitlements :- "$UPSTREAM_APP"
codesign -d --verbose=4 --entitlements :- "$LEGACY_APP"
xcrun stapler validate "$UPSTREAM_APP"
spctl --assess --type execute --verbose=4 "$UPSTREAM_APP"
```

Os dois últimos comandos retornam falha para o artefato observado; ela é
evidência do estado do bundle, não uma instrução para contornar o macOS.

## Decisão

O x86QW preserva integralmente os arquivos do bundle upstream no caminho
stable:

- não altera o `Info.plist`;
- não remove sandbox ou entitlements;
- não executa `codesign --sign`, `--force` ou `--deep` para assinar;
- não remove quarentena nem contorna o Gatekeeper;
- pode executar `codesign --verify --deep --strict` no macOS para validar a
  consistência do bundle recebido;
- mantém versão, canal, nome instalado e recibo separados do nightly.

A rotina de transformação local passa a aceitar somente o canal nightly. Essa
exceção preexistente fica fora da decisão de confiança do stable e permanece
explicitamente condicional até trabalho próprio.

O suporte dos clientes macOS stable e nightly é projetado no candidato como
`conditional`. Disponibilidade do artefato universal não equivale a Developer
ID, notarização ou smoke nativo completo.

## Migração de instalações 0.7.1

O bundle stable transformado é reconhecido somente quando coexistem:

1. recibo válido do artefato stable 3.6.9 fixado;
2. hash exato do executável transformado pela linha 0.7.1;
3. chave de área segura inserida pela transformação antiga;
4. no macOS, ausência confirmada do sandbox.

Um artifact desconhecido, executável upstream, executável modificado com hash
não reconhecido, recibo ausente ou identidade inconclusiva nunca é substituído
por inferência. Quando a transformação antiga é comprovada, `update --dry-run`
mostra a restauração; o bootstrap obtém a mesma versão registrada, valida
tamanho e SHA-256, prepara o bundle sem mutá-lo e faz a troca transacional de
runtime e recibo. Falha de commit restaura ambos.

A CLI instalada não baixa payload arbitrariamente: `repair` diagnostica e
orienta reexecutar o bootstrap no mesmo destino. Configurações pessoais, PAKs,
demos, logs, canal nightly e versões mais novas são preservados. Update e
repair não apagam o bookmark de escopo de segurança já escolhido; a instalação
inicial pode limpar uma seleção antiga para obrigar a confirmação do novo
destino.

## Alternativas rejeitadas

1. **Continuar a assinatura ad hoc local:** mantém a perda da identidade
   verificável do artefato e remove proteções upstream.
2. **Remover a quarentena:** contorna uma proteção do sistema e não cria
   identidade de publicador.
3. **Publicar agora um bundle x86QW ad hoc:** troca uma identidade não
   autenticada por outra e não resolve Gatekeeper.
4. **Adotar Developer ID sem pipeline e governança de chave:** exigiria
   hardened runtime, notarização, stapling, custódia e resposta a incidente;
   essa opção precisa de ADR e evidência próprios.

## Consequências e riscos residuais

Preservar o stable impede que o x86QW degrade o bundle recebido, mas não
transforma o upstream em software identificado ou notarizado pela Apple. O
Gatekeeper pode exigir aprovação explícita do usuário.

O sandbox upstream pode pedir a pasta `quake-world` na primeira abertura e
reutilizar um bookmark de escopo de segurança nas aberturas seguintes, conforme
o contrato documentado pela Apple em
[Accessing files from the macOS App Sandbox](https://developer.apple.com/documentation/security/accessing-files-from-the-macos-app-sandbox).
A primeira e a segunda abertura, janela/fullscreen, painel com notch, arm64 e
Intel precisam ser executados com o candidato imutável no PR 11. Até lá, o
suporte permanece condicional.

A release `0.7.1`, seus hashes, tag, bundles e ponteiros públicos permanecem
imutáveis. Esta decisão não autoriza publicação.
