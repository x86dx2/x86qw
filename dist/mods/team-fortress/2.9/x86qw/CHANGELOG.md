# Team Fortress 2.9 no x86QW

## Original reconstruído

- `upstream/tf28.zip`: cliente completo oficial 2.8, 4204316 bytes, SHA-256 `56a6767b7944f2ce54423f18f1cd59239ae37a7c7456f684227d6f5310c76291`;
- `upstream/tf29qw.zip`: atualização oficial QuakeWorld 2.9, somente servidor;
- `source/tf_29src.zip`: fonte pública preservada da versão 2.9, byte a byte
  idêntica à cópia do Internet Archive; a origem da primeira publicação não
  pôde ser comprovada e, por isso, não é rotulada como release oficial;
- os assets e mapas úteis selecionados pelo nQuake continuam como base da distribuição.

## Alterações x86QW

- `source/0001-preserve-client-controls.patch` remove somente os 20 comandos de bind remoto do MOTD e do menu de bindings;
- `runtime/qwprogs.dat` foi recompilado com FTEQCC e promovido após smoke em stable e nightly;
- os binds coerentes ficam no perfil local x86QW e permanecem sob controle do jogador;
- compõe `cl_remote_capabilities` sem substituir a lista segura do cliente e
  acrescenta somente as cvars legadas necessárias ao TF;
- remove automaticamente de configurações antigas o valor literal inválido
  `$cl_remote_capabilities,...` produzido pelo perfil anterior;
- inicia o diretório do mod uma única vez com `-game` e publica `*gamedir`
  separadamente, sem recarregar o filesystem durante o startup;
- deixa o console limpo ao iniciar e mantém a ajuda em `F10` somente sob demanda;
- `runtime/sound/weapons/bounce2.wav` restaura o som diretamente referenciado em `TRIGGERS.QC`, usando o arquivo oficial presente no KTX 1.47: 7960 bytes, SHA-256 `d2da688ec5fc64f24b798b645eb80b8cdd60c6e7d44cb68a7e05e2264e1e69d5`.

## Lacunas conhecidas

- `clan1.wav` a `clan5.wav` só são chamados quando o administrador ativa `localinfo clanmsgs on`; não foram encontrados no cliente 2.8, no upgrade 2.9 nem em arquivo oficial verificável;
- o build histórico produz 187 warnings com FTEQCC; a matriz de runtime passou,
  mas a classificação individual permanece registrada no roadmap para uma
  futura redução sem modernização ampla.

## Validação de runtime

- 30/07/2026: ezQuake 3.6.9 e nightly `20260616-101233_a86996a` em `2fort5r`;
- gamecode PR1 carregado, jogador entrou e `TeamFortress QuakeWorld v2.9` foi confirmado;
- nenhum bind remoto, comando bloqueado, arquivo obrigatório ausente ou erro do mod.

## Correções encaminhadas ao cliente

- o falso erro de QVM antes do fallback PR1 foi enviado ao ezQuake no
  [PR #1149](https://github.com/QW-Group/ezquake-source/pull/1149);
- o ruído de registros duplicados após `vid_restart` foi enviado separadamente
  no [PR #1150](https://github.com/QW-Group/ezquake-source/pull/1150).

## Deliberadamente não alterado

- execuções e screenshot opt-in do mod não foram removidos porque não ocorrem automaticamente;
- nenhum som substituto, classe, arma, mapa ou regra foi inventado.
