# Team Fortress 2.9 no x86QW

## Original reconstruído

- `upstream/tf28.zip`: cliente completo oficial 2.8, 4204316 bytes, SHA-256 `56a6767b7944f2ce54423f18f1cd59239ae37a7c7456f684227d6f5310c76291`;
- `upstream/tf29qw.zip`: atualização oficial QuakeWorld 2.9, somente servidor;
- `source/tf_29src.zip`: fonte oficial 2.9;
- os assets e mapas úteis selecionados pelo nQuake continuam como base da distribuição.

## Alterações x86QW

- `source/0001-preserve-client-controls.patch` remove somente os 20 comandos de bind remoto do MOTD e do menu de bindings;
- `runtime/qwprogs.dat` é o candidato recompilado com FTEQCC; ele não deve ser promovido sem smoke stable/nightly;
- os binds coerentes ficam no perfil local x86QW e permanecem sob controle do jogador;
- `runtime/sound/weapons/bounce2.wav` restaura o som diretamente referenciado em `TRIGGERS.QC`, usando o arquivo oficial presente no KTX 1.47: 7960 bytes, SHA-256 `d2da688ec5fc64f24b798b645eb80b8cdd60c6e7d44cb68a7e05e2264e1e69d5`.

## Lacunas conhecidas

- `clan1.wav` a `clan5.wav` só são chamados quando o administrador ativa `localinfo clanmsgs on`; não foram encontrados no cliente 2.8, no upgrade 2.9 nem em arquivo oficial verificável;
- o build histórico produz 187 warnings com FTEQCC e precisa de comparação de comportamento antes da promoção.

## Deliberadamente não alterado

- execuções e screenshot opt-in do mod não foram removidos porque não ocorrem automaticamente;
- nenhum som substituto, classe, arma, mapa ou regra foi inventado.
