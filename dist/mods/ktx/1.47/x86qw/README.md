# Camada x86QW do KTX 1.47

Este diretório é uma fronteira de autoria e proveniência, não uma árvore que é
copiada integralmente para a instalação. Cada contexto possui uma função única:

- `catalog/`: modos e dados declarativos do launcher; os perfis Frogbot ficam
  isolados em `catalog/frogbots/`;
- `config/`: configuração do cliente, ajuda por teclas e presets projetados
  pelo inventário para `qw/`;
- `runtime/`: QVM e mapa de símbolos efetivamente empacotados;
- `source/`: patches numerados usados para reproduzir o runtime;
- `policy/`: regras verificáveis de composição da camada `ktx.pk3`;
- `CHANGELOG.md`: histórico da integração x86QW.

`maintenance/inventory/components.json` declara explicitamente quais arquivos
são instalados, quais entram no PK3 e quais existem apenas para build ou
validação. Nenhuma dessas subpastas é criada no diretório do jogador.
