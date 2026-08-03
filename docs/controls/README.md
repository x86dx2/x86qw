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

O escopo visual é o plano de controles gerenciado pelo x86QW. Binds de reprodução
de demos herdados da configuração-base e personalizações do usuário não são
apresentados como se fizessem parte do perfil do mod.

O nome oficial do teclado é Keychron K3 Version 3; “Keychron Slim V3” descreve a
categoria, mas poderia ser confundido com o Keychron V3 TKL. O K3 Version 3 não
declara `Insert` no keymap QMK oficial. A variante
marca `Fn+Del → Insert` como remapeamento x86QW recomendado no Keychron Launcher,
necessário para adicionar Frogbots com o bind atual. Teclas ausentes em layouts
compactos são listadas no rodapé em vez de serem desenhadas como se existissem.
No Magic Keyboard, `Fn+Delete` representa `Del` (forward delete); `Insert`
continua ausente. No K3 V3, o `Del` dedicado existe, mas `Insert` só aparece
depois do remapeamento descrito acima.

A geometria usa a unidade física do teclado (`1u`) em vez de larguras visuais
aproximadas. O perfil macOS reproduz o Magic Keyboard USB-C compacto en-US de
`14.5u`, inferido da imagem frontal oficial da Apple, e não reaproveita a matriz
ANSI de `15u`. As cinco linhas do Keychron reproduzem as coordenadas do
`LAYOUT_ansi_84` oficial: `Shift` direito de `1.75u`, barra de espaço de `6.25u`
e seis teclas de `1u` à direita. Antes de gerar cada PNG, a página verifica
limites, sobreposição, alinhamento das setas, trilho de navegação e recorte das
legendas; uma geometria inválida interrompe `render.sh`.

Referências dos layouts:

- [Apple: usar as teclas de função no Mac](https://support.apple.com/guide/mac-help/use-keyboard-function-keys-mchlp2596/mac);
- [Apple Magic Keyboard USB-C, US English](https://www.apple.com/us-edu/shop/product/mxcl3ll/a/magic-keyboard-usb-c-us-english);
- [Keychron K3 Version 3](https://www.keychron.com/products/keychron-k3-qmk-wireless-mechanical-keyboard-version-3);
- [geometria QMK oficial do Keychron K3 V3](https://github.com/Keychron/qmk_firmware/blob/wls_2025q1/keyboards/keychron/k3_version_3/info.json).

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
