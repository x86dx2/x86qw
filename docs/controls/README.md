# Mapas de controles x86QW

O mapa é uma página HTML autocontida e editável. Ele usa os binds gerenciados
dos cinco jogos atuais e evita duplicar a estrutura visual em cinco fontes
independentes.

Abra [`index.html`](index.html) e selecione o jogo e o teclado no topo. Os três
formatos físicos atuais são:

- Windows ANSI en-US, 104 teclas;
- macOS en-US, compacto Apple, com as combinações `fn` explícitas;
- Keychron K3 Version 3, ANSI en-US de 84 teclas, compacto slim 75%, em modo
  macOS.

Também é possível abrir uma combinação diretamente:

```text
index.html?profile=ktx&layout=windows-ansi
index.html?profile=ktx&layout=macos-en-us
index.html?profile=ktx&layout=keychron-k3-v3
```

## Organização

- **Base comum:** movimento, interface e fechamento do jogo.
- **Perfil por jogo:** armas e ações específicas de KTX, Final Arena, Pro-X,
  Team Fortress e Total Destruction 2.
- **KTX contextual:** as teclas `H`, `I`, `M`, `X` e `Z` variam por modo; `F10`
  imprime o significado vigente dentro do jogo.
- **Frogbots:** `Ins`, `Del`, `Home` e `End` são destacados apenas no perfil KTX
  e só funcionam quando a sessão foi iniciada com bots.
- **Legenda explícita:** cada bind destacado possui uma descrição individual;
  ranges como `1–0` não escondem funções diferentes. A página valida também que
  nenhuma tecla destacada ficou sem item correspondente na legenda.

O nome oficial do teclado é Keychron K3 Version 3; “Keychron Slim V3” descreve a
categoria, mas poderia ser confundido com o Keychron V3 TKL. O K3 Version 3 não
declara `Insert` no keymap QMK oficial. A variante
marca `Fn+Del → Insert` como remapeamento x86QW recomendado no Keychron Launcher,
necessário para adicionar Frogbots com o bind atual. Teclas ausentes em layouts
compactos são listadas no rodapé em vez de serem desenhadas como se existissem.

Referências dos layouts:

- [Apple: usar as teclas de função no Mac](https://support.apple.com/guide/mac-help/use-keyboard-function-keys-mchlp2596/mac);
- [Keychron K3 Version 3](https://www.keychron.com/products/keychron-k3-qmk-wireless-mechanical-keyboard-version-3);
- [keymap QMK oficial do Keychron K3 V3](https://github.com/Keychron/qmk_firmware/blob/wls_2025q1/keyboards/keychron/k3_version_3/ansi/rgb/keymaps/default/keymap.c).

Os 15 PNGs em `generated/` são projeções para documentação e divulgação. A fonte
canônica visual continua sendo `index.html`; ao alterar binds no produto, ajuste
os dados `profiles` na página e gere novamente as imagens.

## Gerar os PNGs no macOS

Com Google Chrome instalado:

```sh
./docs/controls/render.sh
```

O fundo original está em `assets/control-map-background.png`. Ele foi criado
especificamente para o x86QW e não contém textos nem controles; toda informação
funcional permanece em HTML/CSS para ser verificável.
