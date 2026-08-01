# Layout de bootstrap limpo

O layout atual assume uma instalação criada do zero. Árvores anteriores podem
ser renomeadas e mantidas fora do destino apenas como referência; não existe
conversão automática.

## Contrato

- `.x86qw/`: CLI, estado, recibos, inventários, lock e journals;
- `mvdsv` ou `mvdsv.exe`: servidor dedicado no basedir QuakeWorld;
- `qtv/`: runtime, recursos, configuração e dados operacionais do QTV;
- `qwfwd/`: runtime e configuração do QWFWD;
- `docs/licenses/`: licenças distribuídas com os serviços.

Os pacotes de MVDSV, QTV e QWFWD continuam reproduzíveis e podem transportar
artefatos para os três sistemas. Antes do overlay, o instalador seleciona a
variante declarada para o destino e elimina o staging das demais. Assim, uma
instalação macOS arm64 não recebe ELF ou PE; Linux amd64 não recebe Mach-O ou
PE; Windows x64 não recebe Mach-O ou ELF.

`BUILD.json`, fontes e patches são evidência de manutenção. Eles participam da
validação do componente, mas não são payload do jogador.
