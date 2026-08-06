# Referência legada — evidência nativa

Este documento preserva o formato e os validadores de compatibilidade para
`handoff.json`, mas não descreve um passo operacional deste checkout. Não há
workflow ativo de `native-smoke` ou `signed-evidence`, nem exigência de
`native-release`, `release-evidence` ou runners externos.

## Fluxo atual

O fluxo atual é validado no Mac com `release_candidate.py verify` e não cria
`release-evidence.json`. A presença de `Linux-X64`, `Windows-X64`,
`macOS-ARM64` e `macOS-X64` nos contratos/catalogos preserva compatibilidade e
não afirma que essas plataformas foram executadas ou validadas neste snapshot.

## Compatibilidade preservada

As ferramentas `native_release_smoke.py`, `native_release_evidence.py`, os
schemas de `x86qw_runtime/contracts/native_evidence.py` e seus testes continuam
no repositório para leitura de formatos legados ou uma futura decisão explícita.
Um `release-evidence.json` fornecido manualmente continua sujeito à validação
estrutural/criptográfica correspondente; ele não é criado implicitamente nem
transforma um candidato Mac em evidência multiplataforma.

A cobertura histórica reconhecida pelos validadores permanece exatamente
`Linux-X64`, `macOS-ARM64` e `Windows-X64`, com `macOS-X64` condicional. Esses
nomes não são uma definição de pronto da release atual.

## Limites de verdade

- teste portável no Mac não é smoke nativo de Linux, Windows ou macOS Intel;
- fixtures e relatórios legados não são apresentados como evidência atual;
- trust de catálogo (`root`, `current`, `snapshot`) continua separado da
  compatibilidade opcional de evidência;
- nenhum runner externo é necessário para preparar, verificar ou promover um
  candidato local neste checkout.
